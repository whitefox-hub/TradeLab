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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa

from tradelab.collectors.cmc import call
from tradelab.collectors.cmc import client as cmc_client

ENDPOINT = "https://api.alternative.me/fng/"
SOURCE = "alternative.me/fng"

#: CoinMarketCap's own index, from the endpoint its charts page calls.
#:
#: A different index, not a second opinion about this one: different method and
#: different number — 72 against 74 for 2026-09-26 — so it is its own series,
#: named for where it came from.
#:
#: **It revises during the day.** alternative.me publishes once at midnight UTC
#: and leaves it; this one carried an update_time of 17:53 on the day it was
#: added. So a stored row is that day's *latest* reading and not a vintage of
#: earlier ones, and `available_at` is its update_time rather than the day's
#: start. A study filtering on availability therefore sees today's value only
#: after it was published, and does not see the earlier intraday values at all —
#: less information than existed, which is the safe direction for that error.
#: The five bands both indices report. Held here as well as in a database
#: CHECK so a sixth appearing upstream fails on the way in, where the payload
#: is still available to look at, rather than as a constraint violation later.
CLASSIFICATIONS = (
    "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed",
)

#: CoinMarketCap's own two endpoints disagree about capitalisation: `latest`
#: says "Extreme Greed" and `historical` says "Extreme greed". Matched without
#: case and stored in one form, so the same band does not become two values in
#: a group-by — which is how a band ends up with half its days missing and
#: nobody notices, because both spellings look right.
_CANONICAL = {name.casefold(): name for name in CLASSIFICATIONS}


def normalise(classification: str) -> str:
    """The canonical band name, or an error naming what arrived.

    Case is forgiven; anything else is not. The first CMC backfill failed here
    with `unknown classification 'Extreme greed'` rather than writing 1185 rows
    nobody would have re-read.
    """
    canonical = _CANONICAL.get(classification.strip().casefold())
    if canonical is None:
        raise ValueError(f"unknown classification {classification!r}")
    return canonical


CMC_SOURCE = "coinmarketcap"

#: The history endpoint returns nothing above roughly 500 and pages with
#: `start`. Both undocumented and found by asking: 1000 came back empty rather
#: than clamped, so a caller that assumed a larger page would have silently
#: collected nothing and reported success.
CMC_PAGE = 500

#: CoinMarketCap **revises during the day**. alternative.me publishes once at
#: midnight UTC and leaves it; CMC's latest endpoint carried 72 at 17:53 and 73
#: at 18:08 on the day it was added. So a row from `latest` is that moment's
#: reading and `available_at` is its update_time, while a row from `historical`
#: is the day's close and becomes knowable the following midnight.
#:
#: A study filtering on availability therefore sees today's value only after it
#: was published, and does not see the earlier intraday values at all — less
#: information than existed, which is the safe direction for that error.


@dataclass(frozen=True, slots=True)
class Reading:
    day: datetime
    value: int
    classification: str
    source: str = SOURCE
    #: When this value became knowable. Defaults to the day's own midnight,
    #: which is when alternative.me publishes; CMC supplies its update_time.
    #:
    #: Never `now()`. Stamping a backfilled 2018 reading with the moment it was
    #: downloaded would make eight years of history look as though it arrived
    #: today, and every study filtering on availability would see none of it.
    published_at: datetime | None = None

    @property
    def available_at(self) -> datetime:
        return self.published_at or self.day


def fetch_cmc(sdk: Any | None = None) -> list[Reading]:
    """CoinMarketCap's index, latest only.

    Keyless: this is the path its own charts page calls. Checked rather than
    trusted all the same — an undocumented endpoint can change shape without
    notice, and a collector that quietly returns nothing leaves a series that
    simply stops, which looks exactly like a flat market.
    """
    sdk = sdk or cmc_client()
    data = call(sdk.global_metrics.fearandgreed_latest).data

    classification = normalise(data.value_classification)
    value = int(data.value)
    if not 0 <= value <= 100:
        raise ValueError(f"value {value} is outside the published 0-100 scale")

    published = datetime.fromisoformat(
        data.update_time.replace("Z", "+00:00")).astimezone(UTC)
    return [Reading(
        day=datetime(published.year, published.month, published.day, tzinfo=UTC),
        value=value, classification=classification,
        source=CMC_SOURCE, published_at=published,
    )]


def fetch_cmc_history(sdk: Any | None = None,
                      max_pages: int = 20) -> list[Reading]:
    """CoinMarketCap's whole published history, paged.

    One value per UTC day, and these are **closes**: the latest endpoint
    carries an intraday update_time and this one does not, so a day here is the
    value that day ended on. `available_at` is therefore the following midnight
    — the first moment that day's close was knowable — and not the day's own
    start, which would make it readable before it existed.

    Paging stops on an empty page or on one that adds nothing new, so a server
    that clamps `start` instead of honouring it ends the loop rather than
    spinning on the same rows.
    """
    sdk = sdk or cmc_client()
    out: list[Reading] = []
    seen: set[str] = set()
    start = 1
    for _ in range(max_pages):
        rows = call(sdk.global_metrics.fearandgreed_historical, collected=out,
                    start=start, limit=CMC_PAGE).data
        fresh = [r for r in rows if r.timestamp not in seen]
        if not fresh:
            break
        seen.update(r.timestamp for r in rows)
        for row in fresh:
            classification = normalise(row.value_classification)
            value = int(row.value)
            if not 0 <= value <= 100:
                raise ValueError(f"value {value} is outside the 0-100 scale")
            day = datetime.fromtimestamp(int(row.timestamp), UTC)
            out.append(Reading(
                day=day, value=value, classification=classification,
                source=CMC_SOURCE, published_at=day + timedelta(days=1)))
        start += CMC_PAGE
    return out


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
        classification = normalise(row["value_classification"])
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
    """Upsert by day. Returns the number of rows that actually changed.

    A day whose value is already stored and identical is left alone — not
    rewritten with a fresh `ingest_time`. The daily run fetches an overlap on
    purpose, so without that test almost every write would be a no-op recorded
    as a write, and `ingest_time` would end up meaning "when the cron last ran"
    rather than "when this value last changed". The second is the only question
    the column can answer.

    A value that genuinely differs *is* written. The index is not supposed to
    revise, so that is a revision, and §4.3 says a revised series with no
    vintages cannot be read as it was known at the time. Writing it makes the
    change visible in the returned count instead of hiding it behind an
    unconditional upsert that would have looked identical either way.
    """
    if not readings:
        return 0
    with engine.begin() as conn:
        written = conn.execute(sa.text(
            "INSERT INTO sentiment_readings "
            "  (day, value, classification, source, available_at) "
            "SELECT * FROM unnest("
            "  CAST(:days AS date[]), CAST(:values AS smallint[]), "
            "  CAST(:classes AS text[]), CAST(:sources AS text[]), "
            "  CAST(:avail AS timestamptz[])) "
            "ON CONFLICT (day, source) DO UPDATE SET "
            "  value = EXCLUDED.value, "
            "  classification = EXCLUDED.classification, "
            "  ingest_time = now() "
            "WHERE sentiment_readings.value IS DISTINCT FROM EXCLUDED.value "
            "   OR sentiment_readings.classification "
            "      IS DISTINCT FROM EXCLUDED.classification "
            "RETURNING day"
        ), {
            "days": [r.day.date() for r in readings],
            "values": [r.value for r in readings],
            "classes": [r.classification for r in readings],
            "sources": [r.source for r in readings],
            "avail": [r.available_at for r in readings],
        }).rowcount
    return max(written, 0)
