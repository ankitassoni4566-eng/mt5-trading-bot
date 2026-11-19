"""The main loop. Every cycle:

  1. make sure MT5 is connected, write a heartbeat
  2. new day? → close the books for yesterday, start today's
  3. update the daily-target state; close everything if a limit is hit
  4. manage open trades (break-even, partial profit, trailing)
  5. if allowed, scan all markets and open as many good trades as the risk limits allow
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path

from bot.brokers.base import Broker
from bot.daily import LOCKED, DailyGuard
from bot.executor import Executor
from bot.ledger import Ledger
from bot.models import Position
from bot.notifier import Notifier
from bot.profit_manager import ProfitManager
from bot.risk import RiskManager
from bot.scanner import Scanner
from bot.state import Store
from bot.strategies import make_strategy

log = logging.getLogger(__name__)

STATE_MESSAGES = {
    "PROTECT": "🛡 Halfway to today's target ({pnl:+.2f}%). New trades at half risk, stops to break-even sooner.",
    "TARGET_HIT": "🎯 Daily target reached ({pnl:+.2f}%). {action} No new trades until tomorrow.",
    "LOSS_HIT": "🛑 Daily loss limit hit ({pnl:+.2f}%). All trades closed. Trading stops until tomorrow.",
    "GIVEBACK": "↩️ Gave back too much of today's gain ({pnl:+.2f}%). All trades closed, done for today.",
}


class Engine:
    def __init__(self, cfg, broker: Broker, store: Store, notifier: Notifier | None = None,
                 write_heartbeat: bool = True):
        self.cfg = cfg
        self.broker = broker
        self.store = store
        self.magic = int(cfg["magic"])
        self.notifier = notifier or Notifier(False)
        self.scanner = Scanner(cfg, broker)
        self.strategy = make_strategy(cfg["strategy"])
        self.risk = RiskManager(cfg, broker.symbol_spec)
        self.executor = Executor(broker, store, self.magic, int(cfg["risk"].get("deviation_points", 20)))
        self.profit = ProfitManager(cfg, broker, store)
        self.guard = DailyGuard(cfg)
        storage = cfg.get("storage", {})
        self.ledger = Ledger(broker, store, self.magic, storage.get("reports_dir", "data/reports"))
        self.heartbeat_file = Path(storage.get("heartbeat_file", "data/heartbeat.txt")) if write_heartbeat else None
        self.day: date | None = None
        self.max_concurrent = 0          # most trades open at the same time (for reports/tests)
        self._alerts: set[str] = set()

    # ── one cycle ──────────────────────────────────────────
    def cycle(self) -> None:
        if not self.broker.ensure_connected():
            self._alert_once("disconnected", "⚠️ Lost connection to MT5 — reconnecting…")
            return
        if "disconnected" in self._alerts:
            self._alerts.discard("disconnected")
            self.notifier.send("✅ Reconnected to MT5.")
        self._heartbeat()
        self._handle_commands()

        now = self.broker.now()
        account = self.broker.account()
        self._roll_day(now.date(), account)
        positions = self.broker.positions(self.magic)
        self._reconcile(positions, now)
        self.max_concurrent = max(self.max_concurrent, len(positions))

        # daily target / loss limit
        day = self.store.get_day(self.day)
        start_eq = day["start_equity"] or account.equity
        pnl_pct = (account.equity - start_eq) / start_eq * 100
        peak = max(day["peak_pnl_pct"] or 0.0, pnl_pct)
        state = self.guard.next_state(day["state"], pnl_pct, peak)
        self.store.update_day(self.day, peak_pnl_pct=peak, state=state)
        if state != day["state"]:
            self._on_state_change(state, pnl_pct)
        if self.guard.should_close_all(state) and positions:
            self.close_all(f"day {state}")
            positions = self.broker.positions(self.magic)

        # take profit on open trades
        if positions:
            for msg in self.profit.manage(positions, state):
                self.notifier.send("💰 " + msg)
            positions = self.broker.positions(self.magic)

        if self.store.get_flag("paused") == "1" or not self.guard.can_open_trades(state):
            return
        self._look_for_trades(account, positions, state, now)

    # ── finding and opening trades (several at once) ───────
    def _look_for_trades(self, account, positions: list[Position], state: str, now: datetime) -> None:
        signals = []
        for view in self.scanner.scan():
            try:
                sig = self.strategy.generate(view)
            except Exception as exc:
                log.warning("strategy error on %s: %s", view.symbol, exc)
                continue
            if sig:
                signals.append(sig)
        if not signals:
            return
        signals.sort(key=lambda s: s.strength, reverse=True)     # best setups first

        open_now = list(positions)
        trades_today = self.store.get_day(self.day)["new_trades"] or 0
        for sig in signals:
            decision = self.risk.evaluate(sig, account, open_now, trades_today, self.guard.risk_scale(state))
            if not decision.approved:
                log.info("SKIP %s %s: %s", sig.side, sig.symbol, decision.reason)
                continue
            res = self.executor.execute(sig, decision)
            if not res.ok:
                continue
            trades_today += 1
            self.store.update_day(self.day, new_trades=trades_today)
            open_now.append(Position(res.ticket, sig.symbol, sig.side, decision.volume, res.price or sig.entry,
                                     sig.sl, sig.tp, 0.0, self.magic, now))
            self.notifier.send(f"📈 {sig.side.upper()} {sig.symbol} {decision.volume} lots @ {res.price or sig.entry}\n"
                               f"SL {sig.sl:.5g} · TP {sig.tp:.5g}\n{sig.reason}\n"
                               f"Open trades now: {len(open_now)}/{self.cfg['risk']['max_open_trades']}")
        self.max_concurrent = max(self.max_concurrent, len(open_now))

    # ── housekeeping ───────────────────────────────────────
    def _roll_day(self, today: date, account) -> None:
        if self.day == today:
            return
        last = self.store.last_day()
        if last and last["day"] != today.isoformat() and not last["reported"]:
            prev = date.fromisoformat(last["day"])
            text = self.ledger.finish_day(prev, account.equity, self.store.open_trade_tickets())
            self.store.update_day(prev, reported=1)
            if text and self.cfg.get("telegram", {}).get("daily_report", True):
                self.notifier.send(text)
        self.store.start_day(today, account.equity)   # keeps the first value if restarted mid-day
        self.day = today

    def _reconcile(self, positions: list[Position], now: datetime) -> None:
        """Trades the broker closed (stop-loss / take-profit) → mark closed in our records."""
        live = {p.ticket for p in positions}
        for ticket in self.store.open_trade_tickets() - live:
            rec = self.store.trade(ticket)
            since = datetime.fromisoformat(rec["open_time"])
            profit = sum(d.profit for d in self.broker.deals_since(since, self.magic, {ticket})
                         if d.position_id == ticket)
            self.store.mark_closed(ticket, now, round(profit, 2))
            self.notifier.send(f"{'✅' if profit > 0 else '❌'} Closed {rec['symbol']} #{ticket}: {profit:+.2f}")

    def close_all(self, reason: str) -> int:
        n = 0
        for p in self.broker.positions(self.magic):
            res = self.broker.close(p, None, int(self.cfg["risk"].get("deviation_points", 20)))
            if res.ok:
                n += 1
            else:
                log.error("could not close %s: %s", p.ticket, res.message)
        if n:
            log.info("closed %d trades (%s)", n, reason)
        return n

    def _on_state_change(self, state: str, pnl: float) -> None:
        self.store.event("day_state", f"{state} at {pnl:+.2f}%", self.broker.now())
        if state in STATE_MESSAGES:
            action = ("All trades closed." if self.cfg["daily"]["on_target"] == "close_all"
                      else "Open trades trailed tightly.")
            self.notifier.send(STATE_MESSAGES[state].format(pnl=pnl, action=action))

    def _heartbeat(self) -> None:
        if self.heartbeat_file:
            self.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_file.write_text(str(time.time()))

    def _alert_once(self, key: str, text: str) -> None:
        if key not in self._alerts:
            self._alerts.add(key)
            self.notifier.send(text)
            log.warning(text)

    # ── Telegram commands ──────────────────────────────────
    def status_text(self) -> str:
        acc = self.broker.account()
        day = self.store.get_day(self.day) if self.day else None
        positions = self.broker.positions(self.magic)
        start = day["start_equity"] if day else acc.equity
        pnl = acc.equity - start
        paused = self.store.get_flag("paused") == "1"
        return (f"Equity {acc.equity:,.2f} {acc.currency} · today {pnl:+,.2f} ({pnl / start * 100:+.2f}%)\n"
                f"Target +{self.cfg['daily']['target_pct']}% · loss limit -{self.cfg['daily']['max_loss_pct']}%\n"
                f"Day status: {day['state'] if day else '-'} · open trades: {len(positions)}/"
                f"{self.cfg['risk']['max_open_trades']} · {'PAUSED' if paused else 'running'}")

    def _handle_commands(self) -> None:
        for cmd in self.notifier.commands():
            if cmd in ("/status", "/today"):
                self.notifier.send(self.status_text())
            elif cmd == "/positions":
                ps = self.broker.positions(self.magic)
                lines = [f"{p.side.upper()} {p.symbol} {p.volume} @ {p.price_open} SL {p.sl} P/L {p.profit:+.2f}"
                         for p in ps]
                self.notifier.send("\n".join(lines) or "No open trades.")
            elif cmd == "/trades":
                rows = self.store.recent_trades(10)
                lines = [f"{r['open_time'][:16]} {r['side']} {r['symbol']} "
                         f"{'open' if not r['closed'] else format(r['profit'] or 0, '+.2f')}" for r in rows]
                self.notifier.send("\n".join(lines) or "No trades yet.")
            elif cmd == "/pause":
                self.store.set_flag("paused", "1")
                self.notifier.send("⏸ Paused: no new trades. Open trades keep their stops and profit rules.")
            elif cmd == "/resume":
                self.store.set_flag("paused", "0")
                self.notifier.send("▶️ Resumed.")
            elif cmd == "/stop":
                self.store.set_flag("paused", "1")
                n = self.close_all("telegram /stop")
                self.notifier.send(f"🛑 Kill switch: closed {n} trades and paused. Send /resume to restart.")

    # ── run forever ────────────────────────────────────────
    def run_forever(self) -> None:
        loop = int(self.cfg.get("loop_seconds", 30))
        self.notifier.send(f"🤖 Bot started ({self.cfg['mode']} mode). " + self._safe_status())
        errors = 0
        while True:
            try:
                self.cycle()
                errors = 0
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                errors += 1
                log.exception("cycle failed: %s", exc)
                if errors in (1, 10, 100):
                    self.notifier.send(f"⚠️ Error ({errors}x): {exc}")
            time.sleep(loop)

    def _safe_status(self) -> str:
        try:
            return self.status_text()
        except Exception:
            return ""


__all__ = ["Engine", "LOCKED"]
