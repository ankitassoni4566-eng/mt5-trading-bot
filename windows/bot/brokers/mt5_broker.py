"""Real MetaTrader 5 broker.

  Windows : the official `MetaTrader5` package, talking to the MT5 terminal on the same PC.
  macOS   : the `siliconmetatrader5` bridge, which runs MT5 in its own runtime on the Mac and
            exposes the same functions over a local connection (host/port in .env).

Both offer the same functions, so everything below works unchanged on either platform.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from bot.brokers.base import Broker
from bot.models import BUY, SELL, Account, Deal, OrderResult, Position, SymbolSpec, Tick

log = logging.getLogger(__name__)

# Official MetaTrader5 constant values — used if a backend doesn't expose them as attributes.
DEFAULT_CONSTANTS = {
    "TIMEFRAME_M1": 1, "TIMEFRAME_M5": 5, "TIMEFRAME_M15": 15, "TIMEFRAME_M30": 30,
    "TIMEFRAME_H1": 16385, "TIMEFRAME_H4": 16388, "TIMEFRAME_D1": 16408,
    "TRADE_ACTION_DEAL": 1, "TRADE_ACTION_SLTP": 6,
    "ORDER_TYPE_BUY": 0, "ORDER_TYPE_SELL": 1,
    "ORDER_FILLING_FOK": 0, "ORDER_FILLING_IOC": 1, "ORDER_FILLING_RETURN": 2,
    "ORDER_TIME_GTC": 0, "TRADE_RETCODE_DONE": 10009,
    "POSITION_TYPE_BUY": 0, "DEAL_ENTRY_OUT": 1, "DEAL_ENTRY_OUT_BY": 3,
    "SYMBOL_TRADE_MODE_FULL": 4, "ACCOUNT_TRADE_MODE_DEMO": 0,
}
RATE_FIELDS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
RETRY_RETCODES = {10004, 10020, 10021, 10031}   # requote, prices changed, off quotes, no connection

MAC_HINT = ("On a Mac the bot needs the SiliconMetaTrader5 bridge:\n"
            "  1) pip install siliconmetatrader5\n"
            "  2) start the bridge runtime and log in to your MT5 demo account (see MAC_SETUP.md)\n"
            "  3) put its host/port in .env (MT5_BRIDGE_HOST / MT5_BRIDGE_PORT)")
WIN_HINT = "On Windows install the official package: pip install MetaTrader5"


def load_backend():
    """Returns (mt5_api, kind). kind is 'official' (Windows) or 'bridge' (Mac)."""
    if sys.platform == "win32":
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise RuntimeError(WIN_HINT) from exc
        return mt5, "official"
    try:
        from siliconmetatrader5 import MetaTrader5  # type: ignore
    except ImportError as exc:
        raise RuntimeError(MAC_HINT if sys.platform == "darwin" else
                           "Live trading needs Windows (MetaTrader5) or macOS (siliconmetatrader5). "
                           "On this system you can run: python run.py --backtest 90") from exc
    host = os.getenv("MT5_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("MT5_BRIDGE_PORT", "8001"))
    return MetaTrader5(host=host, port=port, keepalive=True), "bridge"


class _Constants:
    def __init__(self, api):
        self._api = api

    def __getattr__(self, name: str):
        try:
            value = getattr(self._api, name, None)
        except Exception:          # a remote proxy may raise its own error type
            value = None
        if value is None:
            if name not in DEFAULT_CONSTANTS:
                raise AttributeError(name)
            value = DEFAULT_CONSTANTS[name]
        return value


def _to_datetime(ts) -> datetime:
    """MT5 timestamps are server time expressed as seconds; keep them as naive server time."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)


def rates_to_frame(rates) -> pd.DataFrame:
    """copy_rates_* results → DataFrame. Handles numpy record arrays (official package),
    and plain lists of tuples/dicts (what a bridge may send back)."""
    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    if rates is None:
        return empty
    try:
        arr = np.asarray(rates)
    except Exception:
        arr = None
    if arr is not None and arr.dtype.names:
        df = pd.DataFrame({name: arr[name] for name in arr.dtype.names})
    else:
        rows = list(rates)
        if not rows:
            return empty
        first = rows[0]
        if isinstance(first, dict):
            df = pd.DataFrame(rows)
        elif hasattr(first, "_asdict"):
            df = pd.DataFrame([r._asdict() for r in rows])
        else:
            df = pd.DataFrame([tuple(r) for r in rows], columns=RATE_FIELDS[:len(tuple(first))])
    if df.empty:
        return empty
    df["time"] = pd.to_datetime(df["time"].astype("int64"), unit="s")
    df = df.rename(columns={"tick_volume": "volume"})
    if "volume" not in df:
        df["volume"] = 0
    return df[["time", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float})


class MT5Broker(Broker):
    def __init__(self, login: int | None = None, password: str | None = None,
                 server: str | None = None, path: str | None = None, backend=None):
        if backend is None:
            self.mt5, self.kind = load_backend()
        else:
            self.mt5, self.kind = backend, "custom"
        self.c = _Constants(self.mt5)
        self.login = int(login or os.getenv("MT5_LOGIN", "0") or 0)
        self.password = password or os.getenv("MT5_PASSWORD", "")
        self.server = server or os.getenv("MT5_SERVER", "")
        self.path = path or os.getenv("MT5_PATH") or None
        self._spec_cache: dict[str, SymbolSpec] = {}
        self._tf = {k: getattr(self.c, f"TIMEFRAME_{k}") for k in ("M1", "M5", "M15", "M30", "H1", "H4", "D1")}

    # ── connection ─────────────────────────────────────────
    def connect(self) -> bool:
        kwargs = {}
        if self.path and self.kind == "official":
            kwargs["path"] = self.path
        if self.login:
            kwargs.update(login=self.login, password=self.password, server=self.server)
        for attempt in range(1, 6):
            try:
                ok = self.mt5.initialize(**kwargs)
            except Exception as exc:                       # bridge not running yet, etc.
                log.warning("MT5 connect attempt %d raised: %s", attempt, exc)
                ok = False
            if ok:
                info = self.mt5.account_info()
                log.info("Connected to MT5 (%s): account %s on %s", self.kind,
                         getattr(info, "login", "?"), getattr(info, "server", "?"))
                return True
            try:
                log.warning("MT5 connect attempt %d failed: %s", attempt, self.mt5.last_error())
            except Exception:
                pass
            time.sleep(min(60, 5 * attempt))
        return False

    def is_connected(self) -> bool:
        try:
            info = self.mt5.terminal_info()
            return bool(info and info.connected and self.mt5.account_info() is not None)
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            # On the Mac bridge, shutdown() would disconnect every client; close() only this one.
            if self.kind == "bridge" and hasattr(self.mt5, "close"):
                self.mt5.close()
            else:
                self.mt5.shutdown()
        except Exception:
            pass

    def now(self) -> datetime:
        # Server time = time of the latest tick of a liquid symbol (MT5 stamps ticks in server time)
        for sym in ("EURUSD", "XAUUSD", "GBPUSD"):
            t = self.mt5.symbol_info_tick(sym)
            if t and t.time:
                return _to_datetime(t.time)
        return datetime.now(timezone.utc).replace(tzinfo=None)

    # ── market data ────────────────────────────────────────
    def all_symbols(self) -> list[str]:
        return [s.name for s in (self.mt5.symbols_get() or []) if s.trade_mode == self.c.SYMBOL_TRADE_MODE_FULL]

    def symbol_spec(self, symbol: str) -> SymbolSpec | None:
        if symbol in self._spec_cache:
            return self._spec_cache[symbol]
        info = self.mt5.symbol_info(symbol)
        if info is None:
            return None
        if not info.visible:
            self.mt5.symbol_select(symbol, True)
            info = self.mt5.symbol_info(symbol)
        spec = SymbolSpec(
            name=symbol, point=float(info.point), digits=int(info.digits),
            tick_size=float(info.trade_tick_size or info.point), tick_value=float(info.trade_tick_value),
            volume_min=float(info.volume_min), volume_max=float(info.volume_max),
            volume_step=float(info.volume_step), stops_level=int(info.trade_stops_level),
        )
        self._spec_cache[symbol] = spec
        return spec

    def tick(self, symbol: str) -> Tick | None:
        t = self.mt5.symbol_info_tick(symbol)
        if t is None or not t.bid or not t.ask:
            return None
        return Tick(bid=float(t.bid), ask=float(t.ask), time=_to_datetime(t.time))

    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        # start_pos=1 skips the bar that is still forming → closed bars only
        return rates_to_frame(self.mt5.copy_rates_from_pos(symbol, self._tf[timeframe], 1, count))

    # ── account ────────────────────────────────────────────
    def account(self) -> Account:
        a = self.mt5.account_info()
        return Account(login=int(a.login), balance=float(a.balance), equity=float(a.equity),
                       currency=str(a.currency), is_demo=(a.trade_mode == self.c.ACCOUNT_TRADE_MODE_DEMO))

    def positions(self, magic: int) -> list[Position]:
        out = []
        for p in self.mt5.positions_get() or []:
            if p.magic != magic:
                continue
            out.append(Position(
                ticket=int(p.ticket), symbol=str(p.symbol),
                side=BUY if p.type == self.c.POSITION_TYPE_BUY else SELL,
                volume=float(p.volume), price_open=float(p.price_open), sl=float(p.sl), tp=float(p.tp),
                profit=float(p.profit), magic=int(p.magic), time=_to_datetime(p.time),
                price_current=float(p.price_current), comment=str(p.comment),
            ))
        return out

    # ── orders ─────────────────────────────────────────────
    def _filling(self, symbol: str) -> int:
        info = self.mt5.symbol_info(symbol)
        mode = int(getattr(info, "filling_mode", 0) or 0)
        if mode & 1:   # SYMBOL_FILLING_FOK
            return self.c.ORDER_FILLING_FOK
        if mode & 2:   # SYMBOL_FILLING_IOC
            return self.c.ORDER_FILLING_IOC
        return self.c.ORDER_FILLING_RETURN

    def _send(self, request: dict, retries: int = 3) -> OrderResult:
        last = None
        for _ in range(retries):
            if request["action"] == self.c.TRADE_ACTION_DEAL:   # fresh price before each try
                t = self.mt5.symbol_info_tick(request["symbol"])
                if t:
                    request["price"] = float(t.ask if request["type"] == self.c.ORDER_TYPE_BUY else t.bid)
            res = self.mt5.order_send(request)
            last = res
            if res is not None and res.retcode == self.c.TRADE_RETCODE_DONE:
                return OrderResult(ok=True, ticket=int(res.order), price=float(res.price), message="done")
            if getattr(res, "retcode", None) not in RETRY_RETCODES:
                break
            time.sleep(0.5)
        try:
            err = self.mt5.last_error()
        except Exception:
            err = ""
        return OrderResult(ok=False, message=f"retcode={getattr(last, 'retcode', None)} "
                                             f"{getattr(last, 'comment', '') or err}")

    def market_order(self, symbol, side, volume, sl, tp, magic, comment, deviation) -> OrderResult:
        spec = self.symbol_spec(symbol)
        req = {
            "action": self.c.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(volume),
            "type": self.c.ORDER_TYPE_BUY if side == BUY else self.c.ORDER_TYPE_SELL,
            "price": 0.0, "sl": round(sl, spec.digits), "tp": round(tp, spec.digits),
            "deviation": int(deviation), "magic": int(magic), "comment": comment[:31],
            "type_time": self.c.ORDER_TIME_GTC, "type_filling": self._filling(symbol),
        }
        res = self._send(req)
        if res.ok:
            # on hedging accounts the order ticket is the position ticket; confirm via comment
            for p in self.mt5.positions_get(symbol=symbol) or []:
                if p.magic == magic and str(p.comment).startswith(comment[:20]):
                    res.ticket = int(p.ticket)
                    break
        return res

    def modify_sl_tp(self, position: Position, sl: float, tp: float) -> OrderResult:
        spec = self.symbol_spec(position.symbol)
        req = {"action": self.c.TRADE_ACTION_SLTP, "position": position.ticket, "symbol": position.symbol,
               "sl": round(sl, spec.digits), "tp": round(tp, spec.digits), "magic": position.magic}
        return self._send(req, retries=2)

    def close(self, position: Position, volume: float | None = None, deviation: int = 20) -> OrderResult:
        req = {
            "action": self.c.TRADE_ACTION_DEAL, "position": position.ticket, "symbol": position.symbol,
            "volume": float(volume or position.volume),
            "type": self.c.ORDER_TYPE_SELL if position.side == BUY else self.c.ORDER_TYPE_BUY,
            "price": 0.0, "deviation": int(deviation), "magic": position.magic,
            "comment": "bot close", "type_time": self.c.ORDER_TIME_GTC,
            "type_filling": self._filling(position.symbol),
        }
        return self._send(req)

    def deals_since(self, since: datetime, magic: int, known_ids: set[int] | None = None) -> list[Deal]:
        out = []
        known_ids = known_ids or set()
        # ask a wide window (time-zone quirks), then filter on server time ourselves
        deals = self.mt5.history_deals_get(since - timedelta(days=1), datetime.now() + timedelta(days=2)) or []
        for d in deals:
            if d.entry not in (self.c.DEAL_ENTRY_OUT, self.c.DEAL_ENTRY_OUT_BY):
                continue
            if d.magic != magic and d.position_id not in known_ids:
                continue
            t = _to_datetime(d.time)
            if t < since:
                continue
            out.append(Deal(position_id=int(d.position_id), symbol=str(d.symbol), volume=float(d.volume),
                            profit=float(d.profit + d.commission + d.swap), time=t))
        return out
