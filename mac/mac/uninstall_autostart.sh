#!/bin/bash
# Stops the bot and removes the auto-start. Open trades keep their stop-loss and
# take-profit at the broker.
LABEL="com.mt5bot.watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed. The bot is stopped and will not start at login."
