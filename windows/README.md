# MT5 Auto-Trading Bot — Windows version

![tests](../../../actions/workflows/ci.yml/badge.svg)

> On a Mac? Use the **Mac version** in [`../mac`](../mac). The trading logic is identical; only the MT5 connection and the startup scripts differ.

A Python bot for **MetaTrader 5** that:

1. **Takes trades by itself.** It scans your markets every 30 seconds and opens buy or sell trades when a strong trend pulls back.
2. **Takes out profit automatically.** It moves the stop to break-even, banks half the trade, and then trails the rest.
3. **Holds multiple trades at the same time.** By default that's up to 5 at once, spread across markets and currencies so one bad move can't hit them all.

It also keeps **daily accounts** and has a **daily profit target with brakes**. Once the target is hit, it banks the profit and stops for the day. A daily loss limit does the same on bad days. It's built to run **24/7** on a Windows VPS with a watchdog that restarts it automatically.

```
 scanner ─► trend reader ─► strategy ─► RISK MANAGER ─► executor ─► MT5
                                         (max trades,               │
                                          lot size,                 ▼
                                          total risk)        profit manager ─► daily accounts ─► Telegram
                                                              (break-even,
 watchdog: restarts the bot if it crashes or freezes           partial close, trailing)
```

> ⚠️ This is educational software, not financial advice. Leveraged trading can lose money fast.
> It starts in **demo mode** and refuses to touch a real account until you change two settings on purpose.

---

## 1. Try it in 2 minutes (no MT5 needed yet)

Install Python 3.11+ from python.org (tick **Add Python to PATH**), then in **Command Prompt** inside this folder:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python -m pytest -q                  & REM 35 safety + logic tests
python run.py --backtest 90 --quiet  & REM 90 simulated days on generated prices
```

The backtest prints how many trades were taken, the most trades open at the same time, the drawdown, and how often the daily target or loss limit was hit.
**Generated prices only prove the machinery works. They are not proof of profit.** To test on real history, export H1 data from MT5 to CSV (one file per symbol, with columns `time,open,high,low,close,volume`) and run:

```bash
python run.py --backtest 365 --data path/to/csv_folder
```

## 2. On GitHub

This folder is part of the **mt5-trading-bot** repository. On every push, GitHub runs all the tests and a 90-day backtest for both versions. See the **Actions** tab; a green tick means nothing is broken.

`.env`, which holds your passwords, is in `.gitignore`, so it never gets uploaded.

## 3. Connect to MT5 (Windows)

1. Install MT5 from your broker and log in to a **demo account**.
2. In MT5, go to **Tools → Options → Expert Advisors** and tick **Allow algorithmic trading**.
3. Copy `.env.example` to `.env`. You can leave the login fields empty to use the account that's already open in MT5.
4. In `config.yaml`, set `symbols` to the names your broker uses (check Market Watch, because names like `XAUUSD`/`GOLD` and `US30`/`DJ30` vary between brokers).
5. Run one check, then let it run:
   ```bat
   python run.py --once
   python run.py
   ```
   You'll see the bot's trades in MT5 with the comment `bot …` and magic number `270419`. The bot never touches trades you place by hand.

## 4. The settings you'll care about (`config.yaml`)

| Setting | Default | Meaning |
|---|---|---|
| `risk.max_open_trades` | 5 | How many trades can be open at the same time |
| `risk.max_trades_per_symbol` | 1 | Trades per market |
| `risk.max_trades_per_currency` | 2 | E.g. at most 2 trades involving USD |
| `risk.risk_per_trade_pct` | 0.5 | % of equity lost if one trade hits its stop |
| `risk.max_total_risk_pct` | 2.5 | All open trades together never risk more than this |
| `profit.breakeven_at_r` | 1.0 | Move the stop to entry when profit equals the trade's risk |
| `profit.partial_close_at_r` | 1.5 | Bank part of the trade here… |
| `profit.partial_close_fraction` | 0.5 | …this much of it (half) |
| `profit.trail_atr` | 2.0 | Then trail the stop 2 × ATR behind the price |
| `daily.target_pct` | 1.0 | Stop for the day at +1% |
| `daily.max_loss_pct` | 2.0 | Halt for the day at −2% |
| `daily.on_target` | close_all | Or `trail_tight`, which keeps winners open with a tight stop |
| `symbols` | 10 majors + gold | Or `all` to scan every symbol your broker offers |

**R** is the trade's risk: the distance from entry to the stop-loss. At +1R, the trade has made as much as it could have lost.

**How the day works:** at +0.5% the bot enters *protect* mode, where new trades use half the risk and stops move to break-even sooner. At +1% it closes everything and stops until tomorrow. If the day reaches +0.8% and then falls back to +0.4%, it also stops, to keep what's left. At −2% it closes everything and halts.

## 5. Daily accounts

- `data/reports/daily_summary.csv` has one row per day: start and end equity, change, realized P/L, trades opened and closed, wins, losses, and the day's status.
- `data/reports/YYYY-MM-DD.csv` lists every closing deal of that day.
- `data/bot.db` is a SQLite file with every signal, trade and day (you can open it with *DB Browser for SQLite*).
- If Telegram is on, you get a report message every midnight (broker server time).

## 6. Telegram alerts & remote control

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Message **@userinfobot** to get your chat ID.
3. Put both in `.env`, then set `telegram.enabled: true` in `config.yaml`.

| Command | What it does |
|---|---|
| `/status` or `/today` | Equity, today's P/L vs target, open trades |
| `/positions` | Every open trade with its P/L |
| `/trades` | The last 10 trades |
| `/pause` | No new trades; open trades keep their profit rules |
| `/resume` | Start trading again |
| `/stop` | **Kill switch:** closes everything and pauses |

## 7. Run it 24/7

See [`watchdog/WINDOWS_SERVICE.md`](watchdog/WINDOWS_SERVICE.md). In short: use a Windows VPS, make MT5 start at boot, and run `start_bot.bat` (the watchdog) as a service or a startup task. It restarts the bot if it crashes or freezes and sends you a Telegram alert when it does.

If everything goes down anyway, your trades are still protected: every trade is sent with its stop-loss and take-profit **at the broker**.

## 8. Safety built in

| Protection | How |
|---|---|
| Demo by default | `mode: demo` refuses to run on a real account |
| Two-key live switch | Needs `mode: live` **and** `LIVE_TRADING_CONFIRM=YES_TRADE_REAL_MONEY` |
| No duplicate orders | Each signal is saved before sending, so a restart can't send it twice |
| Closed candles only | Signals can't flip while a candle is still forming |
| Lot size rounds down | Never risks more than planned |
| Spread filter | Skips markets where the spread is too large compared with normal movement |
| Restart-safe days | Today's start equity and status survive a restart |

## 9. Going live checklist

- [ ] At least 4–8 weeks on demo, running 24/7 on the VPS
- [ ] Demo results are close to the backtest on real history
- [ ] Telegram works and you've tested `/stop`
- [ ] Small settings to start: `risk_per_trade_pct: 0.25`, `max_open_trades: 3`
- [ ] `mode: live` plus the confirm phrase in `.env`

## Project layout

```
run.py                     start here (live, --once, --backtest)
config.yaml                every setting
bot/engine.py              main loop + Telegram commands
bot/scanner.py             scans all symbols, spread filter
bot/trend.py               trend reader (EMA 50/200 + ADX)
bot/strategies/            trend_pullback.py (add your own here)
bot/risk.py                max trades, lot size, total risk cap
bot/executor.py            sends orders once
bot/profit_manager.py      break-even, partial close, trailing
bot/daily.py               daily target / loss limit / giveback
bot/ledger.py              daily accounts + reports
bot/state.py               SQLite storage
bot/brokers/               mt5_broker.py (real), sim_broker.py (tests/backtests)
watchdog/                  auto-restart + Windows service guide
backtest/                  backtest runner
tests/                     35 tests, run on GitHub automatically
```

## Known limits

- The strategy is a reasonable example, not a proven money-maker. Backtest it on **real** history before trusting it.
- There's no news filter yet, so the bot can trade through big announcements. You can `/pause` before major news.
- Sizing assumes your account currency is the one MT5 reports tick values in. That's normal, but check the first demo trades' lot sizes.
- On *netting* accounts (one position per symbol), keep `max_trades_per_symbol: 1`.
