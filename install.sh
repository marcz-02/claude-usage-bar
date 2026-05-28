#!/bin/bash
# Claude Token Ring — one-shot installer
# Installs Python dependencies and sets up the macOS LaunchAgent.

set -e

PYTHON=/Library/Developer/CommandLineTools/usr/bin/python3
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.claudetokenring.app"
PLIST=~/Library/LaunchAgents/${LABEL}.plist

# ── Sanity checks ──────────────────────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
  echo "Error: Python 3.9 not found at $PYTHON"
  echo "Install Xcode Command Line Tools:  xcode-select --install"
  exit 1
fi

if [ ! -d ~/Library/LaunchAgents ]; then
  mkdir -p ~/Library/LaunchAgents
fi

# ── Install Python packages ────────────────────────────────────────────────────
echo "Installing Python packages..."
"$PYTHON" -m pip install --user -r "$DIR/requirements.txt"

# ── Install LaunchAgent ────────────────────────────────────────────────────────
echo "Setting up LaunchAgent..."
sed -e "s|__APP_DIR__|$DIR|g" -e "s|__HOME__|$HOME|g" "$DIR/launchagent.plist.template" > "$PLIST"

# Unload any existing instance before loading the new one
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "Done! The ring icon should appear in your menu bar within a few seconds."
echo ""
echo "First run: macOS will show a Keychain prompt for 'Claude Safe Storage'."
echo "Click 'Always Allow' — this is needed for live usage lookups."
echo ""
echo "Logs: tail -f /tmp/claude-token-ring.log"
echo "Stop: launchctl unload $PLIST"
