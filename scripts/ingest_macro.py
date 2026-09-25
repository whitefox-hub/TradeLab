"""Load the macro catalogue and its observations. Plan.md §4.7, §20.6.

Each observation becomes a `macro_releases` row with `actual` and `actual_at`
set and `consensus` left empty, because no free feed carries a forecast
consensus. The operator supplies those through the screen, and only ever for
releases that have not happened yet — a consensus recorded after the print is a
memory of one, and the database refuses it.

`available_at` is `actual_at` plus the series' measured publication lag, which
is the moment a subscriber could have acted rather than the moment the statistic
refers to.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, time, timedelta

import sqlalchemy as sa

from tradelab.collectors.macro import CATALOGUE, observations
from tradelab.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2019-01-01")
    args = parser.parse_args()

    cutoff = datetime.fromisoformat(args.since).date()
    engine = sa.create_engine(Settings().database_url)

    with engine.begin() as connection:
        for spec in CATALOGUE:
            connection.execute(sa.text(
                "INSERT INTO macro_series (code, name, kind, source, has_vintages, "
                "is_revised, session_close, publish_lag_s) "
                "VALUES (:c, :n, :k, :s, :v, :r, :sc, :l) "
                "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, "
                "  has_vintages = EXCLUDED.has_vintages, "
                "  is_revised = EXCLUDED.is_revised, "
                "  publish_lag_s = EXCLUDED.publish_lag_s"
            ), {"c": spec.code, "n": spec.name, "k": spec.kind, "s": spec.source,
                "v": spec.has_vintages, "r": spec.is_revised,
                "sc": spec.session_close, "l": spec.publish_lag_s})

    total = 0
    for spec in CATALOGUE:
        rows = [(day, value) for day, value in observations(spec.code) if day >= cutoff]
        with engine.begin() as connection:
            known = set(connection.execute(sa.text(
                "SELECT scheduled_for FROM macro_releases WHERE series = :s"),
                {"s": spec.code}).scalars().all())
            inserted = 0
            for day, value in rows:
                # Midnight UTC as the scheduled instant for a daily series. The
                # real release time is in `session_close` and belongs on the
                # release, not on the observation date — stated rather than
                # silently assumed, because it is the difference between an
                # event at 08:30 New York and one at midnight.
                scheduled = datetime.combine(day, time(0, 0), tzinfo=UTC)
                if scheduled in known:
                    continue
                connection.execute(sa.text(
                    "INSERT INTO macro_releases (id, series, scheduled_for, actual, "
                    "actual_at, available_at) VALUES (:i, :s, :d, :a, :t, :v)"
                ), {"i": uuid.uuid4(), "s": spec.code, "d": scheduled, "a": value,
                    "t": scheduled,
                    "v": scheduled + timedelta(seconds=spec.publish_lag_s)})
                inserted += 1
        total += inserted
        print(f"  {spec.code:10s} {inserted:5d} new of {len(rows)} since {cutoff}")

    print(f"{total} releases inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
