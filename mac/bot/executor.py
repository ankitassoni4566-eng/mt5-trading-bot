"""Executor: sends approved trades to the broker exactly once."""
from __future__ import annotations

import logging

from bot.brokers.base import Broker
from bot.models import Decision, OrderResult, Signal
from bot.state import Store

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, broker: Broker, store: Store, magic: int, deviation: int):
        self.broker = broker
        self.store = store
        self.magic = magic
        self.deviation = deviation

    def execute(self, sig: Signal, decision: Decision) -> OrderResult:
        # Claim the signal BEFORE sending: a crash/restart can never send it twice
        if not self.store.claim_signal(sig.id, sig.symbol, sig.side, self.broker.now()):
            return OrderResult(ok=False, message="duplicate signal, already handled")

        res = self.broker.market_order(sig.symbol, sig.side, decision.volume, sig.sl, sig.tp,
                                       self.magic, f"bot {sig.id}", self.deviation)
        if not res.ok:
            self.store.set_signal_status(sig.id, "failed", res.message)
            log.warning("order failed %s: %s", sig.id, res.message)
            return res

        fill = res.price or sig.entry
        # keep the planned stop distance even if the fill slipped a little
        self.store.add_trade(res.ticket, sig.id, sig.symbol, sig.side, decision.volume, self.broker.now(),
                             fill, sig.sl, sig.tp, abs(sig.entry - sig.sl), decision.risk_money)
        self.store.set_signal_status(sig.id, "filled", f"ticket {res.ticket}")
        log.info("OPEN %s %s %.2f lots @ %s  SL %s  TP %s  (%s)", sig.side.upper(), sig.symbol,
                 decision.volume, fill, sig.sl, sig.tp, sig.reason)
        return res
