"""Whole-bot tests on the simulator: duplicates, daily target, kill switch, accounts."""
from datetime import datetime

import pytest

from bot.brokers.sim_broker import SimBroker, generate_h1
from bot.engine import Engine
from bot.executor import Executor
from bot.indicators import adx, atr, ema, rsi
from bot.models import BUY, Decision, Signal
from bot.state import Store

MAGIC = 270419


def test_indicators_basic(sim):
    df = sim.bars("EURUSD", "H1", 200)
    assert ema(df["close"], 10).iloc[-1] == pytest.approx(df["close"].iloc[-1])
    r = rsi(generate_h1("EURUSD", 300, 1)["close"])
    assert r.between(0, 100).all()
    assert atr(df).iloc[-1] > 0
    assert adx(generate_h1("EURUSD", 300, 2)).dropna().between(0, 100).all()


def test_sim_gives_closed_bars_only(sim):
    bars = sim.bars("EURUSD", "H1", 50)
    assert bars["time"].iloc[-1] < sim.now()
    h4 = sim.bars("EURUSD", "H4", 10)
    assert (h4["time"].iloc[-1] + __import__("pandas").Timedelta(hours=4)) <= sim.now()


def test_same_signal_never_sent_twice(cfg, sim, store):
    ex = Executor(sim, store, MAGIC, 20)
    t = sim.tick("EURUSD")
    s = Signal("EURUSD", BUY, t.ask, t.ask - 0.005, t.ask + 0.01, 0.003, 1, datetime(2025, 1, 18, 11))
    assert ex.execute(s, Decision(True, volume=0.1, risk_money=50)).ok
    second = ex.execute(s, Decision(True, volume=0.1, risk_money=50))     # e.g. after a restart
    assert not second.ok and "duplicate" in second.message
    assert len(sim.positions(MAGIC)) == 1


def test_daily_target_closes_all_and_stops(cfg, sim, store):
    eng = Engine(cfg, sim, store, write_heartbeat=False)
    eng.cycle()                                                   # starts the day at 10,000
    t = sim.tick("EURUSD")
    sim.market_order("EURUSD", BUY, 1.0, t.ask - 0.005, t.ask + 0.05, MAGIC, "x", 20)
    sim.data["EURUSD"].loc[sim.i, ["open", "high", "low", "close"]] = t.ask + 0.0012   # +$120 = +1.2%
    eng.cycle()
    assert sim.positions(MAGIC) == []                             # profit taken
    assert store.get_day(eng.day)["state"] == "TARGET_HIT"
    assert sim.balance > 10_100


def test_loss_limit_closes_all(cfg, sim, store):
    eng = Engine(cfg, sim, store, write_heartbeat=False)
    eng.cycle()
    t = sim.tick("EURUSD")
    sim.market_order("EURUSD", BUY, 2.0, t.ask - 0.05, t.ask + 0.05, MAGIC, "x", 20)
    sim.data["EURUSD"].loc[sim.i, ["open", "high", "low", "close"]] = t.ask - 0.0011   # -$220+ = -2.2%
    eng.cycle()
    assert sim.positions(MAGIC) == []
    assert store.get_day(eng.day)["state"] == "LOSS_HIT"


def test_kill_switch_command(cfg, sim, store):
    class FakeTelegram:
        def __init__(self):
            self.sent, self.queue = [], ["/stop"]

        def send(self, text):
            self.sent.append(text)

        def commands(self):
            q, self.queue = self.queue, []
            return q

    tg = FakeTelegram()
    eng = Engine(cfg, sim, store, notifier=tg, write_heartbeat=False)
    t = sim.tick("EURUSD")
    sim.market_order("EURUSD", BUY, 0.1, t.ask - 0.005, t.ask + 0.01, MAGIC, "x", 20)
    eng.cycle()
    assert sim.positions(MAGIC) == []
    assert store.get_flag("paused") == "1"
    assert any("Kill switch" in m for m in tg.sent)


def test_restart_keeps_start_equity(cfg, sim, tmp_path):
    db = tmp_path / "bot.db"
    eng = Engine(cfg, sim, Store(db), write_heartbeat=False)
    eng.cycle()
    sim.balance += 500                                          # equity changes during the day
    eng2 = Engine(cfg, sim, Store(db), write_heartbeat=False)   # bot restarted
    eng2.cycle()
    assert Store(db).get_day(eng2.day)["start_equity"] == 10_000


def test_backtest_holds_multiple_trades_and_writes_daily_accounts(cfg, tmp_path):
    symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "XAUUSD", "NZDUSD"]
    data = {s: generate_h1(s, 1300 + 30 * 24, 11 + i) for i, s in enumerate(symbols)}
    sim = SimBroker(data, start_index=1300)
    cfg = dict(cfg, symbols=symbols)
    cfg["storage"] = {"reports_dir": str(tmp_path / "reports")}
    store = Store(tmp_path / "bt.db")
    eng = Engine(cfg, sim, store, write_heartbeat=False)
    while not sim.finished:
        eng.cycle()
        sim.step()
    assert eng.max_concurrent >= 2                              # several trades at the same time
    assert eng.max_concurrent <= cfg["risk"]["max_open_trades"]
    assert len(store.all_trade_tickets()) > 5
    assert (tmp_path / "reports" / "daily_summary.csv").exists()
    days = store.days(100)
    assert len(days) >= 29
    assert all(d["new_trades"] <= cfg["risk"]["max_new_trades_per_day"] for d in days)
