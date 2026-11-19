# Run the bot 24/7 on a Windows VPS

## 1. Prepare the VPS (once)

1. Rent a **Windows** VPS close to your broker's server (often London or New York).
2. Install **MetaTrader 5** from your broker, log in to your account, tick
   **Tools → Options → Expert Advisors → Allow algorithmic trading**.
3. Install **Python 3.11** (tick "Add Python to PATH").
4. Copy this project to `C:\mt5-bot`, then in a terminal:
   ```bat
   cd C:\mt5-bot
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env      &  notepad .env
   python run.py --once
   ```

## 2. Make MT5 start by itself

- Put a shortcut to `terminal64.exe` in `shell:startup` (Win+R → `shell:startup`).
- Turn on auto-login for Windows (`netplwiz` → untick "Users must enter a user name…").

## 3. Install the watchdog as a service (NSSM)

Download NSSM from https://nssm.cc, unzip it, then in an **admin** terminal:

```bat
nssm install MT5Bot "C:\mt5-bot\.venv\Scripts\python.exe" "C:\mt5-bot\watchdog\watchdog.py"
nssm set MT5Bot AppDirectory C:\mt5-bot
nssm set MT5Bot AppStdout C:\mt5-bot\data\watchdog.log
nssm set MT5Bot AppStderr C:\mt5-bot\data\watchdog.log
nssm set MT5Bot Start SERVICE_AUTO_START
nssm start MT5Bot
```

> The MetaTrader5 package needs the terminal running in the same Windows session.
> If the service can't see MT5, run the watchdog from Task Scheduler instead:
> **Create Task → Trigger: At log on → Action:** `C:\mt5-bot\start_bot.bat`,
> tick "Run with highest privileges".

## 4. Know when something goes wrong

- Telegram alerts: restarts, lost connection, errors, daily report.
- Add a free uptime monitor (e.g. UptimeRobot "heartbeat" check) so you hear about it
  even if the whole VPS goes dark.
- Windows Update: set **active hours** to cover market hours, and do restarts on weekends.
