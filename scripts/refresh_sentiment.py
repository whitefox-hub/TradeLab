"""Pull the Fear and Greed index into `sentiment_readings`. Plan.md §11.4.

Idempotent: it fetches the whole published history and upserts by day, so
running it twice changes nothing and running it after a gap fills the gap. Meant
for cron beside scripts/refresh_macro.sh.
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.collectors.sentiment import fetch, fetch_cmc, store
from tradelab.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    # 0 is the whole history. The default is small because the daily run needs
    # only the overlap; a first load passes --limit 0. It applies to
    # alternative.me alone: CoinMarketCap serves only its latest value, so that
    # series starts the day it was first collected and accumulates forward.
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    engine = sa.create_engine(Settings().database_url)
    reached = 0

    # Each source is fetched and stored on its own, so one failing does not cost
    # the other. An undocumented endpoint can start demanding a key without
    # notice, and losing the series that still works alongside it would turn one
    # outage into two.
    for label, pull in (("alternative.me", lambda: fetch(limit=args.limit)),
                        ("coinmarketcap", fetch_cmc)):
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

    # Non-zero only when *every* source failed, so cron notices an outage but
    # not a quiet day. Counting sources reached rather than rows written: zero
    # rows is the normal result once a series is up to date, and treating that
    # as a failure would page on success.
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(main())
