"""CMC100, the broad market index PREREG-005 measures against. §20, §4.3.

A hundred constituents, capitalisation-weighted, based at 100 on 2024-01-01.
Collected because the operator's market trend is a claim about the market and
not about Bitcoin — BTCUSDT was registered first only because it was already
here, and PREREG-005's own confounds said so.

**It is a yardstick, not an instrument.** The index rebalances, so its return
includes rebalancing and is not a return anyone earned. Nothing may trade it;
it exists to say which way the market went.

Requests go through `tradelab.collectors.cmc`, which owns the retry and the two
throttles. The history endpoint caps `count` at ten and pages with `time_start`,
both undocumented and found by asking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from tradelab.collectors.cmc import call
from tradelab.collectors.cmc import client as cmc_client

SOURCE = "cmc100"

#: The endpoint's own ceiling. Ten — `count=100` is refused outright rather
#: than clamped, which is the better of the two failures and how it was found.
PAGE = 10

#: The index is based at 100 here; earlier `time_start` values clamp to it.
INCEPTION = date(2024, 1, 1)


@dataclass(frozen=True, slots=True)
class Point:
    day: date
    value: float
    #: The snapshot's own timestamp, which is when it was knowable. Never
    #: `now()`: stamping a backfilled 2024 point with the download time would
    #: make two years of history look as though it arrived today, and every
    #: study filtering on availability would see none of it.
    available_at: datetime


def _point(update_time: str, value: Any) -> Point:
    stamp = datetime.fromisoformat(
        update_time.replace("Z", "+00:00")).astimezone(UTC)
    number = float(value)
    if number <= 0:
        raise ValueError(f"index value {number} is not positive")
    return Point(day=stamp.date(), value=number, available_at=stamp)


#: The series is built from the historical endpoint alone, and `latest` is
#: deliberately unused.
#:
#: `latest` is an intraday quote — it refreshes every five minutes — while
#: `historical` gives one snapshot per day at 00:00Z. Mixing them would put two
#: different definitions in one column, and the day's row would mean midnight
#: on every day but the current one.
#:
#: Worse, the SDK types `latest.last_update` as a **date** and drops the time,
#: so an 18:00 value arrives looking like a midnight one. Stored with the day's
#: own midnight as its availability, that claims a value was knowable eighteen
#: hours before it existed — a look-ahead introduced by a type, and invisible
#: afterwards because the column would hold a perfectly ordinary timestamp.
#:
#: `historical` already includes the current day, so nothing is lost.
LATEST_IS_INTRADAY = True


def fetch_history(sdk: Any | None = None, since: date = INCEPTION) -> list[Point]:
    """Every daily point from `since`, ten at a time, resumable.

    Paging stops when a page adds nothing new, so a server that clamps
    `time_start` ends the loop rather than returning the same ten rows forever.

    On running out of anonymous quota `call` raises `QuotaReached` carrying the
    points already collected, so the ceiling costs a re-run and not the pages
    already fetched. The first version let that error escape and threw away
    ninety pages with it, and every re-run then restarted from inception and
    hit the same wall in the same place.
    """
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

    The first version returned the day after the newest point, which is wrong
    the moment anything is stored out of order — and something always is, since
    the daily run writes today before any backfill has reached it. It then
    reported the backfill complete with two years missing, and the table looked
    full because its newest row was current.

    Asked as a gap query against the calendar, so it is the first hole and not
    the far end. Resumed from the database rather than from a marker file: the
    database is what a re-run has to agree with anyway, and a marker that
    disagrees with it is a gap nobody sees.
    """
    with engine.connect() as conn:
        # CAST rather than `::`, which SQLAlchemy's text() reads as the start
        # of another named parameter when it follows one.
        missing = conn.execute(sa.text(
            "SELECT CAST(min(d) AS date) FROM generate_series("
            "  CAST(:since AS date), CAST(now() AS date), interval '1 day') AS d "
            "WHERE CAST(d AS date) NOT IN ("
            "  SELECT day FROM market_index_readings WHERE source = :s)"
        ), {"since": INCEPTION, "s": SOURCE}).scalar()
    return missing or datetime.now(UTC).date()


def store(engine: sa.Engine, points: list[Point]) -> int:
    """Upsert by (day, source). Returns the rows that actually changed.

    A day already stored with the same value is left alone rather than
    rewritten with a fresh `ingest_time` — otherwise that column would come to
    mean "when the cron last ran" instead of "when this value last changed",
    and only the second is a question it can answer.
    """
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
