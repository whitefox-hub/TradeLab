"""CMC100, the market index PREREG-005 measures against (§20, §4.3).

A yardstick, not an instrument: its return includes rebalancing, so nothing trades it.
History endpoint (undocumented): `count` capped at ten, pages with `time_start`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from tradelab.collectors.cmc import call
from tradelab.collectors.cmc import client as cmc_client

SOURCE = "cmc100"

PAGE = 10

INCEPTION = date(2024, 1, 1)


@dataclass(frozen=True, slots=True)
class Point:
    day: date
    value: float
    available_at: datetime


def _point(update_time: str, value: Any) -> Point:
    stamp = datetime.fromisoformat(
        update_time.replace("Z", "+00:00")).astimezone(UTC)
    number = float(value)
    if number <= 0:
        raise ValueError(f"index value {number} is not positive")
    return Point(day=stamp.date(), value=number, available_at=stamp)


LATEST_IS_INTRADAY = True


def fetch_history(sdk: Any | None = None, since: date = INCEPTION) -> list[Point]:
    """Every daily point from `since`, ten at a time.

    Stops when a page adds nothing new (server may clamp `time_start`). On quota exhaustion
    `call` raises `QuotaReached` carrying the points already collected."""
    sdk = sdk or cmc_client()
    out: list[Point] = []
    seen: set[date] = set()
    cursor = max(since, INCEPTION)
    today = datetime.now(UTC).date()
    while cursor <= today:
        rows = call(sdk.cmc_index.index_cmc100_historical, collected=out,
                    time_start=f"{cursor.isoformat()}T00:00:00Z",
                    count=str(PAGE)).data
        fresh = [p for p in (_point(r.update_time, r.value) for r in rows)
                 if p.day not in seen]
        if not fresh:
            break
        seen.update(p.day for p in fresh)
        out += fresh
        cursor = max(p.day for p in fresh) + timedelta(days=1)
    return out


def resume_from(engine: sa.Engine) -> date:
    """The earliest day at or after inception that is **not** stored.

    A gap query, not "day after newest": the daily run writes today before backfill does."""
    with engine.connect() as conn:
        missing = conn.execute(sa.text(
            "SELECT CAST(min(d) AS date) FROM generate_series("
            "  CAST(:since AS date), CAST(now() AS date), interval '1 day') AS d "
            "WHERE CAST(d AS date) NOT IN ("
            "  SELECT day FROM market_index_readings WHERE source = :s)"
        ), {"since": INCEPTION, "s": SOURCE}).scalar()
    return missing or datetime.now(UTC).date()


def store(engine: sa.Engine, points: list[Point]) -> int:
    """Upsert by (day, source); returns rows changed. Identical values are skipped so
    `ingest_time` means "value last changed"."""
    if not points:
        return 0
    with engine.begin() as conn:
        written = conn.execute(sa.text(
            "INSERT INTO market_index_readings (day, source, value, available_at) "
            "SELECT * FROM unnest("
            "  CAST(:days AS date[]), CAST(:sources AS text[]), "
            "  CAST(:values AS numeric[]), CAST(:avail AS timestamptz[])) "
            "ON CONFLICT (day, source) DO UPDATE SET "
            "  value = EXCLUDED.value, available_at = EXCLUDED.available_at, "
            "  ingest_time = now() "
            "WHERE market_index_readings.value IS DISTINCT FROM EXCLUDED.value "
            "RETURNING day"
        ), {
            "days": [p.day for p in points],
            "sources": [SOURCE] * len(points),
            "values": [p.value for p in points],
            "avail": [p.available_at for p in points],
        }).rowcount
    return max(written, 0)
