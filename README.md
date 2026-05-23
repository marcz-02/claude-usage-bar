# Claude Token Ring

macOS menu bar app that shows your [Claude.ai](https://claude.ai) token usage as a brushstroke ring icon — updated live from Claude Desktop, no separate API key needed.

The ring fills from empty (0 %) to full (100 %) as you consume your 5-hour quota. A usage chart in the menu lets you see how fast you're burning through it.

![claude-usage-bar menu](screenshots/menu.png)

## Requirements

- macOS 12+
- [Claude Desktop](https://claude.ai/download) installed and logged in
- Python 3.9 from Xcode Command Line Tools (`xcode-select --install`)

## Install

```bash
git clone https://github.com/YOUR_USERNAME/ClaudeTokenRing.git
cd ClaudeTokenRing
./install.sh
```

The script installs Python dependencies and registers a LaunchAgent so the app starts automatically at login.

**First run:** macOS will show a Keychain prompt for **"Claude Safe Storage"** — click **Always Allow**. This is needed to read Claude Desktop's session cookie for direct usage lookups.

## What it shows

| Element | Meaning |
|---|---|
| Ring icon | Fills 0 → 100 % as you consume your 5-hour Claude quota |
| `%` label | Optional percentage next to the icon (`Show percentage` toggle) |
| Rate | Current consumption rate, e.g. `+18 %/h` |
| Full in | Projected time until 100 %, e.g. `~2h 15m` |
| Advanced ▸ | Chart of actual usage over the current 5-hour session |

## How data is read

Three tiers, freshest first — the **Source** line in the menu tells you which one is active:

1. **Claude Desktop's HTTP cache** — parses zstd-compressed JSON from disk; no network call needed
2. **Direct `/usage` API** — uses your Desktop session cookie; kicks in when the cache is stale (>60 s old)
3. **Fallback heuristic** — local token counter + session-anchor model; used only when both live paths fail (less accurate on cold start)

## Manage the app

```bash
# Restart
launchctl stop com.claudetokenring.app && launchctl start com.claudetokenring.app

# Logs
tail -f /tmp/claude-token-ring.log

# Uninstall
launchctl unload ~/Library/LaunchAgents/com.claudetokenring.app.plist
rm ~/Library/LaunchAgents/com.claudetokenring.app.plist
```

## Known limitations

- Requires Claude Desktop to be running for live data (tiers 1 and 2)
- On cold start without live API access the display may underestimate usage until Claude Desktop is opened
- macOS only

## License

[MIT](LICENSE)
