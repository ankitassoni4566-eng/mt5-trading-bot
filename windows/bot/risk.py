"""Risk manager: the gate every trade must pass. It decides how many trades may be
open at once, sizes each one, and blocks anything that breaks a limit."""
from __future__ import annotations

import math

from bot.models import BUY, Account, Decision, Position, Signal, SymbolSpec


def round_volume(volume: float, spec: SymbolSpec) -> float:
    """Round DOWN to the broker's lot step, so we never risk more than planned."""
    steps = math.floor(volume / spec.volume_step + 1e-9)
    return round(min(steps * spec.volume_step, spec.volume_max), 8)


def money_at_risk(distance: float, volume: float, spec: SymbolSpec) -> float:
    return distance / spec.tick_size * spec.tick_value * volume


def position_risk(pos: Position, spec: SymbolSpec, fallback: float) -> float:
    """Money lost if this position hits its stop now. Zero once the stop is at/after entry."""
    if not pos.sl:
        return fallback
    dist = (pos.price_open - pos.sl) if pos.side == BUY else (pos.sl - pos.price_open)
    return max(0.0, money_at_risk(dist, pos.volume, spec))


class RiskManager:
    def __init__(self, cfg, spec_lookup):
        self.r = cfg["risk"]
        self.spec_lookup = spec_lookup        # symbol → SymbolSpec

    def _currencies(self, symbol: str) -> set[str]:
        spec = self.spec_lookup(symbol)
        base, quote = spec.base_quote if spec else (symbol, "")
        return {c for c in (base, quote) if c}

    def evaluate(self, sig: Signal, account: Account, open_positions: list[Position],
                 trades_today: int, risk_scale: float = 1.0) -> Decision:
        r = self.r
        spec = self.spec_lookup(sig.symbol)
        if spec is None:
            return Decision(False, reason="unknown symbol")

        # 1. how many trades at once
        if len(open_positions) >= r["max_open_trades"]:
            return Decision(False, reason=f"already {len(open_positions)} trades open (max {r['max_open_trades']})")
        if trades_today >= r["max_new_trades_per_day"]:
            return Decision(False, reason="daily new-trade limit reached")

        # 2. spread the trades out: per market and per currency
        same_symbol = sum(1 for p in open_positions if p.symbol == sig.symbol)
        if same_symbol >= r["max_trades_per_symbol"]:
            return Decision(False, reason=f"already {same_symbol} trade(s) on {sig.symbol}")
        for cur in self._currencies(sig.symbol):
            n = sum(1 for p in open_positions if cur in self._currencies(p.symbol))
            if n >= r["max_trades_per_currency"]:
                return Decision(False, reason=f"already {n} trades on {cur}")

        # 3. stop distance sanity
        distance = abs(sig.entry - sig.sl)
        if distance <= 0:
            return Decision(False, reason="stop-loss on the wrong side")
        if spec.stops_level and distance < spec.stops_level * spec.point:
            return Decision(False, reason="stop-loss closer than broker minimum")

        # 4. size the trade from the risk budget
        risk_budget = account.equity * r["risk_per_trade_pct"] / 100 * risk_scale
        per_lot = money_at_risk(distance, 1.0, spec)
        volume = round_volume(risk_budget / per_lot, spec)
        if volume < spec.volume_min:
            return Decision(False, reason=f"stop too wide for account size (needs {spec.volume_min} lots min)")
        risk_money = money_at_risk(distance, volume, spec)

        # 5. all trades together must stay under the total risk cap
        fallback = account.equity * r["risk_per_trade_pct"] / 100
        open_risk = sum(position_risk(p, self.spec_lookup(p.symbol), fallback) for p in open_positions
                        if self.spec_lookup(p.symbol))
        cap = account.equity * r["max_total_risk_pct"] / 100
        if open_risk + risk_money > cap + 1e-9:
            return Decision(False, reason=f"total risk would be {open_risk + risk_money:.2f} > cap {cap:.2f}")

        return Decision(True, volume=volume, risk_money=risk_money,
                        reason=f"{volume} lots, risking {risk_money:.2f} ({risk_money / account.equity:.2%})")
