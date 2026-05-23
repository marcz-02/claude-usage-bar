# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

macOS menu bar app that shows Claude token usage as a white brushstroke ring icon. Tracks Claude's 5h usage window via a **persisted session anchor** (not a rolling window over our own log).

## Managing the app

The app runs as a LaunchAgent (auto-starts on login):

```bash
# Restart
launchctl stop com.marcz.claude-token-ring && launchctl start com.marcz.claude-token-ring

# Status
launchctl list | grep claude-token-ring   # PID, last exit code

# Logs
cat /tmp/claude-token-ring.log

# Manual start (debugging)
nohup /Library/Developer/CommandLineTools/usr/bin/python3 ~/ClaudeTokenRing/claude_token_ring.py > /tmp/claude-token-ring.log 2>&1 &
```

LaunchAgent plist: `~/Library/LaunchAgents/com.marcz.claude-token-ring.plist`

## Preview animation in browser

```bash
/Library/Developer/CommandLineTools/usr/bin/python3 -m http.server 7823 --directory ~/ClaudeTokenRing
```

## Python interpreter

Always use `/Library/Developer/CommandLineTools/usr/bin/python3` (3.9.6). The default `python3` at `/opt/local/bin/python3` (3.13) lacks the required packages. **Never use `int | None` type hints** — Python 3.9 doesn't support the `|` union syntax; omit return type annotations or use `Optional`.

Installed packages: `rumps 0.4.0`, `Pillow 11.3.0`, `pyobjc`, `zstandard` (decode HTTP cache), `pycryptodome` (decrypt cookies), `curl_cffi` (Chrome-TLS HTTP). Install extras with `python3 -m pip install --user <pkg>`.

## Architecture

### `ring_renderer.py`

Core rendering. `render_ring(pct, size=44, dark_mode=True)` renders at 4× size then downscales via LANCZOS for smooth antialiasing.

Key geometry: Pillow's `draw.arc(bbox, width=w)` places the stroke's *outer edge* at the bbox boundary and extends inward — so the background ring uses a separate `bg_bbox` (inset by `max_stroke // 2`) to share the same visual center radius as the arc. Brushstroke taper: `max_stroke * t^0.72` over ~200 segments. Round cap at the thick leading edge is a filled ellipse placed at `mid_r` (arc center).

**This file is considered stable. Don't touch unless explicitly working on rendering.**

### `claude_token_ring.py`

rumps menu bar app. Every 30s `_fetch()` resolves the displayed value through a **three-tier lookup**, freshest first:

1. **HTTP cache** (`_read_live_usage`) — parse Claude Desktop's Chromium disk cache for the most recent `GET /api/organizations/<uuid>/usage` response. Body is zstd-compressed JSON: `{"five_hour": {"utilization": 0..100, "resets_at": "<ISO8601>"}}`. If younger than `CACHE_FRESH_SEC` (60s), used directly — no network call.
2. **Direct API call** (`_fetch_usage_via_api`) — if the cache is stale/missing, call `/usage` ourselves. This is the key to live updates during normal chat use (Claude Desktop only refreshes its own cache when its Code/Plan UI is shown). See "Direct API call" below.
3. **Fallback** — `buddy-tokens.json` → `tokens-today` + the 4-phase session-anchor heuristic (`_update_session`). Only used when both live paths fail.

`utilization` from tier 1/2 is Claude's own UI number — authoritative, no calibration needed. The menu's `Quelle:` line shows which tier produced the current value (`live (API, vor Xs)`, `live (Cache, vor Xs)`, or `Schätzung (buddy.json)`).

**The `/usage` API is percentage-only — it has no token counts.** It returns two windows: `five_hour` and `seven_day`, each `{utilization: 0..100, resets_at: ISO8601}`. Both are surfaced in the menu. `buddy-tokens.json`'s `tokens-today` is the only token-count source anywhere and it's unreliable (sporadic writes, `0` on date mismatch) — so the app does not display raw token counts. `_shape_usage()` normalises a raw `/usage` JSON dict into the internal shape used by both tier-1 and tier-2 readers. (Verified dead-end: chat-conversation messages carry no `usage` field; `usage_v2` / `usage/details` / `cost` etc. all 404.)

### Consumption-rate tracking

Since only a percentage is available, the app makes the "fills up faster" feeling measurable. Every tick with a genuine live value appends `{ts, util}` to `~/.claude_token_ring_usage_log.jsonl` (`_append_usage_log`, pruned to `RATE_LOG_MAX` lines). `_usage_rate()` computes %/h over the last `RATE_WINDOW_SEC` (30 min), restarting the window after any drop > `RATE_RESET_DROP` (a 5h reset). `_update_icon` shows `Tempo: +X%/h` and projects `Voll in: ~Yh Zm` (with `(nach Reset)` appended when the projection lands past the 5h reset). Fallback estimates are never logged — only real live values.

### Direct API call (`_fetch_usage_via_api`)

To call `/usage` ourselves we need Claude Desktop's session cookies and must defeat Cloudflare's bot manager:

1. **Cookie decryption** — Claude Desktop stores cookies in a SQLite DB (`Cookies`), encrypted with the Chromium Safe Storage scheme. `_get_safe_storage_key()` reads the password from macOS Keychain (`security find-generic-password -s "Claude Safe Storage" -a "Claude Key" -w`) and derives the AES key via `PBKDF2-HMAC-SHA1(pw, "saltysalt", 1003, 16)`. `_decrypt_chrome_cookie()` does AES-128-CBC (IV = 16 spaces), strips the `v10` prefix and the 32-byte SHA256 host prefix, removes PKCS7 padding.
2. **TLS impersonation** — Python's default TLS fingerprint is blocked by Cloudflare (403). `curl_cffi` with `impersonate="chrome"` mimics Chrome's TLS/HTTP2 fingerprint and passes.
3. The `sessionKey` cookie + `cf_clearance` (both decrypted) authenticate the request. Org UUID comes from `_get_org_uuid()` (scraped once from any cached `/api/organizations/<uuid>/...` URL).

**First run**: macOS shows a Keychain access prompt for `Claude Safe Storage`. The user must click "Always Allow". Until then, tier 2 fails and the app uses tier 1/3.

All of `_fetch_usage_via_api` fails soft (returns `None`) → caller falls through to the next tier.

### Session-anchor model (the core logic)

Persisted in `~/.claude_token_ring_session.json`:

```json
{
  "session_start_ts": 1779011161.453,
  "session_start_tokens": 0,
  "last_seen_ts": 1779012739.4,
  "last_seen_tokens": 32890,
  "midnight_carryover": 0,
  "confidence": "high|medium|low"
}
```

**Display:**
```
session_usage = midnight_carryover + max(0, tokens_now - session_start_tokens)
pct           = session_usage / limit
reset_ts      = session_start_ts + 5h
```

**Four-phase update per tick:**

1. **Midnight reset**: if `tokens_now < last_seen_tokens - 1000`, the daily counter wrapped → `midnight_carryover += last_seen_tokens - session_start_tokens`, baseline reset to 0.
2. **Session expiry**: if `now - session_start_ts >= 5h`, drop anchor. New activity (tokens increase) → start fresh anchor with `confidence=high`. No activity → null anchor, display 0%.
3. **Bootstrap** (cold start, no prior state): scan `~/.claude/projects/**/*.jsonl` for the earliest message in the last 5h → use as `session_start_ts` with `session_start_tokens=0` and `confidence=medium`. No hint → `anchor=now`, baseline=`tokens_now`, `confidence=low`.
4. **Refresh** `last_seen_*` fields.

### `_ChartView` (NSView, ~line 625)

Draws the 5h utilisation chart inside `Erweitert ▸` (300×120 px, injected via `_install_chart_item`). X-axis: 0–5h of the current session. Y-axis: 0–100%. Blue line = actual samples from `usage_log.jsonl`; orange dashed = projection to 100% based on current rate; red horizontal line at 100%.

Data is pushed by setting instance variables directly in `_update_icon`, then calling `setNeedsDisplay_(True)`:
```python
self._chart_view._samples          = _samples
self._chart_view._session_start_ts = state.get("session_start_ts")
self._chart_view._rate             = rate
self._chart_view.setNeedsDisplay_(True)
```
**pyobjc pitfall:** every method defined on an `NSObject` subclass is registered as an ObjC selector. A Python method with N arguments (excluding `self`) needs exactly N underscores in its name to match the selector's colon count — otherwise pyobjc raises `BadPrototypeError` at class-definition time. `@objc.python_method` should bypass this but is unreliable in the installed version. Workaround: set instance variables directly from outside instead of defining a custom update method.

### `index.html`

HTML/Canvas preview for tuning the ring visuals.

### `ring_animation.py`

tkinter reference animation. Must run from a real Terminal (not Claude Code's bash tool).

## Menu structure

| Item | Action |
|---|---|
| `5h usage / Reset / Rate / Full in / 7-day usage / Reset 7d / Source` | Live readouts (no callback) |
| `Show percentage` | Toggle "12%" text next to icon (persisted in config) |
| `Advanced ▸` | Usage chart NSView (300×120 px) — no text items |
| `Quit` | Quit |

## Files & paths

| Path | Purpose |
|---|---|
| `~/Library/Application Support/Claude/Cache/Cache_Data/*_0` | Tier 1 source: Chromium HTTP cache, contains `/usage` responses (zstd JSON) |
| `~/Library/Application Support/Claude/Cookies` | SQLite DB — session cookies for the tier-2 direct API call |
| `~/Library/Application Support/Claude/buddy-tokens.json` | Tier 3 fallback: `tokens-today` (server-synced, lagged) |
| macOS Keychain `Claude Safe Storage` / `Claude Key` | Password for decrypting the cookies |
| `~/.claude/projects/**/*.jsonl` | Source: Claude Code message timestamps (bootstrap hint) |
| `~/.claude_token_ring_session.json` | Session anchor — drives the tier-3 fallback display |
| `~/.claude_token_ring_config.json` | `{limit, show_pct}` user config |
| `~/.claude_token_ring_usage_log.jsonl` | `{ts, util}` history for the consumption-rate / projection feature |
| `~/.claude_token_ring_log.jsonl` | Legacy log — no longer used by display logic (kept for debug) |
| `/tmp/claude_ring_icons/r000.png` … `r100.png` | **Pre-rendered icon cache** — one PNG per integer % (0–100) |

## Calibrating the limit

Only relevant for the tier-3 fallback (when both live paths fail). **The slider is currently disabled** (code retained for future use — see next section). To re-enable: add a `Slider-Kalibrierung:` marker item to the `Erweitert` submenu and call `self._install_slider_item(parent_menu=advanced, after=marker)` in `__init__`. Once active, drag the slider to whatever % Claude itself displays; `new_limit = session_usage / (claude_pct / 100)` is saved to config; the session anchor stays untouched.

## Confidence levels

(Tier-3 fallback only — the live API source needs no confidence rating.)

| | Meaning |
|---|---|
| `high` | Anchor was created by this app while running (saw the session start live) |
| `medium` | Anchor reconstructed from Claude Code JSONL timestamps on cold start |
| `low` | Cold start with no Code hint — anchor=now, baseline=current tokens-today (display starts at 0%, prior session usage is invisible) |

## Icon rendering & cache

All 101 ring icons (0%–100%) are rendered once into `/tmp/claude_ring_icons/r000.png` … `r100.png`. On app startup a background daemon thread fills any missing entries via `_prerender_icons()`. During slider drag and regular ticks we just set `self.icon = _get_or_render_icon(pct_int)` — pure path-switch, no PIL work, no disk writes.

Each pct gets a unique filename, so rumps' path-based icon caching is irrelevant. If the renderer is changed, `rm -rf /tmp/claude_ring_icons/` to invalidate.

## Disabled slider code (retained for future use)

`_SliderHandler`, `_install_slider_item`, and `_calibrate_from_slider` are fully implemented but never called. The slider would calibrate the tier-3 fallback limit by reverse-engineering it from a user-supplied % value. To re-enable, see "Calibrating the limit" above.

If re-enabled, be aware: the NSSlider fires 60–120 events/sec during drag. `_render_icon_only()` handles this by switching the icon path without touching `self.title` (which would shift the NSStatusItem frame and re-anchor the open dropdown). Full menu resync happens on mouse-up via `NSEvent.pressedMouseButtons() == 0`.

## Known limitations

- **First-run Keychain prompt**: until the user grants `Claude Safe Storage` access, tier 2 (direct API) fails silently — app falls back to tier 1/3.
- **Cloudflare / format drift**: if Anthropic changes the `/usage` shape or tightens bot detection beyond `curl_cffi`'s impersonation, tier 1+2 break and the app falls back to tier 3.
- **Tier-3 cold start**: bootstrap with no Code hint falls to `confidence=low` — display starts at 0% and underestimates. Recalibrate by re-enabling the slider (see "Calibrating the limit").
- **Midnight in session**: `midnight_carryover` compensates, but the first tick after midnight may be briefly off until phase 1 fires.

## ToS note

The direct API call uses the user's own authenticated session to read the user's own `/usage` endpoint — no auth bypass, no third-party use. All decryption and network calls stay on the user's machine.

## Key values

| | Value | Why |
|---|---|---|
| `DEFAULT_LIMIT` | 197,000 | Tier-3 fallback limit if config missing |
| `UPDATE_INTERVAL` | 30s | Tick frequency |
| `WINDOW_SEC` | 5 × 3600 | Claude's session length |
| `CACHE_FRESH_SEC` | 60s | Below this age, skip the API call and use the cache |
| `LIVE_MAX_AGE` | 600s | Above this, the cache is discarded entirely |
| `MIDNIGHT_DROP` | 1000 | Threshold to distinguish midnight reset from jitter |
| `CODE_SCAN_LIMIT` | 40 | Max recent JSONLs scanned for bootstrap hint |
| `KEYCHAIN_SERVICE` / `KEYCHAIN_ACCOUNT` | `Claude Safe Storage` / `Claude Key` | Keychain lookup for cookie decryption |
| `_TAPER` | 0.72 | Brushstroke exponent |
| Icon size | 44 px | 22 pt × 2× Retina |
