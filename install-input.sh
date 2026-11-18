#!/bin/sh
# Install the keyboard/mouse counter and start it at login (macOS).
set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Library/Application Support/piggy-input/app"
PLIST="$HOME/Library/LaunchAgents/com.piggy.input.plist"
mkdir -p "$APP" "$HOME/Library/LaunchAgents"
chmod 700 "$HOME/Library/Application Support/piggy-input"
cp "$REPO/piggy_hook.py" "$REPO/piggy_input.py" "$APP/"
sed "s#__HOME__#$HOME#g" "$REPO/com.piggy.input.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/com.piggy.input" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "piggy-input installed and running."
