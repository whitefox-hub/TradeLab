"""Load the macro catalogue and its observations into `macro_releases`. Plan.md §4.7, §20.6.

`consensus` stays empty (no free feed has it; the DB refuses one entered after the print).
`available_at` = `actual_at` + the series' publication lag, i.e. when one could act.

    python scripts/ingest_macro.py [--since 2019-01-01]
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
                # Midnight UTC for a daily series; the real release time lives in
                # `session_close`, not on the observation date.
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
