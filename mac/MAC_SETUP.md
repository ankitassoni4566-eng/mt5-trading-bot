# Mac setup — step by step

The official MT5 Python package only exists for Windows. On a Mac, this bot connects through
**SiliconMetaTrader5**, a free community bridge. It runs a hidden MT5 terminal on your Mac
and lets Python control it with the same commands the Windows package uses. Same bot,
same strategy, same safety rules, different connection.

```
 this bot (Python on macOS) ──local connection──► SiliconMetaTrader5 bridge ──► MT5 terminal ──► your broker
                                 host:port in .env      (runs MT5 under Wine on your Mac)
```

**Best on:** a Mac with Apple Silicon (M1 or newer) running a recent macOS. Intel Macs may work but haven't been tried.

---

## 1. Install Python (once)

Download Python 3.11 or newer from https://www.python.org/downloads/macos/ and run the installer.
Then check it in **Terminal**:

```bash
python3 --version
```

## 2. Install the bot and test it (no MT5 needed yet)

Put the folder in your **home folder** (not Desktop, Documents or Downloads, because macOS
blocks background services there), then run:

```bash
mv ~/Downloads/mt5-bot-mac ~/mt5-bot      # adjust if it's somewhere else
cd ~/mt5-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python -m pytest -q                         # 35 tests should pass
python run.py --backtest 90 --quiet         # 90 simulated days
```

If both work, the bot itself is fine on your Mac.

## 3. Install the bridge and log in to MT5

1. Follow the setup guide on the bridge's project page:
   **https://github.com/bahadirumutiscimen/silicon-metatrader5#readme**
   It offers a **desktop app** or an **`smt5` command-line tool**. Either one works.
2. Inside the bridge's MT5, log in to your broker's **demo account**.
3. Turn on **Algo Trading** in that MT5 (the toolbar button turns green).
4. Open the bridge app's **Bot connection** page and note the **host** and **port** it shows.

The bridge is free for personal, non-commercial use (PolyForm Noncommercial licence).
It's a community project, not made by MetaQuotes or your broker.

## 4. Connect the bot to the bridge

```bash
cp .env.example .env
open -e .env
```

Set `MT5_BRIDGE_HOST` and `MT5_BRIDGE_PORT` to the values from the **Bot connection** page
(the usual default is `127.0.0.1` and `8001`). You can leave the login fields empty, because the bot uses the account
you're already logged in to inside the bridge.

In `config.yaml`, set `symbols` to the names your broker uses, as shown in MT5's Market Watch.

## 5. First live check (demo)

```bash
source .venv/bin/activate
python run.py --once
```

You should see `Connected to MT5 (bridge): account …` and then a status line with your equity.
If you do, start it for real:

```bash
./start_bot.sh                 # runs in this Terminal window, Ctrl+C to stop
```

## 6. Run it 24/7 on the Mac

```bash
./mac/install_autostart.sh     # starts at login, restarts if it crashes
./mac/uninstall_autostart.sh   # to remove it
```

The bridge must also be running. Set the bridge app to open at login: **System Settings →
General → Login Items → +**.

**Keep the Mac awake.** The bot can only trade while the Mac is awake and online:

- Keep it **plugged in**. The bot blocks sleep, but only while it's on the power adapter.
- **System Settings → Battery → Options →** turn on *Prevent automatic sleeping on power adapter when the display is off*.
- **Closing the lid puts a MacBook to sleep** unless it's connected to power *and* an external display. Leave the lid open, or use a display.
- Wi-Fi drops and macOS updates will pause trading. The bot reconnects by itself and alerts you on Telegram.

Your open trades stay protected even while the Mac sleeps, because every trade's stop-loss and take-profit sit **at the broker**. What you lose while it sleeps is new trades and profit management (break-even moves, partial closes, trailing stops).

> For real money, a server is more reliable than a laptop. The **Windows version** of this
> bot runs on a small Windows VPS that never sleeps, and you can manage that VPS from your Mac
> with Microsoft's free *Windows App*.

## Troubleshooting

| You see | Fix |
|---|---|
| `No module named siliconmetatrader5` | `source .venv/bin/activate` then `pip install -r requirements.txt` |
| `MT5 connect attempt … failed` / `Connection refused` | The bridge isn't running, or the host/port in `.env` doesn't match its Bot connection page |
| `mode: demo but this MT5 account is REAL` | You logged in to a real account inside the bridge. Use a demo account, or read "Going live" in the README |
| Orders fail with `retcode=10027` | Algo Trading is off in the bridge's MT5. Turn it on |
| Bot stops after the Mac sleeps | That's expected. The watchdog restarts it when the Mac wakes. Follow "Keep the Mac awake" above |
| See what happened | `tail -f data/bot.log` · `cat data/watchdog.log` |
