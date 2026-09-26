"""Pull CMC100 into `market_index_readings`. Plan.md §20, PREREG-005.

Idempotent and resumable. The daily run wants `--latest`, which is one request.
`--history` pages from wherever the table left off and stops when the anonymous
quota runs out, storing what it got — so running it on successive days walks
backfill to completion without a key.
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.collectors.cmc import QuotaReached
from tradelab.collectors.market_index import INCEPTION, fetch_history, resume_from, store
from tradelab.config import Settings


def _report_budget(label: str) -> None:
    """What the vendor says, and what this run spent. Free to ask.

    Printed on every run rather than only when something is wrong: a quota is
    the kind of thing nobody looks at until it has already stopped the
    collection, and by then the series has a hole that looks like a quiet
    market.
    """
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

    # Only the historical endpoint. `latest` is an intraday quote and the SDK
    # drops its time of day, so a value published at 18:00 would be stored as
    # knowable at midnight. The historical series already includes today.
    try:
        collected = fetch_history(since=since)
        print(f"{store(engine, collected)} changed, from {since}")
    except QuotaReached as stopped:
        # Stored before reporting, so the ceiling costs a re-run and not the
        # pages already fetched.
        print(f"{store(engine, stopped.collected)} changed from {since}, "
              f"then {stopped}")
    _report_budget("budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
