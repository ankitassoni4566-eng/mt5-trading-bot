"""Plain data objects shared by every module."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

BUY, SELL = "buy", "sell"


@dataclass
class SymbolSpec:
    name: str
    point: float            # smallest price step, e.g. 0.00001
    digits: int
    tick_size: float
    tick_value: float       # account-currency value of one tick for 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int = 0    # min distance (points) for SL/TP from price

    @property
    def base_quote(self) -> tuple[str, str]:
        """('EUR','USD') for EURUSD; ('XAU','USD') for XAUUSD; (name, '') otherwise."""
        n = self.name.upper()
        letters = "".join(ch for ch in n if ch.isalpha())
        if len(letters) >= 6 and letters[:6].isalpha() and len(n) <= 8:
            return letters[:3], letters[3:6]
        return n, ""


@dataclass
class Tick:
    bid: float
    ask: float
    time: datetime

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Account:
    login: int
    balance: float
    equity: float
    currency: str = "USD"
    is_demo: bool = True


@dataclass
class Position:
    ticket: int
    symbol: str
    side: str               # buy / sell
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float           # floating P/L in account currency
    magic: int
    time: datetime
    price_current: float = 0.0
    comment: str = ""


@dataclass
class Deal:
    """A closing deal (full or partial close) of one of our positions."""
    position_id: int
    symbol: str
    volume: float
    profit: float           # includes commission + swap
    time: datetime


@dataclass
class Signal:
    symbol: str
    side: str
    entry: float
    sl: float
    tp: float
    atr: float
    strength: float         # higher = better; used to rank when several signals fire together
    bar_time: datetime
    reason: str = ""

    @property
    def id(self) -> str:
        return f"{self.symbol}-{self.side}-{self.bar_time:%Y%m%d%H%M}"


@dataclass
class OrderResult:
    ok: bool
    ticket: int = 0
    price: float = 0.0
    message: str = ""


@dataclass
class Decision:
    approved: bool
    volume: float = 0.0
    reason: str = ""
    risk_money: float = 0.0
    extra: dict = field(default_factory=dict)
