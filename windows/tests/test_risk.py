"""Risk manager: multiple trades at once, but never past the limits."""
from datetime import datetime

from bot.models import BUY, SELL, Account, Position, Signal
from bot.risk import RiskManager, money_at_risk, round_volume
from bot.brokers.sim_broker import default_spec

ACC = Account(login=1, balance=10_000, equity=10_000)
T = datetime(2025, 3, 3, 10)


def sig(symbol="EURUSD", side=BUY, entry=1.1000, sl=1.0950):
    tp = entry + (entry - sl) * 2
    return Signal(symbol, side, entry, sl, tp, atr=abs(entry - sl) / 1.5, strength=1, bar_time=T)


def pos(symbol, ticket, side=BUY, entry=1.1, sl=1.095, vol=0.1):
    return Position(ticket, symbol, side, vol, entry, sl, 0, 0, 270419, T)


def rm(cfg):
    return RiskManager(cfg, default_spec)


def test_lot_size_matches_risk_budget(cfg):
    d = rm(cfg).evaluate(sig(), ACC, [], 0)
    assert d.approved
    # 0.5% of 10,000 = 50; stop 50 pips = 500 points × $1 per point per lot → 0.10 lots
    assert d.volume == 0.10
    assert abs(d.risk_money - 50) < 0.01


def test_volume_rounds_down_never_up():
    spec = default_spec("EURUSD")
    assert round_volume(0.129, spec) == 0.12
    assert round_volume(0.1, spec) == 0.1


def test_opens_several_trades_until_max_open(cfg):
    r = rm(cfg)
    symbols = ["EURUSD", "GBPJPY", "AUDCAD", "NZDCHF", "XAUUSD", "USDSGD"]
    open_now = []
    for i, s in enumerate(symbols):
        entry = 2400.0 if s == "XAUUSD" else 1.1
        sl = entry - (8.0 if s == "XAUUSD" else 0.005)
        d = r.evaluate(sig(s, entry=entry, sl=sl), ACC, open_now, i)
        if d.approved:
            open_now.append(pos(s, i, entry=entry, sl=sl, vol=d.volume))
    assert len(open_now) == cfg["risk"]["max_open_trades"] == 5     # several at once, capped at 5


def test_blocks_second_trade_on_same_symbol(cfg):
    d = rm(cfg).evaluate(sig("EURUSD"), ACC, [pos("EURUSD", 1)], 1)
    assert not d.approved and "EURUSD" in d.reason


def test_blocks_too_many_trades_on_one_currency(cfg):
    open_now = [pos("EURUSD", 1), pos("GBPUSD", 2)]                  # USD twice already
    d = rm(cfg).evaluate(sig("AUDUSD"), ACC, open_now, 2)
    assert not d.approved and "USD" in d.reason


def test_total_risk_cap(cfg):
    cfg["risk"]["max_trades_per_currency"] = 10
    cfg["risk"]["max_total_risk_pct"] = 1.0
    open_now = [pos("EURUSD", 1, vol=0.1), pos("GBPUSD", 2, vol=0.1)]   # 50 + 50 = 1% already
    d = rm(cfg).evaluate(sig("AUDUSD"), ACC, open_now, 2)
    assert not d.approved and "total risk" in d.reason


def test_breakeven_trades_free_up_risk_budget(cfg):
    cfg["risk"]["max_trades_per_currency"] = 10
    cfg["risk"]["max_total_risk_pct"] = 1.0
    # both stops moved to entry → zero risk, so a new trade fits
    open_now = [pos("EURUSD", 1, sl=1.1), pos("GBPUSD", 2, sl=1.1)]
    assert rm(cfg).evaluate(sig("AUDUSD"), ACC, open_now, 2).approved


def test_half_risk_in_protect_mode(cfg):
    d = rm(cfg).evaluate(sig(), ACC, [], 0, risk_scale=0.5)
    assert d.volume == 0.05


def test_stop_too_wide_for_small_account(cfg):
    small = Account(login=1, balance=100, equity=100)
    d = rm(cfg).evaluate(sig(sl=1.0500), small, [], 0)     # 0.5% of 100 can't cover a 500-pip stop
    assert not d.approved and "too wide" in d.reason


def test_daily_new_trade_limit(cfg):
    d = rm(cfg).evaluate(sig(), ACC, [], cfg["risk"]["max_new_trades_per_day"])
    assert not d.approved


def test_sell_sizing():
    spec = default_spec("EURUSD")
    assert abs(money_at_risk(0.0050, 0.1, spec) - 50) < 1e-6
    s = sig(side=SELL, entry=1.1, sl=1.105)
    assert s.sl > s.entry
