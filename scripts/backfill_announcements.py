"""Backfill the Binance announcement history into the Parquet archive.

Venue-wide feed, so the `_all` asset key (§3.2). Partitioned by announcement month, not
run date, so a re-run rewrites the same files instead of duplicating.

    python scripts/backfill_announcements.py [--since 2019-01-01]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from tradelab.collectors.binance.announcements import BinanceAnnouncements
from tradelab.storage.paths import Partition


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet"))
    parser.add_argument("--since", default="2019-01-01")
    args = parser.parse_args()

    collector = BinanceAnnouncements()
    start = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    end = datetime.now(UTC)

    frames = [batch.frame for batch in collector.backfill("_all", start, end)]
    if not frames:
        print("no announcements returned")
        return 1

    rows = pl.concat(frames).unique(subset=["article_id"]).sort("event_time")
    written = 0
    for (year, month), group in rows.group_by(
        pl.col("event_time").dt.year().alias("y"), pl.col("event_time").dt.month().alias("m")
    ):
        day = datetime(int(year), int(month), 1, tzinfo=UTC).date()
        path = Partition("announcements", "binance", "_all", day).absolute_path(args.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        group.sort("event_time").write_parquet(path)
        written += group.height

    by_class = rows.group_by("event_class").len().sort("len", descending=True)
    print(f"{written} announcements -> {args.root}")
    print(f"{rows['event_time'].min()} .. {rows['event_time'].max()}")
    print(by_class)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
