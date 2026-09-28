"""Bulk historical backfill from data.binance.vision (Plan.md §4.1). Keyless, back to listing."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl
import pyarrow as pa

from tradelab.collectors import schemas
from tradelab.collectors.base import (
    Batch,
    Issue,
    IssueKind,
    Severity,
    ValidationReport,
    check_duplicates,
    check_gaps,
    check_ohlc_coherence,
    check_ranges,
    check_schema,
    check_timestamps,
)

BASE = "https://data.binance.vision/data/futures/um/daily"
VENUE = "binance"

# A kline is usable at close; the archive merely records it later.
KLINE_PUBLICATION_DELAY = timedelta(milliseconds=0)

# Positional layout of the headerless archives.
_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]

# Binance header names -> ours. Header names changed over the years; headerless files exist.
_RENAME = {
    "count": "trade_count",
    "number_of_trades": "trade_count",
    "taker_buy_volume": "taker_buy_base",
    "taker_buy_base_asset_volume": "taker_buy_base",
    "taker_buy_quote_volume": "taker_buy_quote",
    "taker_buy_quote_asset_volume": "taker_buy_quote",
    "quote_asset_volume": "quote_volume",
}


class BinanceKlineBulk:
    """Daily kline archives. One file per (asset, timeframe, day)."""

    kind = "ohlcv"
    venue = VENUE

    def __init__(self, timeframe: str = "1m", client: httpx.Client | None = None) -> None:
        self.timeframe = timeframe
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=True)

    @property
    def schema(self) -> pa.Schema:
        return schemas.OHLCV

    def url(self, asset: str, day: date) -> str:
        return (
            f"{BASE}/klines/{asset}/{self.timeframe}/"
            f"{asset}-{self.timeframe}-{day.isoformat()}.zip"
        )

    def month_url(self, asset: str, year: int, month: int) -> str:
        return (
            f"{BASE.replace('/daily', '/monthly')}/klines/{asset}/{self.timeframe}/"
            f"{asset}-{self.timeframe}-{year:04d}-{month:02d}.zip"
        )

    def fetch_month(self, asset: str, year: int, month: int) -> pl.DataFrame | None:
        """A whole month in one request; ~30x faster than daily files (archive is latency-bound)."""
        response = self._client.get(self.month_url(asset, year, month))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            raw = archive.read(archive.namelist()[0]).decode()
        return self._parse(raw, asset)

    def fetch_day(self, asset: str, day: date) -> pl.DataFrame | None:
        """One day of bars, or None on 404 (not listed that day; §4.4 decides)."""
        response = self._client.get(self.url(asset, day))
        if response.status_code == 404:
            return None
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            raw = archive.read(archive.namelist()[0]).decode()
        return self._parse(raw, asset)

    def _parse(self, raw: str, asset: str) -> pl.DataFrame:
        # Some archives have a header row, some do not.
        has_header = raw.lstrip().startswith("open_time")
        frame = pl.read_csv(
            io.StringIO(raw), has_header=has_header,
            new_columns=None if has_header else _KLINE_COLUMNS,
            infer_schema_length=10_000,
        )
        frame = frame.rename({k: v for k, v in _RENAME.items() if k in frame.columns})
        missing = {"open_time", "close_time", "trade_count", "taker_buy_base",
                   "taker_buy_quote", "quote_volume"} - set(frame.columns)
        if missing:
            raise ValueError(
                f"Unexpected kline layout for {asset}: missing {sorted(missing)}. "
                f"Columns were {frame.columns}. Extend _RENAME rather than guessing."
            )
        return self._normalise(frame, asset)

    def _normalise(self, frame: pl.DataFrame, asset: str) -> pl.DataFrame:
        # Older archives use epoch ms, newer ones us; detect by magnitude.
        unit = "us" if frame["open_time"].max() > 1e15 else "ms"  # type: ignore[operator]
        ingest = datetime.now(UTC)
        close_time = pl.from_epoch(pl.col("close_time"), time_unit=unit).dt.replace_time_zone("UTC")

        return frame.select(
            # event_time is the bar close.
            event_time=close_time,
            ingest_time=pl.lit(ingest),
            available_at=close_time + KLINE_PUBLICATION_DELAY,
            venue=pl.lit(VENUE),
            asset=pl.lit(asset),
            revision=pl.lit(0, dtype=pl.Int16),
            timeframe=pl.lit(self.timeframe),
            open=pl.col("open").cast(pl.Float64),
            high=pl.col("high").cast(pl.Float64),
            low=pl.col("low").cast(pl.Float64),
            close=pl.col("close").cast(pl.Float64),
            volume=pl.col("volume").cast(pl.Float64),
            quote_volume=pl.col("quote_volume").cast(pl.Float64),
            trade_count=pl.col("trade_count").cast(pl.Int64),
            taker_buy_base=pl.col("taker_buy_base").cast(pl.Float64),
            taker_buy_quote=pl.col("taker_buy_quote").cast(pl.Float64),
        ).sort("event_time")

    def backfill(self, asset: str, start: datetime, end: datetime) -> Iterator[Batch]:
        day = start.date()
        while day <= end.date():
            frame = self.fetch_day(asset, day)
            if frame is not None and frame.height:
                yield Batch(self.kind, VENUE, asset, frame, self.schema)
            day += timedelta(days=1)

    def validate(self, batch: Batch) -> ValidationReport:
        expected = _timeframe_delta(self.timeframe)
        issues = [
            *check_schema(batch),
            *check_timestamps(batch),
            *check_duplicates(batch, ["event_time", "asset", "timeframe"]),
            *check_ranges(
                batch,
                positive=["open", "high", "low", "close"],
                non_negative=["volume", "quote_volume", "trade_count", "taker_buy_base"],
            ),
            *check_ohlc_coherence(batch),
            *check_gaps(batch, expected=expected),
        ]
        return ValidationReport(issues)


def _timeframe_delta(timeframe: str) -> timedelta:
    unit, amount = timeframe[-1], int(timeframe[:-1])
    return {"m": timedelta(minutes=amount), "h": timedelta(hours=amount),
            "d": timedelta(days=amount)}[unit]


_FUNDING_COLUMNS = ["calc_time", "funding_interval_hours", "last_funding_rate"]


class BinanceFundingBulk:
    """Monthly funding-rate archives. Settles at 00/08/16 UTC, hence UTC daily bars (§5.7)."""

    kind = "funding"
    venue = VENUE

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=True)

    @property
    def schema(self) -> pa.Schema:
        return schemas.FUNDING

    def url(self, asset: str, year: int, month: int) -> str:
        return (
            f"{BASE.replace('/daily', '/monthly')}/fundingRate/{asset}/"
            f"{asset}-fundingRate-{year:04d}-{month:02d}.zip"
        )

    def fetch_month(self, asset: str, year: int, month: int) -> pl.DataFrame | None:
        response = self._client.get(self.url(asset, year, month))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            raw = archive.read(archive.namelist()[0]).decode()

        has_header = not raw.lstrip()[0].isdigit()
        frame = pl.read_csv(
            io.StringIO(raw), has_header=has_header,
            new_columns=None if has_header else _FUNDING_COLUMNS,
            infer_schema_length=10_000,
        )
        rename = {
            "calc_time": "event_time_raw",
            "last_funding_rate": "funding_rate",
            "funding_interval_hours": "interval_hours",
        }
        frame = frame.rename({k: v for k, v in rename.items() if k in frame.columns})
        if "event_time_raw" not in frame.columns or "funding_rate" not in frame.columns:
            raise ValueError(
                f"Unexpected funding layout for {asset} {year}-{month:02d}: {frame.columns}"
            )

        unit = "us" if frame["event_time_raw"].max() > 1e15 else "ms"  # type: ignore[operator]
        settle = pl.from_epoch(pl.col("event_time_raw"), time_unit=unit).dt.replace_time_zone("UTC")
        return frame.select(
            # The rate is known and applied at the settlement instant.
            event_time=settle,
            ingest_time=pl.lit(datetime.now(UTC)),
            available_at=settle,
            venue=pl.lit(VENUE),
            asset=pl.lit(asset),
            revision=pl.lit(0, dtype=pl.Int16),
            funding_rate=pl.col("funding_rate").cast(pl.Float64),
            mark_price=pl.lit(None, dtype=pl.Float64),
        ).sort("event_time")

    def backfill(self, asset: str, start: datetime, end: datetime) -> Iterator[Batch]:
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            frame = self.fetch_month(asset, year, month)
            if frame is not None and frame.height:
                yield Batch(self.kind, VENUE, asset, frame, self.schema)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    def validate(self, batch: Batch) -> ValidationReport:
        issues = [
            *check_schema(batch),
            *check_timestamps(batch),
            *check_duplicates(batch, ["event_time", "asset"]),
            # |rate| >= 5% per interval indicates a parsing error.
            *(
                [_out_of_range(batch, "funding_rate", "|rate| must be < 0.05")]
                if batch.frame.filter(pl.col("funding_rate").abs() >= 0.05).height
                else []
            ),
            *check_gaps(batch, expected=timedelta(hours=8)),
        ]
        return ValidationReport(issues)


def _out_of_range(batch: Batch, column: str, rule: str) -> Issue:
    times = batch.frame["event_time"]
    return Issue(
        kind=IssueKind.OUT_OF_RANGE, severity=Severity.BLOCKING,
        period_start=times.min(), period_end=times.max(),  # type: ignore[arg-type]
        asset=batch.asset, detail={"column": column, "rule": rule},
    )


_AGG_COLUMNS = [
    "agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
    "transact_time", "is_buyer_maker",
]


