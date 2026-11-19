"""The real MT5 connector, driven by a fake MT5 API.

The fake deliberately exposes NO named constants and returns plain lists instead of
numpy arrays — the way a bridge (Mac) may behave — so this checks the connector works
with both the official Windows package and the Mac bridge.
"""
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from bot.brokers.mt5_broker import MT5Broker, rates_to_frame
from bot.models import BUY, SELL

Info = namedtuple("Info", "point digits trade_tick_size trade_tick_value volume_min volume_max "
                          "volume_step trade_stops_level visible filling_mode trade_mode name")
TickT = namedtuple("TickT", "bid ask time")
Acc = namedtuple("Acc", "login balance equity currency trade_mode server")
Term = namedtuple("Term", "connected")
Res = namedtuple("Res", "retcode order price comment")
Pos = namedtuple("Pos", "ticket symbol type volume price_open sl tp profit magic time price_current comment")
DealT = namedtuple("DealT", "entry magic position_id symbol volume profit commission swap time")

T0 = int(datetime(2026, 3, 2, 10).timestamp())


class FakeMT5:
    """Minimal MT5 API with official numeric codes but no constant attributes."""

    def __init__(self):
        self.requests, self.positions, self.deals = [], {}, []
        self.next_ticket = 500

    def initialize(self, **kw):
        return True

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return Term(True)

    def account_info(self):
        return Acc(42, 10_000.0, 10_050.0, "USD", 0, "Demo-Server")

    def symbols_get(self):
        return [Info(0.00001, 5, 0.00001, 1.0, 0.01, 100, 0.01, 0, True, 1, 4, "EURUSD"),
                Info(0.01, 2, 0.01, 1.0, 0.01, 100, 0.01, 0, True, 1, 0, "CLOSEDSYM")]

    def symbol_info(self, s):
        return Info(0.00001, 5, 0.00001, 1.0, 0.01, 100, 0.01, 0, True, 2, 4, s)   # IOC only

    def symbol_select(self, s, on):
        return True

    def symbol_info_tick(self, s):
        return TickT(1.10000, 1.10012, T0)

    def copy_rates_from_pos(self, s, tf, start, count):
        assert start == 1 and tf == 16385            # closed bars only, H1 code
        return [(T0 - 3600 * (count - i), 1.1, 1.101, 1.099, 1.1005, 100, 12, 0) for i in range(count)]

    def positions_get(self, symbol=None):
        return [p for p in self.positions.values() if symbol in (None, p.symbol)]

    def order_send(self, req):
        self.requests.append(dict(req))
        if req["action"] == 1 and "position" not in req:          # open
            self.next_ticket += 1
            t = self.next_ticket
            self.positions[t] = Pos(t, req["symbol"], req["type"], req["volume"], req["price"], req["sl"],
                                    req["tp"], 0.0, req["magic"], T0, req["price"], req["comment"])
            return Res(10009, t, req["price"], "done")
        if req["action"] == 1:                                       # close / partial close
            p = self.positions[req["position"]]
            left = round(p.volume - req["volume"], 8)
            if left <= 0:
                del self.positions[p.ticket]
            else:
                self.positions[p.ticket] = p._replace(volume=left)
            self.deals.append(DealT(1, p.magic, p.ticket, p.symbol, req["volume"], 25.0, -1.0, 0.0, T0 + 60))
            return Res(10009, 0, req["price"], "done")
        if req["action"] == 6:                                       # modify SL/TP
            p = self.positions[req["position"]]
            self.positions[p.ticket] = p._replace(sl=req["sl"], tp=req["tp"])
            return Res(10009, 0, 0.0, "done")
        return Res(10013, 0, 0.0, "invalid")

    def history_deals_get(self, a, b):
        return self.deals + [DealT(0, 270419, 1, "EURUSD", 0.1, 0, 0, 0, T0)]    # an entry deal: ignored


@pytest.fixture
def broker():
    return MT5Broker(backend=FakeMT5())


def test_connect_and_account(broker):
    assert broker.connect() and broker.is_connected()
    acc = broker.account()
    assert acc.equity == 10_050.0 and acc.is_demo


def test_symbols_and_spec(broker):
    assert broker.all_symbols() == ["EURUSD"]            # only fully tradable symbols
    spec = broker.symbol_spec("EURUSD")
    assert spec.volume_step == 0.01 and spec.base_quote == ("EUR", "USD")


def test_bars_from_plain_lists(broker):
    df = broker.bars("EURUSD", "H1", 50)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 50 and df["time"].is_monotonic_increasing


def test_rates_from_numpy_record_array():
    dt = np.dtype([(n, "<i8" if n in ("time", "tick_volume", "spread", "real_volume") else "<f8")
                   for n in ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]])
    arr = np.array([(T0, 1.1, 1.2, 1.0, 1.15, 10, 1, 0)], dtype=dt)
    df = rates_to_frame(arr)
    assert df["close"].iloc[0] == 1.15 and df["volume"].iloc[0] == 10


def test_full_trade_lifecycle(broker):
    fake = broker.mt5
    res = broker.market_order("EURUSD", BUY, 0.10, 1.095, 1.11, 270419, "bot EURUSD-buy-1", 20)
    assert res.ok and res.ticket in fake.positions
    open_req = fake.requests[-1]
    assert open_req["type"] == 0 and open_req["type_filling"] == 1 and open_req["price"] == 1.10012

    [pos] = broker.positions(270419)
    assert pos.side == BUY and pos.volume == 0.10

    assert broker.modify_sl_tp(pos, 1.1001, pos.tp).ok
    assert broker.positions(270419)[0].sl == 1.1001

    assert broker.close(pos, 0.05).ok                     # partial close
    assert fake.requests[-1]["type"] == 1                # sell to close a buy
    assert broker.positions(270419)[0].volume == 0.05

    deals = broker.deals_since(datetime(2026, 3, 2), 270419)
    assert len(deals) == 1 and deals[0].profit == 24.0    # profit + commission + swap; entry deal ignored


def test_close_sell_uses_buy(broker):
    broker.market_order("EURUSD", SELL, 0.1, 1.105, 1.09, 270419, "bot x", 20)
    [pos] = broker.positions(270419)
    assert pos.side == SELL
    broker.close(pos)
    assert broker.mt5.requests[-1]["type"] == 0
    assert broker.positions(270419) == []


def test_server_time_from_tick(broker):
    expected = datetime.fromtimestamp(T0, tz=timezone.utc).replace(tzinfo=None)
    assert abs(broker.now() - expected) < timedelta(seconds=1)
