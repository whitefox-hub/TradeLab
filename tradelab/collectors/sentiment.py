"""The Fear and Greed index. Plan.md §11.4, §4.3.

A vendor index, and §11.4's objection to one stands: the method is not
auditable, it is composed partly of data already collected here, and a number
nobody can decompose cannot be debugged when it behaves oddly. It is collected
because it is useful to *see*, not because a study should condition on it, and
`sentiment_readings` is kept out of the point-in-time corpus for that reason.

What is enforced here is the stamp. One value per UTC day, published at that
day's midnight and not revised afterwards, so `available_at` is derivable rather
than guessed — which is the difference between a series a backtest can read
safely and one that quietly leaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa

ENDPOINT = "https://api.alternative.me/fng/"
SOURCE = "alternative.me/fng"

#: The five bands the index reports. Held here as well as in a database CHECK so
#: a sixth one appearing upstream fails on the way in, where the payload is
#: still available to look at, rather than as a constraint violation later.
CLASSIFICATIONS = (
    "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed",
)


@dataclass(frozen=True, slots=True)
class Reading:
    day: datetime
    value: int
    classification: str

    @property
    def available_at(self) -> datetime:
        """Midnight UTC of its own day: when the value was published.

        Not `now()`. Stamping a backfilled 2018 reading with the moment it was
        downloaded would make eight years of history look as though it arrived
        today, and every study filtering on availability would then see none of
        it.
        """
        return self.day


def fetch(limit: int = 0, client: httpx.Client | None = None) -> list[Reading]:
    """`limit=0` is the whole history, which is what the endpoint documents."""
    owned = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        payload = client.get(ENDPOINT, params={"limit": limit, "format": "json"}).json()
    finally:
        if owned:
            client.close()

    error = (payload.get("metadata") or {}).get("error")
    if error:
        raise RuntimeError(f"fear and greed endpoint returned an error: {error}")

    out: list[Reading] = []
    for row in payload.get("data") or []:
        classification = row["value_classification"]
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"unknown classification {classification!r}")
        value = int(row["value"])
        if not 0 <= value <= 100:
            raise ValueError(f"value {value} is outside the published 0-100 scale")
        out.append(Reading(
            day=datetime.fromtimestamp(int(row["timestamp"]), UTC),
            value=value,
            classification=classification,
        ))
    return out


def store(engine: sa.Engine, readings: list[Reading]) -> int:
    """Upsert by day. Returns the number of rows written.

    The index does not revise, so a day already stored should arrive identical;
    the upsert is here for the overlap between runs rather than for corrections.
    A value that *did* change would be a revision, and §4.3 says a revised series
    with no vintages cannot be read as it was known at the time — so this writes
    the new one and the change is visible in the audit of what moved, not hidden.
    """
    if not readings:
        return 0
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO sentiment_readings "
            "  (day, value, classification, source, available_at) "
            "VALUES (:d, :v, :c, :s, :a) "
            "ON CONFLICT (day) DO UPDATE SET "
            "  value = EXCLUDED.value, "
            "  classification = EXCLUDED.classification, "
            "  ingest_time = now()"
        ), [{"d": r.day.date(), "v": r.value, "c": r.classification,
             "s": SOURCE, "a": r.available_at} for r in readings])
    return len(readings)
