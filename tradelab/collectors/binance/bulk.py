"""Bulk historical backfill from data.binance.vision. Plan.md §4.1.

Free, no account, no rate limit worth worrying about, and it goes back to the
listing of each contract. This is what makes phase 1 viable without credentials.
"""

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

# Measured, not assumed. Bulk files are published after the fact, so the honest
# `available_at` for a bar is its close plus the venue's own publication lag.
# For klines the bar is usable at close; the archive file merely records it later.
KLINE_PUBLICATION_DELAY = timedelta(milliseconds=0)

# Positional layout of the headerless archives.
_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]

# Binance's own header names -> ours. Both header and headerless archives exist,
# and the header names have changed over the years, so this is a mapping rather
# than an assumption.
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

    def fetch_day(self, asset: str, day: date) -> pl.DataFrame | None:
        """Return one day of bars, or None when the archive does not exist.

        A missing file is not an error: it means the contract was not listed that
        day. Distinguishing "not listed" from "gap" is what the point-in-time
        universe (§4.4) is for, so this returns None and lets the caller decide.
        """
        response = self._client.get(self.url(asset, day))
        if response.status_code == 404:
            return None
        response.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            raw = archive.read(archive.namelist()[0]).decode()

        # Binance switched to emitting a header row in some archives; both occur.
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
        # Binance emits epoch milliseconds in older archives and microseconds in
        # newer ones. Detect by magnitude rather than by date, which is fragile.
        unit = "us" if frame["open_time"].max() > 1e15 else "ms"  # type: ignore[operator]
        ingest = datetime.now(UTC)
        close_time = pl.from_epoch(pl.col("close_time"), time_unit=unit).dt.replace_time_zone("UTC")

        return frame.select(
            # A bar's event_time is its close: that is when it is complete.
            event_time=close_time,
            ingest_time=pl.lit(ingest),
            # A closed bar is usable the instant it closes. No delay to add here,
            # unlike news or macro, where the feed lag is real and measured.
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
    """Monthly funding-rate archives.

    Funding settles on fixed UTC marks (00:00, 08:00, 16:00) and is the input to
    the positioning component of the state index (§11.4). It is also the reason
    the daily bar boundary must be UTC 00:00 (§5.7): any other cut misaligns
    funding with the bars that are supposed to explain it.
    """

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
            # The settlement instant. The rate is known and applied at that mark.
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
            # Funding can legitimately be negative, so only the magnitude is
            # checked: rates beyond +/-5% per interval indicate a parsing error,
            # not a market event.
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
