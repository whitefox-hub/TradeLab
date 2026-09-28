"""Fear and Greed indices (Plan.md §11.4, §4.3). For display only: vendor method is not
auditable, so `sentiment_readings` stays out of the point-in-time corpus."""

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

#: The five bands both indices report (mirrors a DB CHECK, so a new band fails on ingest).
CLASSIFICATIONS = (
    "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed",
)

#: CMC `latest` says "Extreme Greed", `historical` "Extreme greed": match caselessly,
#: store one spelling.
_CANONICAL = {name.casefold(): name for name in CLASSIFICATIONS}


def normalise(classification: str) -> str:
    """The canonical band name (case-insensitive), or ValueError naming what arrived."""
    canonical = _CANONICAL.get(classification.strip().casefold())
    if canonical is None:
        raise ValueError(f"unknown classification {classification!r}")
    return canonical


CMC_SOURCE = "coinmarketcap"

#: CMC history pages with `start`; limits above ~500 return empty (not clamped).
CMC_PAGE = 500

# CMC revises intraday (alternative.me publishes once at midnight UTC): a `latest` row is
# available at its update_time; a `historical` row is the close, available next midnight.


@dataclass(frozen=True, slots=True)
class Reading:
    day: datetime
    value: int
    classification: str
    source: str = SOURCE
    #: When the value became knowable; defaults to the day's midnight (alternative.me).
    #: Never `now()`, or backfilled history looks as if it arrived today.
    published_at: datetime | None = None

    @property
    def available_at(self) -> datetime:
        return self.published_at or self.day


def fetch_cmc(sdk: Any | None = None) -> list[Reading]:
    """CoinMarketCap's index, latest only. Keyless, undocumented endpoint: validated."""
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
    """CoinMarketCap's full history. Rows are daily closes, available the next midnight.

    Stops on a page adding nothing new, in case the server clamps `start`."""
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
    """Upsert by day; returns rows actually changed.

    Identical rows are skipped so `ingest_time` means "value last changed"; a differing
    value is a revision (§4.3) and is written, showing up in the count."""
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
