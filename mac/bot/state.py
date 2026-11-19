"""SQLite storage: signals (duplicate protection), trades, daily accounts, flags.
Survives crashes and restarts, so the bot always knows what it already did."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY, created TEXT, symbol TEXT, side TEXT, status TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    ticket INTEGER PRIMARY KEY, signal_id TEXT, symbol TEXT, side TEXT, volume REAL,
    open_time TEXT, open_price REAL, sl REAL, tp REAL, risk_distance REAL, risk_money REAL,
    partial_done INTEGER DEFAULT 0, closed INTEGER DEFAULT 0, close_time TEXT, profit REAL
);
CREATE TABLE IF NOT EXISTS days (
    day TEXT PRIMARY KEY, start_equity REAL, peak_pnl_pct REAL DEFAULT 0, state TEXT DEFAULT 'NORMAL',
    end_equity REAL, realized REAL, closed_trades INTEGER, wins INTEGER, losses INTEGER,
    new_trades INTEGER DEFAULT 0, reported INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, kind TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: str | Path = "data/bot.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _one(self, sql: str, args: tuple = ()):
        with self.lock:
            return self.db.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ── signals: never send the same signal twice ──────────
    def claim_signal(self, signal_id: str, symbol: str, side: str, when: datetime) -> bool:
        """Returns False if this signal was already sent (or is being sent)."""
        try:
            self._exec("INSERT INTO signals (id, created, symbol, side, status) VALUES (?,?,?,?,?)",
                       (signal_id, when.isoformat(), symbol, side, "sending"))
            return True
        except sqlite3.IntegrityError:
            return False

    def set_signal_status(self, signal_id: str, status: str, message: str = "") -> None:
        self._exec("UPDATE signals SET status=?, message=? WHERE id=?", (status, message, signal_id))

    # ── trades ─────────────────────────────────────────────
    def add_trade(self, ticket: int, signal_id: str, symbol: str, side: str, volume: float,
                  open_time: datetime, open_price: float, sl: float, tp: float,
                  risk_distance: float, risk_money: float) -> None:
        self._exec("""INSERT OR REPLACE INTO trades (ticket, signal_id, symbol, side, volume, open_time,
                      open_price, sl, tp, risk_distance, risk_money) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (ticket, signal_id, symbol, side, volume, open_time.isoformat(), open_price, sl, tp,
                    risk_distance, risk_money))

    def trade(self, ticket: int):
        return self._one("SELECT * FROM trades WHERE ticket=?", (ticket,))

    def open_trade_tickets(self) -> set[int]:
        return {r["ticket"] for r in self._all("SELECT ticket FROM trades WHERE closed=0")}

    def all_trade_tickets(self) -> set[int]:
        return {r["ticket"] for r in self._all("SELECT ticket FROM trades")}

    def mark_partial(self, ticket: int) -> None:
        self._exec("UPDATE trades SET partial_done=1 WHERE ticket=?", (ticket,))

    def mark_closed(self, ticket: int, when: datetime, profit: float | None) -> None:
        self._exec("UPDATE trades SET closed=1, close_time=?, profit=? WHERE ticket=?",
                   (when.isoformat(), profit, ticket))

    def recent_trades(self, n: int = 10):
        return self._all("SELECT * FROM trades ORDER BY open_time DESC LIMIT ?", (n,))

    # ── days ───────────────────────────────────────────────
    def get_day(self, day: date):
        return self._one("SELECT * FROM days WHERE day=?", (day.isoformat(),))

    def start_day(self, day: date, start_equity: float) -> None:
        self._exec("INSERT OR IGNORE INTO days (day, start_equity) VALUES (?,?)", (day.isoformat(), start_equity))

    def update_day(self, day: date, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE days SET {cols} WHERE day=?", (*fields.values(), day.isoformat()))

    def days(self, n: int = 30):
        return self._all("SELECT * FROM days ORDER BY day DESC LIMIT ?", (n,))

    def last_day(self):
        return self._one("SELECT * FROM days ORDER BY day DESC LIMIT 1")

    # ── events + flags ─────────────────────────────────────
    def event(self, kind: str, text: str, when: datetime | None = None) -> None:
        self._exec("INSERT INTO events (time, kind, text) VALUES (?,?,?)",
                   ((when or datetime.now()).isoformat(), kind, text))

    def get_flag(self, key: str, default: str = "") -> str:
        row = self._one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, value))
