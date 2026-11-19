"""Backtest: runs the real engine against the simulator, bar by bar.

Uses generated price data by default, or your own MT5 history exported as CSV
(one file per symbol in a folder: time,open,high,low,close,volume on H1).
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from bot.brokers.sim_broker import SimBroker, generate_h1
from bot.engine import Engine
from bot.state import Store

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    start_equity: float
    end_equity: float
    days: int
    trades: int
    wins: int
    losses: int
    max_concurrent: int
    max_drawdown_pct: float
    target_days: int
    loss_days: int
    daily: pd.DataFrame

    def summary(self) -> str:
        n = self.wins + self.losses
        ret = (self.end_equity / self.start_equity - 1) * 100
        return "\n".join([
            "──────── Backtest result ────────",
            f"Days simulated      : {self.days}",
            f"Start → end equity  : {self.start_equity:,.2f} → {self.end_equity:,.2f} ({ret:+.2f}%)",
            f"Closed trades       : {n}  (wins {self.wins}, losses {self.losses}, "
            f"win rate {self.wins / n:.0%})" if n else "Closed trades       : 0",
            f"Most trades at once : {self.max_concurrent}",
            f"Max drawdown        : {self.max_drawdown_pct:.2f}%",
            f"Days target hit     : {self.target_days}",
            f"Days loss limit hit : {self.loss_days}",
            "─────────────────────────────────",
            "Generated data is for checking the machinery, not proof of profit.",
        ])


def load_csv_folder(folder: str | Path) -> dict[str, pd.DataFrame]:
    data = {}
    for f in sorted(Path(folder).glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["time"])
        data[f.stem.upper()] = df[["time", "open", "high", "low", "close", "volume"]]
    # align on common timestamps so every symbol has the same bars
    common = None
    for df in data.values():
        common = set(df["time"]) if common is None else common & set(df["time"])
    return {s: df[df["time"].isin(common)].sort_values("time").reset_index(drop=True) for s, df in data.items()}


def run_backtest(cfg, days: int = 120, seed: int = 7, data_folder: str | None = None,
                 balance: float = 10_000.0, warmup_bars: int = 1300) -> BacktestResult:
    if data_folder:
        data = load_csv_folder(data_folder)
        cfg = dict(cfg, symbols=list(data))
    else:
        symbols = cfg["symbols"] if isinstance(cfg["symbols"], list) else ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
        n = warmup_bars + days * 24 + 2
        data = {s: generate_h1(s, n, seed + i) for i, s in enumerate(symbols)}

    broker = SimBroker(data, start_index=warmup_bars, balance=balance)
    tmp = tempfile.mkdtemp(prefix="bt_")
    cfg = dict(cfg)
    cfg["storage"] = {"db_path": f"{tmp}/bt.db", "reports_dir": f"{tmp}/reports",
                      "heartbeat_file": f"{tmp}/hb.txt"}
    store = Store(cfg["storage"]["db_path"])
    engine = Engine(cfg, broker, store, write_heartbeat=False)

    equity_curve = []
    while not broker.finished:
        engine.cycle()
        equity_curve.append(broker.account().equity)
        broker.step()
    engine.close_all("backtest end")
    engine.cycle()  # record the last closes

    eq = pd.Series(equity_curve or [balance])
    drawdown = ((eq.cummax() - eq) / eq.cummax()).max() * 100
    trades = store._all("SELECT * FROM trades WHERE closed=1")
    wins = sum(1 for t in trades if (t["profit"] or 0) > 0)
    daily = pd.DataFrame([dict(r) for r in store.days(10_000)])
    states = daily["state"].value_counts() if len(daily) else {}
    return BacktestResult(
        start_equity=balance, end_equity=broker.account().equity, days=len(daily),
        trades=len(store.all_trade_tickets()), wins=wins, losses=len(trades) - wins,
        max_concurrent=engine.max_concurrent, max_drawdown_pct=float(drawdown),
        target_days=int(states.get("TARGET_HIT", 0)) if len(daily) else 0,
        loss_days=int(states.get("LOSS_HIT", 0)) if len(daily) else 0,
        daily=daily,
    )
