import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.brokers.sim_broker import SimBroker  # noqa: E402
from bot.config import load_config  # noqa: E402
from bot.state import Store  # noqa: E402


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.yaml", env_file=None)


@pytest.fixture
def store():
    return Store(":memory:")


def flat_bars(n=400, price=1.1000, start=datetime(2025, 1, 6), step=0.0):
    """Bars with a gentle slope and a fixed range, for predictable tests."""
    rows = []
    for i in range(n):
        p = price + step * i
        rows.append((start + timedelta(hours=i), p, p + 0.0010, p - 0.0010, p, 100))
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


@pytest.fixture
def sim():
    data = {s: flat_bars() for s in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "XAUUSD")}
    data["USDJPY"] = flat_bars(price=150.0)
    data["XAUUSD"] = flat_bars(price=2400.0)
    return SimBroker(data, start_index=300, balance=10_000)
