"""Simulated broker for tests and backtests. Runs anywhere (Linux, GitHub Actions).

It replays H1 bars. At each step the bot sees closed bars only, orders fill at the
next bar's open (plus spread), and stop-loss / take-profit are checked against each
bar's high/low. If both could have hit in the same bar, the stop-loss is assumed
first (the pessimistic choice).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from bot.brokers.base import Broker
from bot.models import BUY, SELL, Account, Deal, OrderResult, Position, SymbolSpec, Tick

TF_HOURS = {"H1": 1, "H4": 4, "D1": 24}


# ── synthetic market data ──────────────────────────────────
def default_spec(symbol: str) -> SymbolSpec:
    s = symbol.upper()
    if s.startswith("XAU"):
        return SymbolSpec(s, 0.01, 2, 0.01, 1.0, 0.01, 100.0, 0.01, 0)
    if s.endswith("JPY"):
        return SymbolSpec(s, 0.001, 3, 0.001, 0.67, 0.01, 100.0, 0.01, 0)
    return SymbolSpec(s, 0.00001, 5, 0.00001, 1.0, 0.01, 100.0, 0.01, 0)


def start_price(symbol: str) -> float:
    s = symbol.upper()
    if s.startswith("XAU"):
        return 2400.0
    if s.endswith("JPY"):
        return 150.0
    return 1.10


def generate_h1(symbol: str, n_bars: int, seed: int, start: datetime | None = None) -> pd.DataFrame:
    """Random walk with trending regimes, so trend strategies have something to find."""
    rng = np.random.default_rng(seed)
    price = start_price(symbol)
    hourly_vol = 0.0012          # ~0.12% per hour
    drift, regime_left = 0.0, 0
    rows = []
    t = start or datetime(2025, 1, 6)
    for _ in range(n_bars):
        if regime_left <= 0:
            drift = rng.choice([-1, 0, 0, 1]) * rng.uniform(0.00008, 0.00025)
            regime_left = int(rng.integers(80, 400))
        regime_left -= 1
        steps = price * np.exp(np.cumsum(drift / 4 + rng.normal(0, hourly_vol / 2, 4)))
        o, c = price, steps[-1]
        h = max(o, steps.max()) * (1 + abs(rng.normal(0, hourly_vol / 4)))
        lo = min(o, steps.min()) * (1 - abs(rng.normal(0, hourly_vol / 4)))
        rows.append((t, o, h, lo, c, int(rng.integers(100, 1000))))
        price = c
        t += timedelta(hours=1)
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


# ── the broker ─────────────────────────────────────────────
class SimBroker(Broker):
    def __init__(self, data: dict[str, pd.DataFrame], start_index: int = 1000,
                 balance: float = 10_000.0, spread_points: dict[str, int] | None = None,
                 specs: dict[str, SymbolSpec] | None = None, commission_per_lot: float = 0.0):
        self.data = {s: df.reset_index(drop=True) for s, df in data.items()}
        lengths = {len(df) for df in self.data.values()}
        if len(lengths) != 1:
            raise ValueError("all symbols must have the same number of bars")
        self.n = lengths.pop()
        self.i = start_index
        self.specs = specs or {s: default_spec(s) for s in self.data}
        self.spread_points = spread_points or {s: (30 if s.startswith("XAU") else 12) for s in self.data}
        self.commission_per_lot = commission_per_lot
        self.balance = balance
        self._positions: dict[int, Position] = {}
        self._deals: list[Deal] = []
        self._next_ticket = 1000
        self.connected = True
        self.is_demo = True
        self._higher: dict[tuple[str, str], pd.DataFrame] = {}
        self._times = self.data[next(iter(self.data))]["time"].values

    # ── clock ──────────────────────────────────────────────
    @property
    def finished(self) -> bool:
        return self.i >= self.n - 1

    def now(self) -> datetime:
        return pd.Timestamp(self._times[self.i]).to_pydatetime()

    def step(self) -> None:
        """Play out the current bar (SL/TP hits), then move to the next bar."""
        for pos in list(self._positions.values()):
            self._check_stops(pos, self.i)
        self.i += 1

    # ── connection ─────────────────────────────────────────
    def connect(self) -> bool:
        self.connected = True
        return True

    def is_connected(self) -> bool:
        return self.connected

    # ── market data ────────────────────────────────────────
    def all_symbols(self) -> list[str]:
        return list(self.data)

    def symbol_spec(self, symbol: str) -> SymbolSpec | None:
        return self.specs.get(symbol)

    def tick(self, symbol: str) -> Tick | None:
        if symbol not in self.data:
            return None
        bid = float(self.data[symbol].at[self.i, "open"])
        spread = self.spread_points[symbol] * self.specs[symbol].point
        return Tick(bid=bid, ask=bid + spread, time=self.now())

    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        df = self.data[symbol]
        if timeframe == "H1":
            return df.iloc[max(0, self.i - count):self.i].reset_index(drop=True)
        hours = TF_HOURS[timeframe]
        key = (symbol, timeframe)
        if key not in self._higher:
            agg = df.resample(f"{hours}h", on="time").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna().reset_index()
            agg["end"] = agg["time"] + pd.Timedelta(hours=hours)
            self._higher[key] = agg
        agg = self._higher[key]
        cutoff = np.searchsorted(agg["end"].values, np.datetime64(self.now()), side="right")
        out = agg.iloc[max(0, cutoff - count):cutoff]
        return out[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

    # ── account ────────────────────────────────────────────
    def _value(self, symbol: str, price_diff: float, volume: float) -> float:
        spec = self.specs[symbol]
        return price_diff / spec.tick_size * spec.tick_value * volume

    def _floating(self, pos: Position) -> float:
        t = self.tick(pos.symbol)
        if pos.side == BUY:
            pos.price_current = t.bid
            return self._value(pos.symbol, t.bid - pos.price_open, pos.volume)
        pos.price_current = t.ask
        return self._value(pos.symbol, pos.price_open - t.ask, pos.volume)

    def account(self) -> Account:
        floating = sum(self._floating(p) for p in self._positions.values())
        return Account(login=1, balance=self.balance, equity=self.balance + floating,
                       currency="USD", is_demo=self.is_demo)

    def positions(self, magic: int) -> list[Position]:
        out = []
        for p in self._positions.values():
            if p.magic == magic:
                p.profit = self._floating(p)
                out.append(replace(p))      # a snapshot copy, like the real MT5 package returns
        return out

    # ── orders ─────────────────────────────────────────────
    def market_order(self, symbol, side, volume, sl, tp, magic, comment, deviation) -> OrderResult:
        if not self.connected:
            return OrderResult(ok=False, message="not connected")
        t = self.tick(symbol)
        price = t.ask if side == BUY else t.bid
        if (side == BUY and not sl < price < (tp or 1e18)) or (side == SELL and not (tp or 0) < price < sl):
            return OrderResult(ok=False, message="invalid stops")
        self._next_ticket += 1
        ticket = self._next_ticket
        self._positions[ticket] = Position(
            ticket=ticket, symbol=symbol, side=side, volume=volume, price_open=price, sl=sl, tp=tp,
            profit=0.0, magic=magic, time=self.now(), price_current=price, comment=comment,
        )
        self.balance -= self.commission_per_lot * volume / 2
        return OrderResult(ok=True, ticket=ticket, price=price, message="filled")

    def modify_sl_tp(self, position: Position, sl: float, tp: float) -> OrderResult:
        pos = self._positions.get(position.ticket)
        if pos is None:
            return OrderResult(ok=False, message="no such position")
        pos.sl, pos.tp = sl, tp
        return OrderResult(ok=True, ticket=pos.ticket)

    def close(self, position: Position, volume: float | None = None, deviation: int = 20) -> OrderResult:
        pos = self._positions.get(position.ticket)
        if pos is None:
            return OrderResult(ok=False, message="no such position")
        t = self.tick(pos.symbol)
        price = t.bid if pos.side == BUY else t.ask
        self._realize(pos, price, volume)
        return OrderResult(ok=True, ticket=pos.ticket, price=price)

    def _realize(self, pos: Position, price: float, volume: float | None = None) -> None:
        vol = min(volume or pos.volume, pos.volume)
        diff = (price - pos.price_open) if pos.side == BUY else (pos.price_open - price)
        pnl = self._value(pos.symbol, diff, vol) - self.commission_per_lot * vol / 2
        self.balance += pnl
        self._deals.append(Deal(position_id=pos.ticket, symbol=pos.symbol, volume=vol, profit=pnl, time=self.now()))
        pos.volume = round(pos.volume - vol, 8)
        if pos.volume <= 1e-9:
            del self._positions[pos.ticket]

    def _check_stops(self, pos: Position, i: int) -> None:
        bar = self.data[pos.symbol].iloc[i]
        spread = self.spread_points[pos.symbol] * self.specs[pos.symbol].point
        if pos.side == BUY:                       # buys close at bid
            o, hi, lo = bar.open, bar.high, bar.low
            if pos.sl and lo <= pos.sl:
                self._realize(pos, min(o, pos.sl))
            elif pos.tp and hi >= pos.tp:
                self._realize(pos, max(o, pos.tp))
        else:                                     # sells close at ask
            o, hi, lo = bar.open + spread, bar.high + spread, bar.low + spread
            if pos.sl and hi >= pos.sl:
                self._realize(pos, max(o, pos.sl))
            elif pos.tp and lo <= pos.tp:
                self._realize(pos, min(o, pos.tp))

    def deals_since(self, since: datetime, magic: int, known_ids: set[int] | None = None) -> list[Deal]:
        return [d for d in self._deals if d.time >= since]
