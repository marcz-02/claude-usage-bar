# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Start manually (Claude Desktop must be open first)
/Library/Developer/CommandLineTools/usr/bin/python3 claude_token_ring.py

# Restart via LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.marcz.claude-token-ring.plist \
  && launchctl load ~/Library/LaunchAgents/com.marcz.claude-token-ring.plist

# Tail logs
tail -f /tmp/claude-token-ring.log
```

## Python runtime

**Always use** `/Library/Developer/CommandLineTools/usr/bin/python3` (3.9.6).  
**No `int | None` type hints** — Python 3.9 does not support union syntax with `|`.  
Install deps: `pip install --user -r requirements.txt`

## Architecture

The app is a single `rumps` menu-bar app (`claude_token_ring.py`) that polls every 30 s.

### Startup guards (entry point)
Two checks run before `ClaudeRingApp().run()`:
1. `_acquire_single_instance()` — fcntl exclusive lock on `/tmp/claude-token-ring.lock`; duplicate starts (triggered by `WatchPaths`) exit with `os._exit(0)`.
2. `_is_claude_running()` — `pgrep -x Claude`; exits if Claude Desktop is not open.

`os._exit(0)` is required (not `sys.exit`) because `import rumps` creates `NSApplication` early.

### Data tiers (freshest first)

| Tier | Source | How |
|---|---|---|
| 1 | Claude Desktop's Chromium HTTP cache | `Cache/Cache_Data/*_0` — zstd-compressed JSON at `/api/organizations/<uuid>/usage` |
| 2 | Direct API call | Same endpoint, using decrypted Chromium cookies + `curl_cffi` Chrome TLS fingerprint |
| 3 | Fallback heuristic | `buddy-tokens.json` + session-anchor model |

The **Source** menu item always shows which tier is active.

### Session-anchor model (Tier 3)
State in `~/.claude_token_ring_session.json`:
- `session_start_ts` + `session_start_tokens` — anchor for the current 5 h window
- `midnight_carryover` — handles tokens-today midnight reset
- `confidence` — `high`/`medium`/`low`; bootstrap uses `~/.claude/projects/**/*.jsonl` timestamps

### Key functions
- `_read_live_usage()` — parses zstd cache file → `{utilization, resets_at_ts, cache_mtime, ...}`
- `_fetch_usage_via_api()` — decrypts Chromium cookies via macOS Keychain (`security find-generic-password`), calls API with `curl_cffi`
- `_update_session()` — 4-phase update: midnight reset → session expiry → bootstrap → refresh
- `_update_icon()` — picks display value (live vs. heuristic), renders menu, sets icon

### Ring renderer (`ring_renderer.py`)
**Do not modify.** Renders a tapered brushstroke arc using Pillow at 4× scale then downsamples.  
`render_ring(pct: float, size=44, dark_mode=True) → PIL.Image`  
101 icons (0–100 %) are pre-rendered into `/tmp/claude_ring_icons/` on startup.

### Threading model
- Main thread: rumps event loop + menu callbacks
- Background daemon thread: `_tick()` → spawns `_fetch()` every 30 s
- Lock: `self._lock` guards `_state`, `_tokens_now`, `_live`
- `rumps.quit_application(None)` is thread-safe (posts to main run loop)

### LaunchAgent (`~/Library/LaunchAgents/com.marcz.claude-token-ring.plist`)
- `KeepAlive: {SuccessfulExit: false}` — does not restart on clean exit (exit 0)
- `WatchPaths: [Cache/Cache_Data]` — launchd auto-starts ring when Claude Desktop opens and writes its cache
- `RunAtLoad: true` — starts at login if Claude Desktop is already open

Template is `launchagent.plist.template`; `install.sh` substitutes `__APP_DIR__` and `__HOME__`.

## Persistent files

| Path | Purpose |
|---|---|
| `~/.claude_token_ring_session.json` | Session anchor — single source of truth for Tier 3 |
| `~/.claude_token_ring_config.json` | `{limit, show_pct}` — user preferences |
| `~/.claude_token_ring_usage_log.jsonl` | Rolling sample log (≤17 280 entries, ~6 months) |
| `/tmp/claude_ring_icons/r000–r100.png` | Pre-rendered icon cache |
| `/tmp/claude-token-ring.lock` | fcntl single-instance lock |
