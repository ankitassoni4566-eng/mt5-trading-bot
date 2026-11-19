# Contributing

Thanks for wanting to help! Bug reports, ideas, new strategies and docs fixes are all welcome.

## Ground rules

- **Never share account details.** Don't post logins, passwords, `.env` files or real account numbers in issues or pull requests.
- **No profit claims.** Please don't add marketing that promises returns. Backtests on generated data only show the machinery works.
- **Safety first.** Changes must keep the safety rules intact: demo mode by default, the risk manager gate, no duplicate orders, and closed candles only.

## Development setup

The trading logic is the same in `mac/` and `windows/`. Make your change in one folder, mirror it in the other, and run the tests in both:

```bash
cd mac        # then repeat in windows/
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python run.py --backtest 90 --quiet
```

GitHub Actions runs the same checks on every push and pull request.

## Adding a strategy

1. Create `bot/strategies/your_strategy.py` with a class that has `generate(view) -> Signal | None`.
2. Register it in `bot/strategies/__init__.py`.
3. Add tests in `tests/`.
4. Mention it in the README.

## Pull requests

- Keep them focused: one feature or fix per PR.
- Describe what changed and how you tested it.
- All tests must pass in both folders.

## Ideas we'd love help with

See the **Roadmap** in the README: a news filter, more strategies, a web dashboard, and an MQL5 Expert Advisor version.
