"""Trend reader: labels a market as strong up, strong down, or no clear trend."""
from __future__ import annotations

import pandas as pd

from bot.indicators import adx, ema

UP, DOWN, NONE = "up", "down", "none"


def trend_label(bars: pd.DataFrame, ema_fast: int = 50, ema_slow: int = 200,
                adx_period: int = 14, adx_min: float = 20.0) -> str:
    if len(bars) < ema_slow:
        return NONE
    close = bars["close"]
    fast, slow = ema(close, ema_fast).iloc[-1], ema(close, ema_slow).iloc[-1]
    strength = adx(bars, adx_period).iloc[-1]
    last = close.iloc[-1]
    if strength < adx_min:
        return NONE
    if last > slow and fast > slow:
        return UP
    if last < slow and fast < slow:
        return DOWN
    return NONE
