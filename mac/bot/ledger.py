"""Daily accounts: what happened today, written to the database, a CSV per day,
and a short text report for Telegram."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from bot.brokers.base import Broker
from bot.state import Store


class Ledger:
    def __init__(self, broker: Broker, store: Store, magic: int, reports_dir: str | Path = "data/reports"):
        self.broker = broker
        self.store = store
        self.magic = magic
        self.reports_dir = Path(reports_dir)

    @staticmethod
    def day_start(day: date) -> datetime:
        return datetime(day.year, day.month, day.day)

    def stats(self, day: date, open_tickets: set[int]) -> dict:
        start = self.day_start(day)
        end = start + timedelta(days=1)
        deals = [d for d in self.broker.deals_since(start, self.magic, self.store.all_trade_tickets())
                 if d.time < end]
        per_position: dict[int, float] = defaultdict(float)
        for d in deals:
            per_position[d.position_id] += d.profit
        closed = {pid: pnl for pid, pnl in per_position.items() if pid not in open_tickets}
        return {
            "realized": round(sum(d.profit for d in deals), 2),
            "closed_trades": len(closed),
            "wins": sum(1 for v in closed.values() if v > 0),
            "losses": sum(1 for v in closed.values() if v <= 0),
            "deals": deals,
        }

    def finish_day(self, day: date, end_equity: float, open_tickets: set[int]) -> str:
        row = self.store.get_day(day)
        if row is None:
            return ""
        s = self.stats(day, open_tickets)
        self.store.update_day(day, end_equity=end_equity, realized=s["realized"],
                              closed_trades=s["closed_trades"], wins=s["wins"], losses=s["losses"])
        self._write_csv(day, s)
        self._append_summary(day, row["start_equity"], end_equity, s, row)
        return self.report_text(day, row["start_equity"], end_equity, s, row["state"], row["new_trades"])

    def report_text(self, day: date, start_eq: float, equity: float, s: dict, state: str, new_trades: int) -> str:
        change = equity - start_eq
        pct = change / start_eq * 100 if start_eq else 0
        n = s["closed_trades"]
        win_rate = f"{s['wins'] / n:.0%}" if n else "–"
        return (f"📒 Day {day:%a %d %b %Y}\n"
                f"Start equity: {start_eq:,.2f}\nEnd equity:   {equity:,.2f}\n"
                f"Change: {change:+,.2f} ({pct:+.2f}%)\n"
                f"Realized P/L: {s['realized']:+,.2f}\n"
                f"Trades opened: {new_trades} · closed: {n} · win rate: {win_rate}\n"
                f"Day status: {state}")

    def _write_csv(self, day: date, s: dict) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / f"{day.isoformat()}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "position", "symbol", "volume", "profit"])
            for d in s["deals"]:
                w.writerow([d.time.isoformat(), d.position_id, d.symbol, d.volume, round(d.profit, 2)])

    def _append_summary(self, day: date, start_eq: float, end_eq: float, s: dict, row) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / "daily_summary.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["day", "start_equity", "end_equity", "change", "change_pct", "realized",
                            "trades_opened", "trades_closed", "wins", "losses", "day_status"])
            change = end_eq - start_eq
            w.writerow([day.isoformat(), round(start_eq, 2), round(end_eq, 2), round(change, 2),
                        round(change / start_eq * 100, 3) if start_eq else 0, s["realized"],
                        row["new_trades"], s["closed_trades"], s["wins"], s["losses"], row["state"]])
