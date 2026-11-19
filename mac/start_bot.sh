#!/bin/bash
# Starts the bot under the watchdog (auto-restart) and keeps the Mac awake while it runs.
#   ./start_bot.sh            → run in this Terminal window (Ctrl+C to stop)
# For auto-start at login, use:  ./mac/install_autostart.sh
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "No .venv found. First run:  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt"
  exit 1
fi
mkdir -p data
# caffeinate -i: no idle sleep, -s: no system sleep while on power adapter
exec /usr/bin/caffeinate -is .venv/bin/python watchdog/watchdog.py
