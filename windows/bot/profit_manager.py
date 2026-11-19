"""Profit manager: watches every open trade and takes profit automatically.

  +1.0R  → stop-loss moved to entry (the trade can no longer lose)
  +1.5R  → half the trade is closed (profit banked)
  after  → the rest is trailed 2 x ATR behind price, so winners can keep running

R = the trade's initial risk (distance from entry to the original stop-loss).
"""
from __future__ import annotations

import logging

from bot.brokers.base import Broker
from bot.daily import PROTECT, TARGET_HIT
from bot.indicators import atr
from bot.models import BUY, Position
from bot.risk import round_volume
from bot.state import Store

log = logging.getLogger(__name__)


class ProfitManager:
    def __init__(self, cfg, broker: Broker, store: Store):
        self.p = cfg["profit"]
        self.entry_tf = cfg["timeframes"]["entry"]
        self.atr_period = int(cfg["strategy"].get("atr_period", 14))
        self.on_target = cfg["daily"]["on_target"]
        self.deviation = int(cfg["risk"].get("deviation_points", 20))
        self.broker = broker
        self.store = store
        self._atr_cache: dict[tuple[str, object], float] = {}

    def _atr(self, symbol: str) -> float:
        bars = self.broker.bars(symbol, self.entry_tf, self.atr_period * 5)
        if len(bars) < self.atr_period + 1:
            return 0.0
        key = (symbol, bars["time"].iloc[-1])
        if key not in self._atr_cache:
            self._atr_cache = {k: v for k, v in self._atr_cache.items() if k[0] != symbol}
            self._atr_cache[key] = float(atr(bars, self.atr_period).iloc[-1])
        return self._atr_cache[key]

    def manage(self, positions: list[Position], day_state: str) -> list[str]:
        actions = []
        for pos in positions:
            try:
                act = self._manage_one(pos, day_state)
                if act:
                    actions.append(act)
            except Exception as exc:
                log.warning("profit manager error on %s: %s", pos.ticket, exc)
        return actions

    def _manage_one(self, pos: Position, day_state: str) -> str | None:
        spec = self.broker.symbol_spec(pos.symbol)
        tick = self.broker.tick(pos.symbol)
        if spec is None or tick is None:
            return None
        rec = self.store.trade(pos.ticket)
        risk = (rec["risk_distance"] if rec else 0) or abs(pos.price_open - pos.sl)
        if risk <= 0:
            return None

        is_buy = pos.side == BUY
        price = tick.bid if is_buy else tick.ask                 # price we'd close at
        gain = (price - pos.price_open) if is_buy else (pos.price_open - price)
        r_multiple = gain / risk
        notes = []

        # ── 1. partial close: bank part of the profit ──────
        partial_done = bool(rec and rec["partial_done"])
        if not partial_done and r_multiple >= self.p["partial_close_at_r"]:
            part = round_volume(pos.volume * self.p["partial_close_fraction"], spec)
            if part >= spec.volume_min and pos.volume - part >= spec.volume_min - 1e-9:
                res = self.broker.close(pos, part, self.deviation)
                if res.ok:
                    notes.append(f"closed {part} lots at +{r_multiple:.1f}R")
                    pos.volume = round(pos.volume - part, 8)
                else:
                    log.warning("partial close failed %s: %s", pos.ticket, res.message)
                    return None
            else:
                notes.append("too small to split, trailing whole trade")
            self.store.mark_partial(pos.ticket)
            partial_done = True

        # ── 2. work out the best stop-loss ─────────────────
        new_sl = pos.sl
        tighter = (lambda a, b: a > b) if is_buy else (lambda a, b: (b == 0) or a < b)

        be_trigger = self.p["breakeven_at_r"]
        if day_state == PROTECT:
            be_trigger = min(be_trigger, 0.5)
        if r_multiple >= be_trigger:
            buffer = tick.spread
            be = pos.price_open + buffer if is_buy else pos.price_open - buffer
            if tighter(be, new_sl):
                new_sl = be

        trail_mult = None
        if partial_done:
            trail_mult = self.p["trail_atr"]
        if day_state == TARGET_HIT and self.on_target == "trail_tight" and gain > 0:
            trail_mult = 1.0
        if trail_mult:
            a = self._atr(pos.symbol)
            if a > 0:
                trail = price - trail_mult * a if is_buy else price + trail_mult * a
                if tighter(trail, new_sl):
                    new_sl = trail

        # ── 3. send the change (only if it really tightens) ─
        min_gap = max(spec.stops_level, 1) * spec.point
        too_close = (price - new_sl < min_gap) if is_buy else (new_sl - price < min_gap)
        if new_sl != pos.sl and abs(new_sl - pos.sl) >= spec.point and not too_close:
            new_sl = round(new_sl, spec.digits)
            res = self.broker.modify_sl_tp(pos, new_sl, pos.tp)
            if res.ok:
                notes.append(f"stop → {new_sl}")
                pos.sl = new_sl

        if notes:
            msg = f"{pos.symbol} #{pos.ticket}: " + "; ".join(notes)
            log.info(msg)
            return msg
        return None
