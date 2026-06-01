#!/usr/bin/env python3
"""
Claude Session Ring — macOS menu bar app.
Shows Claude token usage (rolling 5h window) as a white brushstroke ring icon.

Session-anchor model:
  We maintain a persisted session anchor (start_ts + tokens at start) and derive
  current 5h-window usage from it. The anchor is refreshed automatically when
  Claude's real 5h window expires (next token increase after >=5h marks a new
  session). See plan file for full edge-case behaviour.
"""
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import rumps
import objc
from AppKit import (
    NSMenuItem, NSSlider, NSView, NSTextField, NSFont, NSColor, NSEvent,
    NSBezierPath, NSRectFill, NSMenu, NSFontAttributeName,
)
from Foundation import NSMakeRect, NSMakePoint, NSString, NSObject

try:
    import zstandard
except Exception:
    zstandard = None

try:
    from Crypto.Cipher import AES
except Exception:
    AES = None

try:
    from curl_cffi import requests as cffi_requests
except Exception:
    cffi_requests = None

from ring_renderer import render_ring

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_LIMIT    = 197_000
CONFIG_PATH      = Path.home() / ".claude_token_ring_config.json"
BUDDY_TOKENS     = Path.home() / "Library" / "Application Support" / "Claude" / "buddy-tokens.json"
CHROME_CACHE_DIR = Path.home() / "Library" / "Application Support" / "Claude" / "Cache" / "Cache_Data"
COOKIES_DB       = Path.home() / "Library" / "Application Support" / "Claude" / "Cookies"
USAGE_URL_RE     = re.compile(rb"/api/organizations/[a-f0-9-]+/usage[^/_]")
ORG_UUID_RE      = re.compile(rb"/api/organizations/([a-f0-9-]{36})/")
ZSTD_MAGIC       = b"\x28\xb5\x2f\xfd"
LIVE_MAX_AGE     = 10 * 60        # treat live cache as fresh if <10min old
CACHE_FRESH_SEC  = 60             # cache is "fresh enough" — skip API call
KEYCHAIN_SERVICE = "Claude Safe Storage"
KEYCHAIN_ACCOUNT = "Claude Key"

USAGE_LOG        = Path.home() / ".claude_token_ring_usage_log.jsonl"
RATE_WINDOW_SEC  = 30 * 60        # consumption rate computed over the last 30 min
RATE_LOG_MAX     = 17280          # ~6 months of history at 30s ticks (≈ 1.4 MB)
RATE_RESET_DROP  = 15.0           # a utilization drop > this % marks a 5h reset
RATE_MIN_SPAN    = 180            # need >= 3 min of samples for a meaningful rate
SESSION_STATE    = Path.home() / ".claude_token_ring_session.json"
CODE_PROJECTS    = Path.home() / ".claude" / "projects"
ICON_DIR         = Path("/tmp/claude_ring_icons")
UPDATE_INTERVAL  = 30
WINDOW_SEC       = 5 * 3600
MIDNIGHT_DROP    = 1000   # threshold for detecting tokens-today midnight reset
CODE_SCAN_LIMIT  = 40     # max number of recent JSONL files to scan for hint

CONFIDENCE_LABEL = {
    "high":   "hoch",
    "medium": "mittel",
    "low":    "niedrig",
}

DEFAULTS = {"limit": DEFAULT_LIMIT, "show_pct": True}

# ── Icon cache (pre-rendered 0..100%) ─────────────────────────────────────────

def _is_claude_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-x", "Claude"],
            capture_output=True, timeout=2
        ).returncode == 0
    except Exception:
        return True


def _icon_path(pct_int: int) -> str:
    return str(ICON_DIR / f"r{pct_int:03d}.png")


def _get_or_render_icon(pct_int: int) -> str:
    """Return path to the pre-rendered ring icon, generating on demand."""
    pct_int = max(0, min(100, int(pct_int)))
    path    = _icon_path(pct_int)
    if not Path(path).exists():
        try:
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            img = render_ring(pct_int / 100.0, size=44, dark_mode=True)
            img.save(path)
        except Exception:
            pass
    return path


def _prerender_icons():
    """Render all 101 icons synchronously. Called in a background thread."""
    try:
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        for p in range(101):
            path = _icon_path(p)
            if not Path(path).exists():
                img = render_ring(p / 100.0, size=44, dark_mode=True)
                img.save(path)
    except Exception:
        pass


# ── Live usage from Claude Desktop's HTTP cache ───────────────────────────────
#
# Claude Desktop is an Electron wrapper around claude.ai. The renderer
# periodically calls GET /api/organizations/<org>/usage, and the Chromium
# disk cache stores the response under Cache/Cache_Data/<hash>_0.
#
# Layout of a cache entry:
#   • First ~1 KB: ASCII metadata containing the request URL + HTTP response
#     headers (e.g. "content-encoding:zstd").
#   • Then the response body, encoded according to content-encoding. For the
#     /usage endpoint it is zstandard-compressed JSON.
#
# We locate the entry by URL pattern, find the zstd magic bytes (0x28b52ffd),
# decompress, and parse JSON. The response shape:
#   {"five_hour": {"utilization": 92.0, "resets_at": "<ISO8601>"}, ...}
#
# utilization is 0..100 and matches Claude's own UI display exactly.

def _find_latest_usage_cache():
    """Most recent /api/organizations/<uuid>/usage cache entry, or None."""
    if not CHROME_CACHE_DIR.exists():
        return None
    best, best_mtime = None, 0.0
    try:
        for fp in CHROME_CACHE_DIR.glob("*_0"):
            try:
                head = fp.open("rb").read(1024)
            except Exception:
                continue
            if not USAGE_URL_RE.search(head):
                continue
            m = fp.stat().st_mtime
            if m > best_mtime:
                best_mtime, best = m, fp
    except Exception:
        return None
    return best


def _read_live_usage():
    """
    Parse Claude's own /usage response from disk cache.
    Returns dict {"utilization": float, "resets_at_ts": float, "cache_mtime": float}
    or None on failure.
    """
    if zstandard is None:
        return None
    fp = _find_latest_usage_cache()
    if fp is None:
        return None
    try:
        data = fp.read_bytes()
        idx  = data.find(ZSTD_MAGIC)
        if idx < 0:
            return None
        dctx = zstandard.ZstdDecompressor()
        try:
            body = dctx.decompress(data[idx:], max_output_size=4 * 1024 * 1024)
        except Exception:
            body = dctx.stream_reader(data[idx:]).read()
        obj = json.loads(body.decode("utf-8", errors="replace"))
        return _shape_usage(obj, cache_mtime=fp.stat().st_mtime, source="cache")
    except Exception:
        return None


def _shape_usage(obj: dict, cache_mtime: float, source: str):
    """Normalise a /usage JSON response into our internal dict. None if no five_hour."""
    fh = obj.get("five_hour") or {}
    util = fh.get("utilization")
    if util is None:
        return None
    sd = obj.get("seven_day") or {}
    sd_util = sd.get("utilization")
    fh_iso  = fh.get("resets_at")
    sd_iso  = sd.get("resets_at")
    return {
        "utilization":      float(util),
        "resets_at_ts":     _iso_to_ts(fh_iso) if fh_iso else None,
        "sd_utilization":   float(sd_util) if sd_util is not None else None,
        "sd_resets_at_ts":  _iso_to_ts(sd_iso) if sd_iso else None,
        "cache_mtime":      cache_mtime,
        "source":           source,
    }


# ── Direct API call (Chromium safe-storage cookies + Chrome TLS) ──────────────
#
# When Claude Desktop hasn't refreshed its /usage cache recently (which happens
# during normal chat use — only Code/Plan UI triggers a refresh), we can call
# the same endpoint ourselves using Claude Desktop's session cookies. This
# requires:
#
#   1. Decrypt cookies from Cookies SQLite via macOS Keychain (Chromium Safe
#      Storage scheme: AES-128-CBC, PBKDF2-HMAC-SHA1 key derivation).
#   2. Make the HTTPS call with Chrome's TLS fingerprint (Cloudflare's bot
#      manager blocks Python's default TLS) — provided by curl_cffi.

_org_uuid_cached      = None
_safe_storage_key     = None
_cookies_cache        = None
_cookies_cache_mtime  = 0.0
_LOCK_FD              = None   # held open for the lifetime of the process


def _acquire_single_instance() -> bool:
    """Return True if this is the only running instance (exclusive fcntl lock)."""
    global _LOCK_FD
    try:
        _LOCK_FD = open("/tmp/claude-token-ring.lock", "w")
        fcntl.flock(_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False   # another instance already holds the lock


def _get_org_uuid():
    """Cache the org UUID — extracted from any /api/organizations/<uuid>/... cache file."""
    global _org_uuid_cached
    if _org_uuid_cached:
        return _org_uuid_cached
    if not CHROME_CACHE_DIR.exists():
        return None
    try:
        for fp in CHROME_CACHE_DIR.glob("*_0"):
            try:
                head = fp.open("rb").read(1024)
            except Exception:
                continue
            m = ORG_UUID_RE.search(head)
            if m:
                _org_uuid_cached = m.group(1).decode("ascii")
                return _org_uuid_cached
    except Exception:
        return None
    return None


_KEY_CACHE_PATH = Path("/tmp/claude_ring_ssk")

def _get_safe_storage_key():
    """Derive AES key from macOS Keychain password.

    Cached in RAM after first read. Also persisted to /tmp/claude_ring_ssk so
    that process restarts (triggered by LaunchAgent WatchPaths) do not cause a
    new Keychain prompt — the derived key is read from disk instead.

    /tmp is user-private and cleared on reboot, so the window of exposure is
    short. The value stored is already the derived PBKDF2 key, not the raw
    Keychain password.
    """
    global _safe_storage_key
    if _safe_storage_key is not None:
        return _safe_storage_key
    # Try disk cache first — survives process restarts within the same login session.
    try:
        _safe_storage_key = _KEY_CACHE_PATH.read_bytes()
        if len(_safe_storage_key) == 16:
            return _safe_storage_key
        _safe_storage_key = None  # corrupt/wrong length, fall through
    except Exception:
        pass
    # Fall back to Keychain (triggers macOS prompt on first use).
    try:
        pw = subprocess.check_output(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return None
    _safe_storage_key = hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, 16)
    # Persist to disk so future process restarts skip the Keychain prompt.
    try:
        _KEY_CACHE_PATH.write_bytes(_safe_storage_key)
        _KEY_CACHE_PATH.chmod(0o600)  # owner-read/write only
    except Exception:
        pass
    return _safe_storage_key


def _decrypt_chrome_cookie(encrypted_value: bytes, key: bytes):
    """AES-128-CBC + strip 32-byte SHA256 host prefix + PKCS7 unpad."""
    if AES is None or not encrypted_value or not encrypted_value.startswith(b"v10"):
        return None
    try:
        pt = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(encrypted_value[3:])
        pad = pt[-1]
        if pad < 1 or pad > 16:
            return None
        pt = pt[:-pad]
        # macOS Chromium prepends a 32-byte SHA256("host\x00<host_key>") to the value
        if len(pt) > 32:
            return pt[32:].decode("utf-8", errors="replace")
        return pt.decode("utf-8", errors="replace")
    except Exception:
        return None


def _read_claude_session_cookies():
    """Returns dict of all decrypted claude.ai cookies. Caches by mtime."""
    global _cookies_cache, _cookies_cache_mtime
    try:
        mtime = COOKIES_DB.stat().st_mtime
    except Exception:
        return None
    if _cookies_cache is not None and mtime == _cookies_cache_mtime:
        return _cookies_cache

    key = _get_safe_storage_key()
    if key is None:
        return None
    try:
        snap = Path("/tmp/claude_cookies_snap.db")
        shutil.copy(COOKIES_DB, snap)
        conn = sqlite3.connect(snap)
        rows = conn.execute(
            "SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%claude.ai%'"
        ).fetchall()
        conn.close()
    except Exception:
        return None

    out = {}
    for name, enc in rows:
        v = _decrypt_chrome_cookie(enc, key)
        if v:
            out[name] = v
    _cookies_cache       = out
    _cookies_cache_mtime = mtime
    return out


def _fetch_usage_via_api():
    """
    GET /api/organizations/<org>/usage with Chrome TLS impersonation.
    Returns dict shaped like _read_live_usage's output, or None on any failure.
    """
    if cffi_requests is None:
        return None
    org = _get_org_uuid()
    if not org:
        return None
    cookies = _read_claude_session_cookies()
    if not cookies or "sessionKey" not in cookies:
        return None
    url = f"https://claude.ai/api/organizations/{org}/usage"
    try:
        r = cffi_requests.get(url, cookies=cookies, impersonate="chrome", timeout=10)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        obj = r.json()
    except Exception:
        return None
    # cache_mtime = now: we just fetched, so it's maximally fresh
    return _shape_usage(obj, cache_mtime=time.time(), source="api")


# ── Token reading ─────────────────────────────────────────────────────────────

def _read_tokens_now() -> int:
    """Current tokens-today from buddy-tokens.json (server-synced, all devices)."""
    try:
        data  = json.loads(BUDDY_TOKENS.read_text())
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = data.get("tokens-today", {})
        if entry.get("date") == today:
            return int(entry.get("tokens", 0))
        # Date mismatch (yesterday's snapshot still there) → treat as 0
        return 0
    except Exception:
        return 0


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state():
    """Returns dict or empty dict if no state yet."""
    try:
        return json.loads(SESSION_STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        SESSION_STATE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


# ── Claude Code session-start hint ────────────────────────────────────────────

def _iso_to_ts(iso: str):
    """Parse ISO 8601 (with optional 'Z') to unix timestamp; None on failure."""
    try:
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


def _format_eta(secs: int) -> str:
    """Human-readable countdown. Days for the 7-day window, h/m otherwise."""
    if secs <= 0:
        return "gleich"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m      = rem // 60
    if d:
        return f"in {d}d {h}h"
    if h:
        return f"in {h}h {m:02d}m"
    return f"in {m}m"


# ── Usage-rate tracking ───────────────────────────────────────────────────────
#
# Anthropic exposes only a 0..100 utilization %, no token counts. To make the
# "it fills up faster and faster" feeling measurable, we log utilization over
# time and derive a consumption rate (%/h) plus a projection to 100%.

def _append_usage_log(util: float):
    """Append one {ts, util} sample, prune to RATE_LOG_MAX lines."""
    entry = json.dumps({"ts": int(time.time()), "util": round(float(util), 2)}) + "\n"
    try:
        lines = USAGE_LOG.read_text().splitlines(keepends=True) if USAGE_LOG.exists() else []
        lines.append(entry)
        if len(lines) > RATE_LOG_MAX:
            lines = lines[-RATE_LOG_MAX:]
        USAGE_LOG.write_text("".join(lines))
    except Exception:
        pass


def _read_usage_log():
    """Returns chronologically sorted list of {"ts": int, "util": float}."""
    try:
        out = []
        for line in USAGE_LOG.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                out.append({"ts": int(obj["ts"]), "util": float(obj["util"])})
            except Exception:
                continue
        out.sort(key=lambda s: s["ts"])
        return out
    except Exception:
        return []


def _usage_rate(samples, now):
    """
    Consumption rate in %/h over the last RATE_WINDOW_SEC.

    Returns (rate_per_hour, anchor_ts) or (None, None) if there's not enough
    history. A utilization drop > RATE_RESET_DROP marks a 5h reset — only
    samples after the most recent reset are used.
    """
    if not samples or len(samples) < 2:
        return None, None
    cutoff = now - RATE_WINDOW_SEC
    window = [s for s in samples if s["ts"] >= cutoff]
    if len(window) < 2:
        return None, None
    # Restart the window after the most recent reset (sharp downward drop).
    start = 0
    for i in range(1, len(window)):
        if window[i]["util"] < window[i - 1]["util"] - RATE_RESET_DROP:
            start = i
    window = window[start:]
    if len(window) < 2:
        return None, None
    first, last = window[0], window[-1]
    dt = last["ts"] - first["ts"]
    if dt < RATE_MIN_SPAN:
        return None, None
    rate = (last["util"] - first["util"]) / dt * 3600.0
    return rate, first["ts"]


def _find_code_session_hint(now: float):
    """
    Scan ~/.claude/projects/**/*.jsonl for the earliest message timestamp
    within the last WINDOW_SEC. Returns unix ts or None.
    """
    cutoff = now - WINDOW_SEC
    earliest = None
    try:
        files = sorted(
            glob.glob(str(CODE_PROJECTS / "**" / "*.jsonl"), recursive=True),
            key=lambda p: Path(p).stat().st_mtime,
            reverse=True,
        )[:CODE_SCAN_LIMIT]
    except Exception:
        return None

    for fp in files:
        try:
            mtime = Path(fp).stat().st_mtime
            if mtime < cutoff:
                continue
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str:
                        continue
                    ts = _iso_to_ts(ts_str)
                    if ts is None or ts < cutoff:
                        continue
                    if earliest is None or ts < earliest:
                        earliest = ts
        except Exception:
            continue
    return earliest


# ── Session update logic ──────────────────────────────────────────────────────

def _update_session(now: float, tokens_now: int, state: dict) -> dict:
    """
    Apply the four-phase update:
      1. Detect midnight reset (tokens-today fell sharply)
      2. Detect session expiry / new session on activity
      3. Bootstrap if no valid anchor
      4. Always refresh last_seen_*

    Returns the new state dict.
    """
    s = dict(state) if state else {}

    anchor_ts       = s.get("session_start_ts")
    anchor_tokens   = s.get("session_start_tokens")
    last_seen_ts    = s.get("last_seen_ts")
    last_tokens     = s.get("last_seen_tokens")
    carryover       = s.get("midnight_carryover", 0)
    confidence      = s.get("confidence", "low")

    # ── Phase 1: midnight reset ───────────────────────────────────────────────
    if (
        last_tokens is not None
        and anchor_ts is not None
        and anchor_tokens is not None
        and tokens_now < last_tokens - MIDNIGHT_DROP
    ):
        # Tokens-today dropped sharply → midnight crossed (or manual reset).
        carryover += max(0, last_tokens - anchor_tokens)
        anchor_tokens = 0

    # ── Phase 2: session expiry ───────────────────────────────────────────────
    if anchor_ts is not None and (now - anchor_ts) >= WINDOW_SEC:
        if last_tokens is not None and tokens_now > last_tokens:
            # New activity post-expiry → start a new session
            anchor_ts     = now
            anchor_tokens = tokens_now
            carryover     = 0
            confidence    = "high"
        else:
            # Expired but quiet → drop anchor, wait for activity
            anchor_ts     = None
            anchor_tokens = None
            carryover     = 0

    # ── Phase 3: bootstrap ────────────────────────────────────────────────────
    if anchor_ts is None:
        if last_tokens is None:
            # Genuine cold start: try Claude Code hint
            hint = _find_code_session_hint(now)
            if hint is not None:
                anchor_ts     = hint
                anchor_tokens = 0
                confidence    = "medium"
            else:
                anchor_ts     = now
                anchor_tokens = tokens_now
                confidence    = "low"
            carryover = 0
        else:
            # Anchor expired in Phase 2 and no activity yet — only create new
            # anchor when tokens increase.
            if tokens_now > last_tokens:
                anchor_ts     = now
                anchor_tokens = last_tokens   # baseline = pre-jump value
                carryover     = 0
                confidence    = "high"

    # ── Phase 4: refresh observation ──────────────────────────────────────────
    s["session_start_ts"]     = anchor_ts
    s["session_start_tokens"] = anchor_tokens
    s["last_seen_ts"]         = now
    s["last_seen_tokens"]     = tokens_now
    s["midnight_carryover"]   = carryover
    s["confidence"]           = confidence
    return s


def _session_usage(state: dict, tokens_now: int) -> int:
    """Compute current session token usage from state."""
    if state.get("session_start_ts") is None or state.get("session_start_tokens") is None:
        return 0
    return state.get("midnight_carryover", 0) + max(0, tokens_now - state["session_start_tokens"])


# ── Slider handler ────────────────────────────────────────────────────────────

class _SliderHandler(NSObject):
    """Receives NSSlider action and forwards to the app's calibration logic."""

    def initWithApp_(self, app):
        self = objc.super(_SliderHandler, self).init()
        if self is None:
            return None
        self._app   = app
        self._label = None
        return self

    def sliderChanged_(self, sender):
        val = int(sender.intValue())
        if self._label is not None:
            self._label.setStringValue_(f"→ {val:3d}% (Claude-Anzeige hier matchen)")
        # If the mouse is no longer down, this is the final value after the
        # user released the thumb → safe to do a full menu refresh (title etc).
        try:
            mouse_down = NSEvent.pressedMouseButtons() != 0
        except Exception:
            mouse_down = True  # err on the side of "still dragging"
        self._app._calibrate_from_slider(val, finalize=not mouse_down)


# ── Usage chart ───────────────────────────────────────────────────────────────

class _ChartView(NSView):
    """NSView that draws a % utilisation-over-time graph for the 5h window."""

    def initWithFrame_(self, frame):
        self = objc.super(_ChartView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._samples          = []
        self._session_start_ts = None
        self._reset_at_ts      = None
        self._rate             = None   # %/h, None if unknown
        return self

    def drawRect_(self, rect):
        W, H       = 300, 120
        L, R, T, B = 38, 12, 10, 24   # margins left/right/top/bottom
        cw         = W - L - R         # chart width  = 250
        ch         = H - T - B         # chart height = 86

        NSColor.windowBackgroundColor().set()
        NSRectFill(NSMakeRect(0, 0, W, H))

        def px(h):
            return L + (h / 5.0) * cw

        def py(p):
            return B + (p / 100.0) * ch

        # Horizontal grid lines
        for pct in [25, 50, 75, 100]:
            if pct == 100:
                NSColor.systemRedColor().set()
                lw = 1.0
            else:
                NSColor.separatorColor().set()
                lw = 0.5
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(lw)
            path.moveToPoint_(NSMakePoint(px(0), py(pct)))
            path.lineToPoint_(NSMakePoint(px(5), py(pct)))
            path.stroke()

        # Y-axis labels
        attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(9)}
        for pct in [50, 100]:
            NSString.stringWithString_(f"{pct}%").drawAtPoint_withAttributes_(
                NSMakePoint(2, py(pct) - 5), attrs
            )

        # X-axis tick marks and hour labels
        NSColor.secondaryLabelColor().set()
        for h in range(6):
            x = px(h)
            tick = NSBezierPath.bezierPath()
            tick.setLineWidth_(0.5)
            tick.moveToPoint_(NSMakePoint(x, B))
            tick.lineToPoint_(NSMakePoint(x, B - 4))
            tick.stroke()
            label = "0" if h == 0 else f"{h}h"
            NSString.stringWithString_(label).drawAtPoint_withAttributes_(
                NSMakePoint(x - 5, 1), attrs
            )

        if self._session_start_ts is None or not self._samples:
            return

        # Map samples to screen coordinates within the 5h window
        points = []
        for s in self._samples:
            h = (s["ts"] - self._session_start_ts) / 3600.0
            if -0.1 <= h <= 5.1:
                points.append(NSMakePoint(px(h), py(min(100.0, max(0.0, s["util"])))))

        if len(points) >= 2:
            NSColor.systemBlueColor().set()
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(1.5)
            path.moveToPoint_(points[0])
            for pt in points[1:]:
                path.lineToPoint_(pt)
            path.stroke()

        # Dashed orange projection from last point to where 100% is reached
        if self._rate is not None and self._rate > 0.5 and self._samples:
            last_s    = self._samples[-1]
            last_util = last_s["util"]
            if last_util < 100.0:
                last_h       = (last_s["ts"] - self._session_start_ts) / 3600.0
                h_to_full    = (100.0 - last_util) / self._rate
                target_h     = min(last_h + h_to_full, 5.0)
                target_util  = min(100.0, last_util + (target_h - last_h) * self._rate)
                NSColor.systemOrangeColor().set()
                proj = NSBezierPath.bezierPath()
                try:
                    proj.setLineDash_count_phase_([4.0, 3.0], 2, 0.0)
                except Exception:
                    pass
                proj.setLineWidth_(1.0)
                proj.moveToPoint_(NSMakePoint(px(last_h), py(last_util)))
                proj.lineToPoint_(NSMakePoint(px(target_h), py(target_util)))
                proj.stroke()


# ── App ───────────────────────────────────────────────────────────────────────

class ClaudeRingApp(rumps.App):
    def __init__(self):
        super().__init__("", quit_button=None)
        cfg = self._load_config()
        self._limit       = cfg["limit"]
        self._show_pct    = cfg["show_pct"]
        self._tokens_now  = 0
        self._state       = {}
        self._live        = None
        self._lock        = threading.Lock()

        # Pre-render all 101 ring icons in the background so slider drag is
        # smooth without on-demand rendering.
        threading.Thread(target=_prerender_icons, daemon=True).start()

        toggle_item = rumps.MenuItem("Show percentage", callback=self._toggle_pct)
        toggle_item.state = 1 if self._show_pct else 0
        self._toggle_item = toggle_item

        # "Advanced" submenu — usage chart
        advanced            = rumps.MenuItem("Advanced")
        self._advanced_menu = advanced
        self._chart_view    = None

        self.menu = [
            rumps.MenuItem("5h usage: —",            callback=None),
            rumps.MenuItem("  Reset: —",             callback=None),
            rumps.MenuItem("  Rate: —",              callback=None),
            rumps.MenuItem("  Full in: —",           callback=None),
            rumps.MenuItem("7-day usage: —",         callback=None),
            rumps.MenuItem("  Reset 7d: —",          callback=None),
            rumps.MenuItem("  Source: —",            callback=None),
            None,
            toggle_item,
            advanced,
            rumps.MenuItem("Export usage data…",     callback=self._export_data),
            None,
            rumps.MenuItem("Quit",                   callback=rumps.quit_application),
        ]

        # Inject the usage chart into the Erweitert submenu.
        self._install_chart_item(parent_menu=advanced)

        # First sync runs immediately on main thread so the icon is correct at launch
        self._fetch()
        rumps.Timer(self._tick, UPDATE_INTERVAL).start()

    # ── Slider injection ──────────────────────────────────────────────────────

    def _install_slider_item(self, parent_menu, after):
        """
        Build a custom NSView (slider + value label) and insert it into the
        underlying NSMenu of `parent_menu` (a rumps.MenuItem with submenu)
        right after the given marker rumps.MenuItem.
        """
        initial_pct = int(round(self._current_pct() * 100))

        view   = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 48))
        slider = NSSlider.alloc().initWithFrame_(NSMakeRect(12, 24, 216, 20))
        slider.setMinValue_(0.0)
        slider.setMaxValue_(100.0)
        slider.setIntValue_(initial_pct)
        slider.setContinuous_(True)
        try:
            slider.setNumberOfTickMarks_(11)
            slider.setTickMarkPosition_(1)  # NSTickMarkPositionAbove
            slider.setAllowsTickMarkValuesOnly_(False)
        except Exception:
            pass
        view.addSubview_(slider)

        label = NSTextField.alloc().initWithFrame_(NSMakeRect(12, 4, 216, 16))
        label.setEditable_(False)
        label.setBordered_(False)
        label.setSelectable_(False)
        label.setDrawsBackground_(False)
        label.setFont_(NSFont.systemFontOfSize_(11))
        try:
            label.setTextColor_(NSColor.secondaryLabelColor())
        except Exception:
            pass
        label.setStringValue_(f"→ {initial_pct:3d}% (Claude-Anzeige hier matchen)")
        view.addSubview_(label)

        handler = _SliderHandler.alloc().initWithApp_(self)
        handler._label = label
        slider.setTarget_(handler)
        slider.setAction_(b"sliderChanged:")

        slider_item = NSMenuItem.alloc().init()
        slider_item.setView_(view)

        # Insert into the parent submenu's NSMenu right after the marker.
        try:
            # parent_menu is a rumps.MenuItem with a submenu (added via .update(...))
            ns_menu        = parent_menu._menuitem.submenu()
            marker_ns_item = after._menuitem
            idx            = ns_menu.indexOfItem_(marker_ns_item)
            if idx < 0:
                ns_menu.addItem_(slider_item)
            else:
                ns_menu.insertItem_atIndex_(slider_item, idx + 1)
        except Exception as exc:
            return

        # Keep references — NSObjects need to be held by Python or get GC'd.
        self._slider         = slider
        self._slider_handler = handler
        self._slider_label   = label
        self._slider_item    = slider_item

    # ── Chart injection ───────────────────────────────────────────────────────

    def _install_chart_item(self, parent_menu):
        """Build a _ChartView NSView and inject it as the sole item in the
        Erweitert submenu's NSMenu."""
        view       = _ChartView.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 120))
        chart_item = NSMenuItem.alloc().init()
        chart_item.setView_(view)
        try:
            ns_item = parent_menu._menuitem
            ns_menu = ns_item.submenu()
            if ns_menu is None:
                ns_menu = NSMenu.alloc().init()
                ns_item.setSubmenu_(ns_menu)
            ns_menu.addItem_(chart_item)
        except Exception as exc:
            return
        # Hold Python references so ObjC objects aren't GC'd.
        self._chart_view    = view
        self._chart_item_ns = chart_item

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        cfg = dict(DEFAULTS)
        try:
            raw = json.loads(CONFIG_PATH.read_text())
            if "limit" in raw:
                cfg["limit"] = int(raw["limit"])
            if "show_pct" in raw:
                cfg["show_pct"] = bool(raw["show_pct"])
        except Exception:
            pass
        return cfg

    def _save_config(self):
        try:
            CONFIG_PATH.write_text(json.dumps({
                "limit": self._limit,
                "show_pct": self._show_pct,
            }))
        except Exception:
            pass

    # ── Fetch & update ────────────────────────────────────────────────────────

    def _fetch(self):
        # Quit cleanly when Claude Desktop is no longer running.
        # LaunchAgent (KeepAlive/SuccessfulExit:false) will not restart us on a
        # clean exit — and WatchPaths will re-launch us when Claude opens again.
        if not _is_claude_running():
            rumps.quit_application(None)
            return

        now        = time.time()
        tokens_now = _read_tokens_now()
        state      = _update_session(now, tokens_now, _load_state())
        _save_state(state)

        # ── Live source lookup, in order of freshness ─────────────────────────
        # 1. Disk cache — if fresh enough, no need to hit the network.
        live = _read_live_usage()
        cache_age = (now - live["cache_mtime"]) if live else None

        # 2. Cache stale (or missing)? Make our own API call.
        if live is None or cache_age > CACHE_FRESH_SEC:
            api_live = _fetch_usage_via_api()
            if api_live is not None:
                live = api_live
            elif live is not None and cache_age > LIVE_MAX_AGE:
                # Cache too old AND API failed → don't use cache at all
                live = None

        # Log the real utilization for rate tracking — only genuine live values,
        # never fallback estimates (they would distort the rate).
        if live is not None:
            _append_usage_log(live["utilization"])

        with self._lock:
            self._tokens_now = tokens_now
            self._state      = state
            self._live       = live
        self._update_icon()

    # ── Icon & menu ───────────────────────────────────────────────────────────

    def _update_icon(self, sync_slider: bool = True):
        with self._lock:
            state      = dict(self._state)
            tokens_now = self._tokens_now
            limit      = self._limit
            live       = self._live

        # ── Choose the display value ──────────────────────────────────────────
        # If we have a fresh /usage snapshot (cache or API), use Claude's own
        # numbers. Otherwise fall back to the session-anchor heuristic.
        sd_util   = None
        sd_resets = None
        if live is not None:
            pct       = max(0.0, min(1.0, live["utilization"] / 100.0))
            resets_ts = live.get("resets_at_ts")
            sd_util   = live.get("sd_utilization")
            sd_resets = live.get("sd_resets_at_ts")
            age       = int(time.time() - live['cache_mtime'])
            tag       = "API" if live.get("source") == "api" else "Cache"
            source    = f"live ({tag}, {age}s ago)"
        else:
            usage     = _session_usage(state, tokens_now)
            pct       = min(1.0, usage / limit) if limit > 0 else 0.0
            anchor_ts = state.get("session_start_ts")
            resets_ts = (anchor_ts + WINDOW_SEC) if anchor_ts is not None else None
            source    = "estimate (buddy.json)"

        self.icon     = _get_or_render_icon(int(round(pct * 100)))
        self.template = True
        # Fixed-width title (always 5 chars) so the status item width stays
        # constant — otherwise the menu shifts under the user's cursor.
        self.title    = f" {int(round(pct * 100)):3d}%" if self._show_pct else ""

        def _reset_label(ts):
            if ts is None:
                return "—"
            return _format_eta(int(ts - time.time()))

        self.menu["5h usage: —"].title = f"5h usage: {int(round(pct * 100))}%"
        self.menu["  Reset: —"].title  = f"  Reset: {_reset_label(resets_ts)}"

        # ── Consumption rate + projection ─────────────────────────────────────
        util_now = pct * 100.0
        _samples = _read_usage_log()
        rate, _  = _usage_rate(_samples, time.time())
        if rate is None:
            rate_str, full_str = "— (collecting data)", "—"
        elif rate <= 0.5:
            rate_str, full_str = "stable", "—"
        else:
            rate_str = f"+{rate:.0f}%/h"
            if util_now >= 100:
                full_str = "reached"
            else:
                secs_full  = int((100.0 - util_now) / rate * 3600)
                eta_full   = "~" + _format_eta(secs_full)[3:]   # strip "in "
                secs_reset = int(resets_ts - time.time()) if resets_ts else None
                if secs_reset is not None and secs_full > secs_reset:
                    full_str = f"{eta_full} (after reset)"
                else:
                    full_str = eta_full
        self.menu["  Rate: —"].title    = f"  Rate: {rate_str}"
        self.menu["  Full in: —"].title = f"  Full in: {full_str}"

        if sd_util is not None:
            self.menu["7-day usage: —"].title = f"7-day usage: {int(round(sd_util))}%"
            self.menu["  Reset 7d: —"].title  = f"  Reset: {_reset_label(sd_resets)}"
        else:
            self.menu["7-day usage: —"].title = "7-day usage: —"
            self.menu["  Reset 7d: —"].title  = "  Reset: —"

        self.menu["  Source: —"].title = f"  Source: {source}"

        # Refresh the usage chart with the latest samples and rate.
        if self._chart_view is not None:
            self._chart_view._samples          = _samples
            self._chart_view._session_start_ts = state.get("session_start_ts")
            self._chart_view._reset_at_ts      = live.get("resets_at_ts") if live is not None else None
            self._chart_view._rate             = rate
            self._chart_view.setNeedsDisplay_(True)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _export_data(self, _sender):
        """Export the usage log as a CSV file to ~/Downloads."""
        samples = _read_usage_log()
        if not samples:
            rumps.notification("claude-usage-bar", "", "No data recorded yet.")
            return
        ts_str  = datetime.now().strftime("%Y%m%d-%H%M%S")
        path    = Path.home() / "Downloads" / f"claude-usage-{ts_str}.csv"
        rows    = ["timestamp,datetime_utc,utilization_pct"]
        for s in samples:
            dt = datetime.fromtimestamp(s["ts"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows.append(f"{s['ts']},{dt},{s['util']:.2f}")
        try:
            path.write_text("\n".join(rows) + "\n")
            rumps.notification("claude-usage-bar", "Export saved", str(path))
        except Exception as exc:
            rumps.notification("claude-usage-bar", "Export failed", str(exc))

    def _tick(self, _sender):
        # rumps.Timer fires on main thread → safe to update UI here.
        self._fetch()

    def _force_update(self, _sender):
        self._fetch()

    def _reset_session(self, _sender):
        """Manually drop the persisted anchor — next tick will bootstrap fresh."""
        try:
            SESSION_STATE.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        self._fetch()

    def _set_limit(self, _sender):
        response = rumps.Window(
            message="Maximale Tokens pro 5h-Fenster:",
            title="Limit anpassen",
            default_text=str(self._limit),
            ok="Speichern",
            cancel="Abbrechen",
            dimensions=(260, 24),
        ).run()
        if response.clicked:
            try:
                val = int(response.text.replace(",", "").strip())
                if val > 0:
                    with self._lock:
                        self._limit = val
                    self._save_config()
                    self._update_icon()
            except ValueError:
                pass

    def _toggle_pct(self, sender):
        self._show_pct = not self._show_pct
        sender.state = 1 if self._show_pct else 0
        self._save_config()
        self._update_icon()

    # ── Slider-based calibration ──────────────────────────────────────────────

    def _current_pct(self) -> float:
        with self._lock:
            state      = dict(self._state)
            tokens_now = self._tokens_now
            limit      = self._limit
        if limit <= 0:
            return 0.0
        return min(1.0, _session_usage(state, tokens_now) / limit)

    def _render_icon_only(self, pct: float):
        """
        Fast path: switch to a pre-rendered ring icon. Renders are cached on
        disk so every slider event is just a file-path switch — no PIL work.

        We deliberately do NOT touch self.title here — proportional system
        font glyph widths differ between digits, so even a "fixed-width"
        title string shifts the NSStatusItem's frame and anchors the open
        menu to a new position. Title resyncs when the user releases.
        """
        pct_int       = int(round(max(0.0, min(1.0, pct)) * 100))
        self.icon     = _get_or_render_icon(pct_int)
        self.template = True

    def _calibrate_from_slider(self, pct_int: int, finalize: bool = False):
        """
        Reverse-engineer the limit so our display shows pct_int%.
        new_limit = current_session_usage / (pct_int / 100)
        Anchor stays untouched.

        During drag (finalize=False) we only redraw the icon — touching
        self.title or internal menu items shifts the open menu under the
        cursor. When the mouse is released (finalize=True) we do a full
        refresh to keep title and inner items consistent.
        """
        if pct_int <= 0:
            self._render_icon_only(0.0)
            if finalize:
                self._update_icon(sync_slider=False)
            return
        with self._lock:
            state      = dict(self._state)
            tokens_now = self._tokens_now
        usage = _session_usage(state, tokens_now)
        if usage <= 0:
            self._render_icon_only(pct_int / 100.0)
            return
        new_limit = max(1, int(round(usage / (pct_int / 100.0))))
        with self._lock:
            self._limit = new_limit
        self._save_config()
        if finalize:
            self._update_icon(sync_slider=False)
        else:
            self._render_icon_only(pct_int / 100.0)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not _acquire_single_instance():
        os._exit(0)   # another instance is already running
    if not _is_claude_running():
        os._exit(0)   # Claude Desktop is not open
    ClaudeRingApp().run()
