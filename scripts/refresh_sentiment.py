"""Pull the Fear and Greed index into `sentiment_readings`. Plan.md §11.4.

Idempotent upsert by day; meant for cron beside scripts/refresh_macro.sh.

    python scripts/refresh_sentiment.py [--limit 0] [--cmc-history]
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.collectors.sentiment import (
    fetch,
    fetch_cmc,
    fetch_cmc_history,
    store,
)
from tradelab.config import Settings


def _report_budget(label: str) -> None:
    """Print the vendor quota and this run's spend (every run, so exhaustion is seen early)."""
    from tradelab.collectors.cmc import budget, spent

    allowance = budget()
    if allowance is None:
        print(f"{label}: {spent()} requests, keyless "
              "(no credit budget; the anonymous quota is per IP and unpublished)")
        return
    print(f"{label}: {spent()} requests this run \u00b7 "
          f"{allowance.used_month}/{allowance.limit_month} credits used this month "
          f"({allowance.share_used:.1%}), {allowance.left_month} left")


def main() -> int:
    parser = argparse.ArgumentParser()
    # alternative.me only; 0 is the whole history (first load).
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--cmc-history", action="store_true",
                        help="also page CoinMarketCap's full published history")
    args = parser.parse_args()

    engine = sa.create_engine(Settings().database_url)
    reached = 0

    # Sources are independent so one failing (e.g. suddenly requiring a key) keeps the other.
    sources = [("alternative.me", lambda: fetch(limit=args.limit)),
               ("coinmarketcap", fetch_cmc)]
    if args.cmc_history:
        sources.append(("coinmarketcap history", fetch_cmc_history))

    for label, pull in sources:
        try:
            readings = pull()
        except Exception as failure:
            print(f"{label}: FAILED — {failure}")
            continue
        if not readings:
            print(f"{label}: no readings returned")
            continue
        reached += 1
        written = store(engine, readings)
        newest = max(readings, key=lambda r: r.day)
        print(f"{label}: {written} changed, newest "
              f"{newest.day.date()} = {newest.value} ({newest.classification})")

    _report_budget("budget")
    # Fail only if every source failed; zero rows written is normal when up to date.
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(main())
