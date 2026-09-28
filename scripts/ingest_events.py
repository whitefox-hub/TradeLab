"""Promote captured announcements into events. Plan.md §11.1, §11.7.

`event_assets` carries the names; `shape` (§11.7) comes from the class; `code_hash`
versions the taxonomy that built the row. Idempotent by `article_id`; corrections are
annotations, never edits.

    python scripts/ingest_events.py [--dry-run] [--retention-days N]
"""

from __future__ import annotations

import argparse
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import sqlalchemy as sa

from tradelab.collectors.binance import announcements as taxonomy
from tradelab.config import Settings
from tradelab.events.retention import retention_days

#: §11.7 shape per announcement class; delistings carry a date, hence `schedule`.
SHAPES: dict[str, str] = {
    "futures_listing": "shock",
    "spot_listing": "shock",
    "seed_tag": "shock",
    "futures_delivery": "schedule",
    "futures_delisting": "schedule",
    "spot_delisting": "schedule",
    "margin_change": "shock",
    "trading_halt": "shock",
    "trading_resume": "shock",
}

INGESTED = frozenset(SHAPES)


def taxonomy_hash() -> str:
    """Hash of the taxonomy module source, stamped as `code_hash`."""
    source = Path(taxonomy.__file__).read_bytes()
    return hashlib.sha256(source).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retention-days", type=int, default=None,
                        help="Override the stored window. Raising it rebuilds "
                             "events a prune removed.")
    args = parser.parse_args()

    rows = pl.read_parquet(
        args.root / "announcements" / "venue=binance" / "asset=_all" / "**/*.parquet"
    )
    rows = rows.with_columns(
        pl.col("title").map_elements(taxonomy.classify, return_dtype=pl.Utf8).alias("cls"),
        pl.col("title").map_elements(
            taxonomy.extract_assets, return_dtype=pl.List(pl.Utf8)).alias("assets"),
    ).filter(pl.col("cls").is_in(list(INGESTED)))

    # Same window `prune_events.py` deletes by; otherwise the next run undoes the prune.
    engine = sa.create_engine(Settings().database_url)
    with engine.connect() as connection:
        days = args.retention_days or retention_days(connection)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    before = rows.height
    rows = rows.filter(pl.col("available_at") >= cutoff)
    print(f"retention {days}d: {before - rows.height} older announcements skipped")

    code_hash = taxonomy_hash()
    print(f"{rows.height} classified announcements, taxonomy {code_hash}")
    if args.dry_run:
        print(rows.group_by("cls").len().sort("len", descending=True))
        return 0

    inserted = skipped = 0
    with engine.begin() as connection:
        existing = set(connection.execute(sa.text(
            "SELECT cluster_method FROM events WHERE cluster_method LIKE 'article:%'"
        )).scalars().all())

        for record in rows.iter_rows(named=True):
            key = f"article:{record['article_id']}"
            if key in existing:
                # Backfill headline on rows written before the column existed.
                connection.execute(sa.text(
                    "UPDATE events SET headline = :hl "
                    "WHERE cluster_method = :c AND headline IS NULL"
                ), {"hl": record["title"], "c": key})
                skipped += 1
                continue
            event_id = uuid.uuid4()
            connection.execute(sa.text(
                "INSERT INTO events (id, event_type, shape, first_seen_at, "
                "headline, latest_revision, cluster_method, code_hash) "
                "VALUES (:i, :t, :s, :f, :hl, 1, :c, :h)"
            ), {"i": event_id, "t": record["cls"], "s": SHAPES[record["cls"]],
                "hl": record["title"],
                # `available_at`, not `event_time`: first actionable moment (§11.2).
                "f": record["available_at"], "c": key, "h": code_hash})

            for asset in record["assets"] or []:
                connection.execute(sa.text(
                    "INSERT INTO event_assets (event_id, revision, asset, "
                    "expected_direction) VALUES (:e, 1, :a, 0) "
                    "ON CONFLICT DO NOTHING"
                ), {"e": event_id, "a": taxonomy.base_ticker(asset)})
            inserted += 1

    print(f"inserted {inserted}, already present {skipped}")
    print(f"as of {datetime.now(UTC).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
