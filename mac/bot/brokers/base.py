"""The broker interface. The engine only talks to this, so the real MT5
broker and the simulator are interchangeable (that's how tests run on GitHub)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from bot.models import Account, Deal, OrderResult, Position, SymbolSpec, Tick


class Broker(ABC):
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    def ensure_connected(self) -> bool:
        return self.is_connected() or self.connect()

    @abstractmethod
    def now(self) -> datetime:
        """Broker server time (the trading day rolls over at server midnight)."""

    @abstractmethod
    def all_symbols(self) -> list[str]: ...

    @abstractmethod
    def symbol_spec(self, symbol: str) -> SymbolSpec | None: ...

    @abstractmethod
    def tick(self, symbol: str) -> Tick | None: ...

    @abstractmethod
    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """CLOSED bars only (the still-forming bar is excluded).
        Columns: time, open, high, low, close, volume. Oldest first."""

    @abstractmethod
    def account(self) -> Account: ...

    @abstractmethod
    def positions(self, magic: int) -> list[Position]: ...

    @abstractmethod
    def market_order(self, symbol: str, side: str, volume: float, sl: float, tp: float,
                     magic: int, comment: str, deviation: int) -> OrderResult: ...

    @abstractmethod
    def modify_sl_tp(self, position: Position, sl: float, tp: float) -> OrderResult: ...

    @abstractmethod
    def close(self, position: Position, volume: float | None = None, deviation: int = 20) -> OrderResult:
        """Close the whole position, or `volume` lots of it (partial close)."""

    @abstractmethod
    def deals_since(self, since: datetime, magic: int, known_ids: set[int] | None = None) -> list[Deal]:
        """Closing deals (full + partial) of our positions since `since` (server time).
        `known_ids` = position tickets the bot opened, matched even if a deal lacks the magic number."""

    def shutdown(self) -> None:
        pass
