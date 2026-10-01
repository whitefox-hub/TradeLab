"""Pull CMC100 into `market_index_readings`. Plan.md §20, PREREG-005.

Idempotent; resumes from the first gap and stores what it got when the anonymous quota
runs out, so daily runs complete the backfill without a key.

    python scripts/refresh_market_index.py [--full]
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.collectors.cmc import QuotaReached
from tradelab.collectors.market_index import INCEPTION, fetch_history, resume_from, store
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
    parser.add_argument("--full", action="store_true",
                        help="start from inception rather than from the first gap")
    args = parser.parse_args()

    engine = sa.create_engine(Settings().database_url)
    since = INCEPTION if args.full else resume_from(engine)

    try:
        collected = fetch_history(since=since)
        print(f"{store(engine, collected)} changed, from {since}")
    except QuotaReached as stopped:
        print(f"{store(engine, stopped.collected)} changed from {since}, "
              f"then {stopped}")
    _report_budget("budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
