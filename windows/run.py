"""Start here.

  python run.py --backtest 120   simulate 120 days on generated data (works anywhere)
  python run.py --once           one live cycle on MT5, then exit (good first test)
  python run.py                  run on MT5 forever (Windows, MT5 terminal open)
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.config import live_trading_confirmed, load_config


def setup_logging(log_file: str | None) -> None:
    for stream in (sys.stdout, sys.stderr):      # Windows consoles: don't choke on emoji
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        handlers=handlers)


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 auto-trading bot")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--backtest", type=int, metavar="DAYS", help="simulate DAYS days instead of trading")
    ap.add_argument("--data", help="folder of H1 CSV files (one per symbol) for the backtest")
    ap.add_argument("--seed", type=int, default=7, help="random seed for generated backtest data")
    ap.add_argument("--once", action="store_true", help="run one cycle on MT5 and exit")
    ap.add_argument("--quiet", action="store_true", help="backtest: only print the summary")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.backtest:
        setup_logging(None)
        if args.quiet:
            logging.getLogger().setLevel(logging.WARNING)
        from backtest.runner import run_backtest
        result = run_backtest(cfg, days=args.backtest, seed=args.seed, data_folder=args.data)
        print(result.summary())
        return 0

    storage = cfg.get("storage", {})
    setup_logging(storage.get("log_file", "data/bot.log"))
    log = logging.getLogger("run")

    from bot.brokers.mt5_broker import MT5Broker
    from bot.engine import Engine
    from bot.notifier import Notifier
    from bot.state import Store

    try:
        broker = MT5Broker()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    if not broker.connect():
        log.error("Could not connect to MT5. Is the terminal open and logged in? Check .env")
        return 2

    account = broker.account()
    if cfg["mode"] == "demo" and not account.is_demo:
        log.error("config says mode: demo but this MT5 account is REAL. Refusing to trade.")
        return 3
    if cfg["mode"] == "live" and not live_trading_confirmed():
        log.error("mode: live needs LIVE_TRADING_CONFIRM=YES_TRADE_REAL_MONEY in .env. Refusing to trade.")
        return 3

    notifier = Notifier(cfg.get("telegram", {}).get("enabled", False))
    engine = Engine(cfg, broker, Store(storage.get("db_path", "data/bot.db")), notifier)
    log.info("Account %s · equity %.2f %s · %s", account.login, account.equity, account.currency,
             "DEMO" if account.is_demo else "REAL")
    try:
        if args.once:
            engine.cycle()
            print(engine.status_text())
        else:
            engine.run_forever()
    except KeyboardInterrupt:
        log.info("Stopped by user (open trades keep their stop-loss and take-profit at the broker).")
    finally:
        broker.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
