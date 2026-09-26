"""Pull the Fear and Greed index into `sentiment_readings`. Plan.md §11.4.

Idempotent: it fetches the whole published history and upserts by day, so
running it twice changes nothing and running it after a gap fills the gap. Meant
for cron beside scripts/refresh_macro.sh.
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.collectors.sentiment import fetch, store
from tradelab.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    # 0 is the whole history. The default is small because the daily run needs
    # only the overlap; a first load passes --limit 0.
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    readings = fetch(limit=args.limit)
    if not readings:
        print("no readings returned")
        return 1

    engine = sa.create_engine(Settings().database_url)
    written = store(engine, readings)
    newest = max(readings, key=lambda r: r.day)
    print(f"{written} readings changed, newest "
          f"{newest.day.date()} = {newest.value} ({newest.classification})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
