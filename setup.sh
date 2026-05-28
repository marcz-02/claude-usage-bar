#!/bin/bash
# Claude Token Ring — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/marcz-02/claude-usage-bar/main/setup.sh | bash

set -e

REPO="https://github.com/marcz-02/claude-usage-bar/archive/refs/heads/main.zip"
DEST=~/ClaudeTokenRing

echo "Downloading Claude Token Ring..."
curl -fsSL "$REPO" -o /tmp/ctr.zip
unzip -q -o /tmp/ctr.zip -d /tmp/
rsync -a /tmp/claude-usage-bar-main/ "$DEST/"
rm -f /tmp/ctr.zip

bash "$DEST/install.sh"
