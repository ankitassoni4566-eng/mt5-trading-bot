"""Profit taking and the daily target rules."""
import pytest

from bot.daily import GIVEBACK, LOSS_HIT, NORMAL, PROTECT, TARGET_HIT, DailyGuard
from bot.models import BUY, SELL
from bot.profit_manager import ProfitManager

MAGIC = 270419


def open_trade(sim, store, symbol="EURUSD", side=BUY, volume=0.10, risk=0.0050):
    t = sim.tick(symbol)
    entry = t.ask if side == BUY else t.bid
    sl = entry - risk if side == BUY else entry + risk
    tp = entry + 3 * risk if side == BUY else entry - 3 * risk
    res = sim.market_order(symbol, side, volume, sl, tp, MAGIC, "test", 20)
    assert res.ok
    store.add_trade(res.ticket, "sig", symbol, side, volume, sim.now(), res.price, sl, tp, risk, 50)
    return res.ticket


def move_price(sim, symbol, new_bid):
    """Force the current bar's open (the sim's live price) to a new value."""
    df = sim.data[symbol]
    df.loc[sim.i, ["open", "high", "low", "close"]] = new_bid


def pm_for(cfg, sim, store):
    return ProfitManager(cfg, sim, store)


# ── profit manager ──────────────────────────────────────
def test_moves_stop_to_breakeven_at_1R(cfg, sim, store):
    ticket = open_trade(sim, store)
    entry = sim.positions(MAGIC)[0].price_open
    move_price(sim, "EURUSD", entry + 0.0051)                 # a bit over +1R
    pm_for(cfg, sim, store).manage(sim.positions(MAGIC), NORMAL)
    p = sim.positions(MAGIC)[0]
    assert p.ticket == ticket and p.sl >= entry               # can no longer lose
    assert p.volume == 0.10                                    # no partial yet


def test_partial_close_at_1_5R_then_trailing(cfg, sim, store):
    open_trade(sim, store)
    entry = sim.positions(MAGIC)[0].price_open
    move_price(sim, "EURUSD", entry + 0.0080)                 # +1.6R
    pm = pm_for(cfg, sim, store)
    pm.manage(sim.positions(MAGIC), NORMAL)
    p = sim.positions(MAGIC)[0]
    assert p.volume == pytest.approx(0.05)                     # half banked
    assert sim.balance > 10_000                                # realized profit
    first_sl = p.sl
    move_price(sim, "EURUSD", entry + 0.0200)                 # runs further
    pm.manage(sim.positions(MAGIC), NORMAL)
    assert sim.positions(MAGIC)[0].sl > first_sl               # stop trailed up
    move_price(sim, "EURUSD", entry + 0.0150)                 # pulls back
    before = sim.positions(MAGIC)[0].sl
    pm.manage(sim.positions(MAGIC), NORMAL)
    assert sim.positions(MAGIC)[0].sl == before                # trailing never loosens


def test_partial_happens_only_once(cfg, sim, store):
    open_trade(sim, store)
    entry = sim.positions(MAGIC)[0].price_open
    move_price(sim, "EURUSD", entry + 0.0080)
    pm = pm_for(cfg, sim, store)
    pm.manage(sim.positions(MAGIC), NORMAL)
    pm.manage(sim.positions(MAGIC), NORMAL)
    assert sim.positions(MAGIC)[0].volume == pytest.approx(0.05)


def test_sell_side_breakeven(cfg, sim, store):
    open_trade(sim, store, side=SELL)
    p = sim.positions(MAGIC)[0]
    move_price(sim, "EURUSD", p.price_open - 0.0060)
    pm_for(cfg, sim, store).manage(sim.positions(MAGIC), NORMAL)
    assert sim.positions(MAGIC)[0].sl <= p.price_open


def test_protect_mode_moves_breakeven_sooner(cfg, sim, store):
    open_trade(sim, store)
    entry = sim.positions(MAGIC)[0].price_open
    move_price(sim, "EURUSD", entry + 0.0030)                 # +0.6R
    pm_for(cfg, sim, store).manage(sim.positions(MAGIC), NORMAL)
    assert sim.positions(MAGIC)[0].sl < entry                  # normal: not yet
    pm_for(cfg, sim, store).manage(sim.positions(MAGIC), PROTECT)
    assert sim.positions(MAGIC)[0].sl >= entry                 # protect: yes


def test_several_trades_managed_independently(cfg, sim, store):
    open_trade(sim, store, "EURUSD")
    open_trade(sim, store, "GBPUSD")
    open_trade(sim, store, "AUDUSD")
    assert len(sim.positions(MAGIC)) == 3
    e = {p.symbol: p.price_open for p in sim.positions(MAGIC)}
    move_price(sim, "EURUSD", e["EURUSD"] + 0.0080)            # winner → partial
    move_price(sim, "GBPUSD", e["GBPUSD"] + 0.0055)            # +1R → break-even
    pm_for(cfg, sim, store).manage(sim.positions(MAGIC), NORMAL)
    by = {p.symbol: p for p in sim.positions(MAGIC)}
    assert by["EURUSD"].volume == pytest.approx(0.05)
    assert by["GBPUSD"].sl >= e["GBPUSD"] and by["GBPUSD"].volume == 0.10
    assert by["AUDUSD"].sl < e["AUDUSD"]                        # untouched


# ── daily target ────────────────────────────────────────
def test_daily_states(cfg):
    g = DailyGuard(cfg)                       # target 1%, loss 2%, protect at 0.5%
    assert g.next_state(NORMAL, 0.2, 0.2) == NORMAL
    assert g.next_state(NORMAL, 0.6, 0.6) == PROTECT
    assert g.next_state(PROTECT, 0.3, 0.6) == PROTECT          # protect sticks for the day
    assert g.next_state(PROTECT, 1.05, 1.05) == TARGET_HIT
    assert g.next_state(NORMAL, -2.1, 0.0) == LOSS_HIT
    assert g.next_state(PROTECT, 0.35, 0.85) == GIVEBACK       # was +0.85%, fell to +0.35%


def test_locked_states_stay_locked(cfg):
    g = DailyGuard(cfg)
    for s in (TARGET_HIT, LOSS_HIT, GIVEBACK):
        assert g.next_state(s, 0.0, 1.0) == s
        assert not g.can_open_trades(s)
    assert g.can_open_trades(NORMAL) and g.can_open_trades(PROTECT)


def test_close_all_rules(cfg):
    g = DailyGuard(cfg)
    assert g.should_close_all(LOSS_HIT) and g.should_close_all(GIVEBACK)
    assert g.should_close_all(TARGET_HIT)                      # on_target: close_all
    cfg["daily"]["on_target"] = "trail_tight"
    assert not DailyGuard(cfg).should_close_all(TARGET_HIT)
    assert g.risk_scale(PROTECT) == 0.5
