#!/bin/bash
# Makes the bot start automatically when you log in to your Mac, restart if it crashes,
# and keep the Mac awake while it runs (on the power adapter).
#   ./mac/install_autostart.sh       install + start now
#   ./mac/uninstall_autostart.sh     remove
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.mt5bot.watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "$DIR" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
    echo "⚠️  The bot is in $DIR."
    echo "    macOS blocks background services from Desktop/Documents/Downloads."
    echo "    Move the folder to your home folder first, e.g.:  mv \"$DIR\" ~/mt5-bot"
    exit 1;;
esac

if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "No .venv found in $DIR. Set it up first (see MAC_SETUP.md step 2)."
  exit 1
fi
mkdir -p "$DIR/data" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-is</string>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/watchdog/watchdog.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/data/watchdog.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/watchdog.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✅ Installed. The bot now starts at login and restarts if it stops."
echo "   Log:     tail -f \"$DIR/data/bot.log\""
echo "   Status:  launchctl print gui/$(id -u)/$LABEL | grep state"
echo "   Remove:  ./mac/uninstall_autostart.sh"
