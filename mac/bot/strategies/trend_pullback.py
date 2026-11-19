"""Trend pullback: in a strong uptrend, buy when a dip ends; in a strong downtrend,
sell when a bounce ends.

  1. Trend timeframe (H4): EMA50 above EMA200, price above EMA200, ADX strong → uptrend.
  2. Entry timeframe (H1): EMAs agree, and RSI dipped below 45 and has just turned back up.
  3. Stop-loss 1.5 x ATR away, take-profit 3 x ATR away (2:1).
"""
from __future__ import annotations

from bot.indicators import ema, rsi
from bot.models import BUY, SELL, Signal
from bot.scanner import MarketView
from bot.trend import DOWN, UP, trend_label


class TrendPullback:
    name = "trend_pullback"

    def __init__(self, params: dict):
        self.p = params

    def generate(self, view: MarketView) -> Signal | None:
        p = self.p
        trend = trend_label(view.trend_bars, p["ema_fast"], p["ema_slow"], p["adx_period"], p["adx_min"])
        if trend not in (UP, DOWN):
            return None

        bars = view.entry_bars
        close = bars["close"]
        fast = ema(close, p["ema_fast"]).iloc[-1]
        slow = ema(close, p["ema_slow"]).iloc[-1]
        r = rsi(close, p["rsi_period"])
        r_prev, r_now = r.iloc[-2], r.iloc[-1]
        bar_time = bars["time"].iloc[-1]
        bar_time = bar_time.to_pydatetime() if hasattr(bar_time, "to_pydatetime") else bar_time
        a = view.atr

        if trend == UP and fast > slow and close.iloc[-1] > slow \
                and r_prev < p["rsi_buy_below"] <= r_now:
            entry = view.tick.ask
            return Signal(view.symbol, BUY, entry, entry - p["sl_atr"] * a, entry + p["tp_atr"] * a,
                          a, strength=float(r_now - r_prev), bar_time=bar_time,
                          reason=f"H4 uptrend, RSI turned up {r_prev:.0f}→{r_now:.0f}")

        if trend == DOWN and fast < slow and close.iloc[-1] < slow \
                and r_prev > p["rsi_sell_above"] >= r_now:
            entry = view.tick.bid
            return Signal(view.symbol, SELL, entry, entry + p["sl_atr"] * a, entry - p["tp_atr"] * a,
                          a, strength=float(r_prev - r_now), bar_time=bar_time,
                          reason=f"H4 downtrend, RSI turned down {r_prev:.0f}→{r_now:.0f}")
        return None
