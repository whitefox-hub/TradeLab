"""Arrow schemas for the Parquet archive (Plan.md §4.3, §3.2).

Backtests filter only on `available_at`; the other timestamps diagnose leaks. Prices are
float64 here (analysis); the execution path uses Decimal."""

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
    # Participation proxies (§11.4); wash trading inflates volume, not trade count.
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

#: Exchange announcements (§11.1). `available_at` = publication + modelled subscriber delay,
#: never ingest time.
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
    # Closed taxonomy (§11.1): unmatched titles get "unclassified", never a new class.
    pa.field("event_class", pa.string(), nullable=False),
    pa.field("assets", pa.list_(pa.string()), nullable=False),
    # Class implies assets but the title names none (they are in the article body).
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
