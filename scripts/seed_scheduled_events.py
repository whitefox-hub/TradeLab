"""Known future events, which no announcement feed publishes. Plan.md §11.6.

The collectors read what a venue has already said. A halving three years out was
never announced by anyone — it is a property of the protocol — and the same holds
for scheduled unlocks, mainnet dates and policy meetings.

These are the events §11.6 is actually about. For a scheduled event the tradeable
object is the **approach** rather than the arrival: risk appetite builds or drains
as the date nears, and that anticipation is only measurable if the date is in the
system before it happens. An event added afterwards can be studied but never
traded, which is the difference this file exists to close.

Each entry carries `confidence`, because a protocol date and a rumoured listing
are not the same claim. `schedule_revisions` keeps the date as believed at the
time, so a postponement leaves both the old belief and the new one — computing
"days until" from the date it eventually happened on assumes the market knew a
date it did not (§11.6).
"""

from __future__ import annotations

import argparse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa

from tradelab.config import Settings


@dataclass(frozen=True, slots=True)
class Scheduled:
    key: str
    event_type: str
    title: str
    scheduled_for: datetime
    assets: tuple[str, ...]
    confidence: str
    source: str


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


#: `fixed` is a date the protocol or an institution has committed to. `estimated`
#: is a projection from a known rate — the halving is block-height based, so its
#: calendar date moves with hash rate and is an estimate however certain the
#: event is. Recording that difference is the point of the column.
CALENDAR: tuple[Scheduled, ...] = (
    Scheduled("btc-halving-2028", "halving",
              "Bitcoin fourth halving, block 1,050,000",
              _utc(2028, 4, 20), ("BTCUSDT",), "estimated",
              "block height projection at current hash rate"),
    Scheduled("ltc-halving-2027", "halving",
              "Litecoin halving, block 3,150,000",
              _utc(2027, 8, 6), ("LTCUSDT",), "estimated",
              "block height projection"),
    Scheduled("bch-halving-2028", "halving",
              "Bitcoin Cash halving",
              _utc(2028, 4, 10), ("BCHUSDT",), "estimated",
              "block height projection"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = sa.create_engine(Settings().database_url)
    inserted = updated = 0

    with engine.begin() as connection:
        author = connection.execute(sa.text("SELECT id FROM users LIMIT 1")).scalar()
        for entry in CALENDAR:
            marker = f"calendar:{entry.key}"
            existing = connection.execute(sa.text(
                "SELECT id FROM events WHERE cluster_method = :m"), {"m": marker}
            ).scalar()

            if existing is None:
                if args.dry_run:
                    print(f"  would add  {entry.key:20s} {entry.scheduled_for.date()}")
                    inserted += 1
                    continue
                event_id = uuid.uuid4()
                connection.execute(sa.text(
                    "INSERT INTO events (id, event_type, shape, first_seen_at, "
                    "latest_revision, cluster_method, code_hash) "
                    "VALUES (:i, :t, 'schedule', now(), 1, :m, 'calendar')"
                ), {"i": event_id, "t": entry.event_type, "m": marker})
                for asset in entry.assets:
                    connection.execute(sa.text(
                        "INSERT INTO event_assets (event_id, revision, asset, "
                        "expected_direction) VALUES (:e, 1, :a, 0)"
                    ), {"e": event_id, "a": asset})
                connection.execute(sa.text(
                    "INSERT INTO event_annotations (id, event_id, kind, "
                    "available_at, payload, author) VALUES (:i, :e, 'correction', "
                    "now(), CAST(:p AS jsonb), :u)"
                ), {"i": uuid.uuid4(), "e": event_id,
                    "p": f'{{"title": "{entry.title}", "from_calendar": true}}',
                    "u": author})
                inserted += 1
            else:
                event_id = existing

            # A date already believed is left alone; a changed one is appended as
            # a new revision rather than overwritten, so the old belief survives.
            latest = connection.execute(sa.text(
                "SELECT revision, scheduled_for FROM schedule_revisions "
                "WHERE event_id = :e ORDER BY revision DESC LIMIT 1"), {"e": event_id}
            ).first()
            if latest is not None and latest.scheduled_for == entry.scheduled_for:
                continue
            if args.dry_run:
                updated += 1
                continue
            connection.execute(sa.text(
                "INSERT INTO schedule_revisions (event_id, revision, available_at, "
                "scheduled_for, confidence, source) "
                "VALUES (:e, :r, now(), :s, :c, :src)"
            ), {"e": event_id, "r": (latest.revision + 1) if latest else 1,
                "s": entry.scheduled_for, "c": entry.confidence, "src": entry.source})
            updated += 1

    print(f"events added {inserted}, schedule revisions written {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
