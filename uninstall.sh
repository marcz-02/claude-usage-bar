#!/bin/bash
# Claude Token Ring — uninstaller
# Removes the LaunchAgent, app files, and all config/state data.

PLIST_A=~/Library/LaunchAgents/com.marcz.claude-token-ring.plist
PLIST_B=~/Library/LaunchAgents/com.claudetokenring.app.plist

echo "Stopping Claude Token Ring..."

# Stop and unload LaunchAgent (both possible label names)
launchctl unload "$PLIST_A" 2>/dev/null || true
launchctl unload "$PLIST_B" 2>/dev/null || true

# Kill any running instance
pkill -f "claude_token_ring.py" 2>/dev/null || true

echo "Removing LaunchAgent..."
rm -f "$PLIST_A" "$PLIST_B"

echo "Removing app files..."
rm -rf ~/ClaudeTokenRing

echo "Removing config and state files..."
rm -f ~/.claude_token_ring_session.json
rm -f ~/.claude_token_ring_config.json
rm -f ~/.claude_token_ring_usage_log.jsonl
rm -f ~/.claude_token_ring_log.jsonl

echo "Removing temp files..."
rm -rf /tmp/claude_ring_icons
rm -f /tmp/claude_ring_disabled.png
rm -f /tmp/claude-token-ring.log
rm -f /tmp/ctr.zip
rm -rf /tmp/claude-usage-bar-main

echo ""
echo "Done — Claude Token Ring has been completely removed."
