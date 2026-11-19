"""Load config.yaml + .env into one object, with sanity checks."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # optional
    def load_dotenv(*_a, **_k):
        return False

LIVE_CONFIRM_PHRASE = "YES_TRADE_REAL_MONEY"


class ConfigError(ValueError):
    pass


class Config(dict):
    """dict with attribute access: cfg.risk.max_open_trades"""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
        return Config(value) if isinstance(value, dict) else value


def load_config(path: str | Path = "config.yaml", env_file: str | Path | None = ".env") -> Config:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file)
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = Config(raw)
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.get("mode") not in ("demo", "live"):
        raise ConfigError("mode must be 'demo' or 'live'")
    r, p, d = cfg.risk, cfg.profit, cfg.daily
    if not 0 < r.risk_per_trade_pct <= 5:
        raise ConfigError("risk.risk_per_trade_pct must be between 0 and 5")
    if r.max_open_trades < 1:
        raise ConfigError("risk.max_open_trades must be at least 1")
    if r.max_total_risk_pct < r.risk_per_trade_pct:
        raise ConfigError("risk.max_total_risk_pct must be >= risk_per_trade_pct")
    if not 0 < p.partial_close_fraction < 1:
        raise ConfigError("profit.partial_close_fraction must be between 0 and 1")
    if d.target_pct <= 0 or d.max_loss_pct <= 0:
        raise ConfigError("daily.target_pct and daily.max_loss_pct must be positive")
    if d.on_target not in ("close_all", "trail_tight"):
        raise ConfigError("daily.on_target must be close_all or trail_tight")
    if not 0 <= d.giveback_to < d.giveback_from <= 1:
        raise ConfigError("daily.giveback_to must be below giveback_from (both 0..1)")


def live_trading_confirmed() -> bool:
    return os.getenv("LIVE_TRADING_CONFIRM", "") == LIVE_CONFIRM_PHRASE
