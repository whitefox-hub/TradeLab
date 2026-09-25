"""Remove events that no longer have effect. Plan.md §11.8.

Two things have to happen together or the removal does not hold: the rows go,
and the ingestor stops bringing them back. `ingest_events.py` is idempotent by
article id, so deleting alone would leave the next run re-inserting everything —
a prune that undoes itself on the next cron tick is worse than none, because it
looks done.

So both read the same retention window, stored as an operator setting and
editable on the Parameters screen, and the ingestor skips anything already past
it.

**What is lost and what is not.** Events are derived: the announcement Parquet is
the source of truth and re-running the ingestor with a longer retention rebuilds
them exactly. Human annotations are not derived and cannot be rebuilt, so this
refuses to delete an event carrying one unless told twice.
"""

from __future__ import annotations

import argparse

import sqlalchemy as sa

from tradelab.config import Settings
from tradelab.news.expiry import resolve
from tradelab.news.retention import retention_days


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
            SELECT e.id, e.event_type, e.shape, e.first_seen_at, e.resolved_at,
                   (SELECT min(sr.scheduled_for) FROM schedule_revisions sr
                     WHERE sr.event_id = e.id) AS scheduled_for,
                   (SELECT pt.state FROM process_transitions pt
                     WHERE pt.event_id = e.id
                     ORDER BY pt.sequence DESC LIMIT 1) AS latest_state,
                   (SELECT count(*) FROM event_annotations an
                     WHERE an.event_id = e.id
                       AND an.kind = 'judgement') AS judgements,
                   e.first_seen_at < now() - make_interval(days => :d) AS past_retention
            FROM events e
        """), {"d": days}).mappings().all()

        doomed, kept_for_annotation = [], []
        for row in rows:
            expiry = resolve(
                event_type=row["event_type"], shape=row["shape"],
                first_seen_at=row["first_seen_at"], resolved_at=row["resolved_at"],
                scheduled_for=row["scheduled_for"], latest_state=row["latest_state"],
            )
            if expiry.active or not row["past_retention"]:
                continue
            # A judgement is the one thing here that cannot be rebuilt from the
            # Parquet archive, so it is worth more than the row it hangs on.
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
