#!/bin/sh
# Stop the counter and remove the login item (queue and state are kept).
launchctl bootout "gui/$(id -u)/com.piggy.input" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.piggy.input.plist"
echo "piggy-input stopped and removed from login items."
