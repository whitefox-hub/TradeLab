"""Arrow schemas for the Parquet archive. Plan.md §4.3, §3.2.

Every row carries three timestamps. `available_at` is the only one a backtest is
allowed to filter on; the other two exist so that a leak can be diagnosed after
the fact rather than merely suspected.

Prices are float64 here. The execution path uses Decimal (Postgres `numeric`) —
the archive is for analysis, where float64 has ample precision and is 20x faster.
"""

from __future__ import annotations

import pyarrow as pa

US_UTC = pa.timestamp("us", tz="UTC")

#: Present in every market-data schema, in this order.
IDENTITY: list[pa.Field] = [
    pa.field("event_time", US_UTC, nullable=False),
    pa.field("ingest_time", US_UTC, nullable=False),
    # Publication/observation time plus measured delay. The backtest sees only this.
    pa.field("available_at", US_UTC, nullable=False),
    pa.field("venue", pa.string(), nullable=False),
    pa.field("asset", pa.string(), nullable=False),
    # Corrections are new rows with a higher revision, never UPDATEs (§3.2).
    pa.field("revision", pa.int16(), nullable=False),
]


def _schema(*fields: pa.Field) -> pa.Schema:
    return pa.schema([*IDENTITY, *fields])


OHLCV = _schema(
    pa.field("timeframe", pa.string(), nullable=False),
    pa.field("open", pa.float64(), nullable=False),
    pa.field("high", pa.float64(), nullable=False),
    pa.field("low", pa.float64(), nullable=False),
    pa.field("close", pa.float64(), nullable=False),
    pa.field("volume", pa.float64(), nullable=False),
    pa.field("quote_volume", pa.float64(), nullable=False),
    # Participation proxies (§11.4). Trade count and average size beat volume,
    # which wash trading inflates.
    pa.field("trade_count", pa.int64(), nullable=False),
    pa.field("taker_buy_base", pa.float64(), nullable=False),
    pa.field("taker_buy_quote", pa.float64(), nullable=False),
)

TRADES = _schema(
    pa.field("trade_id", pa.int64(), nullable=False),
    pa.field("price", pa.float64(), nullable=False),
    pa.field("qty", pa.float64(), nullable=False),
    pa.field("quote_qty", pa.float64(), nullable=False),
    pa.field("is_buyer_maker", pa.bool_(), nullable=False),
)

FUNDING = _schema(
    pa.field("funding_rate", pa.float64(), nullable=False),
    pa.field("mark_price", pa.float64(), nullable=True),
)

OPEN_INTEREST = _schema(
    pa.field("open_interest", pa.float64(), nullable=False),
    pa.field("open_interest_value", pa.float64(), nullable=True),
)

LIQUIDATIONS = _schema(
    pa.field("side", pa.string(), nullable=False),  # BUY closes a short, SELL a long
    pa.field("price", pa.float64(), nullable=False),
    pa.field("qty", pa.float64(), nullable=False),
    pa.field("order_type", pa.string(), nullable=True),
)

#: Exchange announcements (§11.1). Not market data, so it carries its own identity
#: fields, but the three-timestamp contract is identical and matters more here:
#: `available_at` is publication plus a MODELLED subscriber delay, never our
#: ingest time — backfilled announcements would otherwise all become available
#: in 2026, which makes them useless, while using publication exactly assumes a
#: zero-latency subscriber, which is a lie in the other direction.
ANNOUNCEMENTS = pa.schema([
    pa.field("event_time", US_UTC, nullable=False),      # releaseDate
    pa.field("ingest_time", US_UTC, nullable=False),
    pa.field("available_at", US_UTC, nullable=False),
    pa.field("venue", pa.string(), nullable=False),
    pa.field("revision", pa.int16(), nullable=False),
    pa.field("article_id", pa.string(), nullable=False),
    pa.field("code", pa.string(), nullable=False),
    pa.field("title", pa.string(), nullable=False),
    pa.field("catalog_id", pa.int32(), nullable=False),
    # Closed taxonomy (§11.1). Unmatched titles get "unclassified", never a new
    # class: emergent classes fragment the sample and make the IRF unestimable.
    pa.field("event_class", pa.string(), nullable=False),
    pa.field("assets", pa.list_(pa.string()), nullable=False),
    # True when the class implies assets but the title does not name them — the
    # "Multiple ... Perpetual Contracts" case, where they live in the body. An
    # empty asset list on a listing event is a defect, so it is made visible
    # rather than left to look like an event that affects nothing.
    pa.field("needs_body", pa.bool_(), nullable=False),
    pa.field("subscriber_delay_s", pa.int32(), nullable=False),
])

BY_KIND: dict[str, pa.Schema] = {
    "ohlcv": OHLCV,
    "trades": TRADES,
    "funding": FUNDING,
    "open_interest": OPEN_INTEREST,
    "liquidations": LIQUIDATIONS,
    "announcements": ANNOUNCEMENTS,
}
