"""Remove events past the retention window. Plan.md §11.8.

`ingest_events.py` reads the same retention setting and skips anything past it; otherwise
the next ingest would re-insert what was pruned. Events are rebuildable from the
announcement Parquet; human annotations are not, so annotated events need --force-annotated.

    python scripts/prune_events.py [--dry-run] [--force-annotated]
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.config import Settings
from tradelab.events.retention import retention_days


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-annotated", action="store_true",
                        help="also delete events carrying a human annotation")
    args = parser.parse_args()

    engine = sa.create_engine(Settings().database_url)
    with engine.begin() as connection:
        days = retention_days(connection)
        rows = connection.execute(sa.text("""
            SELECT e.id, e.event_type, e.first_seen_at,
                   (SELECT count(*) FROM event_annotations an
                     WHERE an.event_id = e.id
                       AND an.kind = 'judgement') AS judgements,
                   e.first_seen_at < now() - make_interval(days => :d) AS past_retention
            FROM events e
        """), {"d": days}).mappings().all()

        doomed, kept_for_annotation = [], []
        for row in rows:
            if not row["past_retention"]:
                continue
            if row["judgements"] and not args.force_annotated:
                kept_for_annotation.append(row["id"])
                continue
            doomed.append(row["id"])

        print(f"retention      : {days} days")
        print(f"events         : {len(rows)}")
        print(f"to remove      : {len(doomed)}")
        print(f"kept, annotated: {len(kept_for_annotation)}")

        if args.dry_run or not doomed:
            return 0

        for table in ("event_annotations", "event_assets", "schedule_revisions",
                      "process_transitions"):
            connection.execute(
                sa.text(f"DELETE FROM {table} WHERE event_id = ANY(:ids)"),
                {"ids": doomed})
        removed = connection.execute(
            sa.text("DELETE FROM events WHERE id = ANY(:ids)"), {"ids": doomed}
        ).rowcount

    print(f"removed        : {removed}")
    print("Re-runnable: raise event_retention_days and re-run ingest_events.py "
          "to rebuild these from the announcement archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
