"""Daily profit target with brakes.

States (checked every cycle, reset at broker midnight):
  NORMAL      trade as usual
  PROTECT     reached part of the target → half risk on new trades, break-even stops sooner
  TARGET_HIT  reached the target → close (or tightly trail) everything, no new trades today
  LOSS_HIT    reached the daily loss limit → close everything, no new trades today
  GIVEBACK    was close to target, then gave a lot back → close everything, stop for today
"""
from __future__ import annotations

NORMAL, PROTECT, TARGET_HIT, LOSS_HIT, GIVEBACK = "NORMAL", "PROTECT", "TARGET_HIT", "LOSS_HIT", "GIVEBACK"
LOCKED = {TARGET_HIT, LOSS_HIT, GIVEBACK}


class DailyGuard:
    def __init__(self, cfg):
        self.d = cfg["daily"]

    def next_state(self, current: str, pnl_pct: float, peak_pct: float) -> str:
        if current in LOCKED:
            return current                         # stays locked until tomorrow
        d = self.d
        target = d["target_pct"]
        if pnl_pct <= -d["max_loss_pct"]:
            return LOSS_HIT
        if pnl_pct >= target:
            return TARGET_HIT
        if peak_pct >= d["giveback_from"] * target and pnl_pct <= d["giveback_to"] * target:
            return GIVEBACK
        if current == PROTECT or pnl_pct >= d["protect_at_fraction"] * target:
            return PROTECT
        return NORMAL

    @staticmethod
    def can_open_trades(state: str) -> bool:
        return state not in LOCKED

    @staticmethod
    def risk_scale(state: str) -> float:
        return 0.5 if state == PROTECT else 1.0

    def should_close_all(self, state: str) -> bool:
        if state in (LOSS_HIT, GIVEBACK):
            return True
        return state == TARGET_HIT and self.d["on_target"] == "close_all"
