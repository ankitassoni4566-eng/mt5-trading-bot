"""Market scanner: loops over every symbol, drops the ones not worth trading right
now, and hands the rest (with their price history) to the strategy."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from bot.brokers.base import Broker
from bot.indicators import atr
from bot.models import SymbolSpec, Tick

log = logging.getLogger(__name__)


@dataclass
class MarketView:
    symbol: str
    spec: SymbolSpec
    tick: Tick
    entry_bars: pd.DataFrame     # e.g. H1, closed bars
    trend_bars: pd.DataFrame     # e.g. H4, closed bars
    atr: float


class Scanner:
    def __init__(self, cfg, broker: Broker):
        self.cfg = cfg
        self.broker = broker
        self._last_bar: dict[str, object] = {}   # symbol → last closed bar time evaluated

    def symbols(self) -> list[str]:
        wanted = self.cfg["symbols"]
        if isinstance(wanted, str) and wanted.lower() == "all":
            return self.broker.all_symbols()
        return list(wanted)

    def scan(self, only_new_bars: bool = True) -> list[MarketView]:
        tf_entry = self.cfg["timeframes"]["entry"]
        tf_trend = self.cfg["timeframes"]["trend"]
        min_bars = int(self.cfg["scan"]["min_bars"])
        max_spread_atr = float(self.cfg["scan"]["max_spread_atr"])
        atr_period = int(self.cfg["strategy"].get("atr_period", 14))
        views = []
        for sym in self.symbols():
            try:
                spec = self.broker.symbol_spec(sym)
                tick = self.broker.tick(sym)
                if spec is None or tick is None:
                    continue                                   # unknown symbol or market closed
                entry = self.broker.bars(sym, tf_entry, min_bars + 50)
                if len(entry) < min_bars:
                    continue
                last_time = entry["time"].iloc[-1]
                if only_new_bars and self._last_bar.get(sym) == last_time:
                    continue                                   # nothing new since last look
                self._last_bar[sym] = last_time
                a = float(atr(entry, atr_period).iloc[-1])
                if a <= 0 or tick.spread > max_spread_atr * a:
                    log.debug("%s skipped: spread %.5f vs ATR %.5f", sym, tick.spread, a)
                    continue
                trend = self.broker.bars(sym, tf_trend, min_bars + 50)
                if len(trend) < min_bars:
                    continue
                views.append(MarketView(sym, spec, tick, entry, trend, a))
            except Exception as exc:  # one bad symbol must not stop the scan
                log.warning("scan error on %s: %r", sym, exc)
        return views
