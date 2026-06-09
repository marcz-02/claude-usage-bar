#!/bin/bash
# Claude Token Ring — one-shot installer
# Installs Python dependencies and sets up the macOS LaunchAgent.

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
DIRNAME="$(basename "$DIR")"

# Derive namespace from directory name — supports parallel beta installs.
# ~/ClaudeTokenRing      → label com.marcz.claude-token-ring
# ~/ClaudeTokenRingBeta  → label com.marcz.claude-token-ring-beta
if [ "$DIRNAME" = "ClaudeTokenRing" ]; then
  NS=""
else
  NS=$(echo "$DIRNAME" | sed 's/ClaudeTokenRing//' | tr '[:upper:]' '[:lower:]')
fi
NS_LABEL="${NS:+-$NS}"   # "" or "-beta"

LABEL="com.marcz.claude-token-ring${NS_LABEL}"
PLIST=~/Library/LaunchAgents/${LABEL}.plist

# ── Detect Python ──────────────────────────────────────────────────────────────
# Try candidates in order — first one that exists and is Python 3.9+ wins.
# Using the correct binary is critical: macOS Keychain "Always Allow" is tied
# to the exact executable path. A mismatch causes repeated Keychain prompts.
PYTHON=""
for candidate in \
    /Library/Developer/CommandLineTools/usr/bin/python3 \
    /usr/bin/python3 \
    /opt/homebrew/bin/python3 \
    /opt/local/bin/python3 \
    "$(command -v python3 2>/dev/null)"; do
  [ -z "$candidate" ] && continue
  if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Error: Python 3.9+ not found."
  echo "Install Xcode Command Line Tools:  xcode-select --install"
  exit 1
fi

echo "Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"

if [ ! -d ~/Library/LaunchAgents ]; then
  mkdir -p ~/Library/LaunchAgents
fi

# ── Install Python packages ────────────────────────────────────────────────────
echo "Installing Python packages..."
"$PYTHON" -m pip install --user -r "$DIR/requirements.txt"

# ── Install LaunchAgent ────────────────────────────────────────────────────────
echo "Setting up LaunchAgent..."
sed -e "s|__APP_DIR__|$DIR|g" -e "s|__HOME__|$HOME|g" -e "s|__LABEL__|$LABEL|g" -e "s|__NS_LABEL__|$NS_LABEL|g" -e "s|__PYTHON__|$PYTHON|g" "$DIR/launchagent.plist.template" > "$PLIST"

# Unload any existing instance before loading the new one
launchctl unload "$PLIST" 2>/dev/null || true

echo ""
echo "============================================="
echo "  macOS security prompt is coming up next."
echo ""
echo "  >>> Click 'Always Allow' <<<"
echo ""
echo "  (Not just 'Allow' — that asks again every"
echo "  time. 'Always Allow' = done forever.)"
echo "============================================="
echo ""

launchctl load "$PLIST"

echo ""
echo "Done! The ring icon should appear in your menu bar within a few seconds."
echo ""
echo "First run: macOS will show a Keychain prompt for 'Claude Safe Storage'."
echo "Click 'Always Allow' — this is needed for live usage lookups."
echo ""
echo "Logs: tail -f /tmp/claude-token-ring.log"
echo "Stop: launchctl unload $PLIST"
