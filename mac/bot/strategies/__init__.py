"""Strategy registry. To add your own: write a class with
`generate(view: MarketView) -> Signal | None` and register it below."""
from bot.strategies.trend_pullback import TrendPullback

STRATEGIES = {
    TrendPullback.name: TrendPullback,
}


def make_strategy(params: dict):
    name = params.get("name", "trend_pullback")
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; choose from {sorted(STRATEGIES)}")
    return STRATEGIES[name](params)
