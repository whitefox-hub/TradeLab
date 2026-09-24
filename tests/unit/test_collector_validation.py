"""Ingestion-time validation. Plan.md §4.2."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest

from tradelab.collectors import schemas
from tradelab.collectors.base import (
    Batch,
    IssueKind,
    Severity,
    check_duplicates,
    check_gaps,
    check_ohlc_coherence,
    check_ranges,
    check_timestamps,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FIXTURE = FIXTURES / "btcusdt_1m_2024-03-07_head240.parquet"


@pytest.fixture
def real_batch() -> Batch:
    return Batch("ohlcv", "binance", "BTCUSDT", pl.read_parquet(FIXTURE), schemas.OHLCV)


@pytest.mark.fast
def test_real_data_passes_every_check(real_batch: Batch) -> None:
    issues = [
        *check_timestamps(real_batch),
        *check_duplicates(real_batch, ["event_time", "asset", "timeframe"]),
        *check_ranges(real_batch, ["open", "high", "low", "close"], ["volume", "trade_count"]),
        *check_ohlc_coherence(real_batch),
        *check_gaps(real_batch, timedelta(minutes=1), timedelta(minutes=1)),
    ]
    assert issues == []


@pytest.mark.fast
def test_available_before_event_is_blocking(real_batch: Batch) -> None:
    """The seed of look-ahead bias: a row usable before it happened (§4.3).

    Rejecting this at ingestion costs nothing. Detecting it later means finding it
    in a Sharpe ratio, which is the expensive way.
    """
    tampered = Batch(
        real_batch.kind, real_batch.venue, real_batch.asset,
        real_batch.frame.with_columns(
            (pl.col("event_time") - pl.duration(hours=1)).alias("available_at")
        ),
        real_batch.schema,
    )
    issues = check_timestamps(tampered)
    assert any(i.severity is Severity.BLOCKING for i in issues)


@pytest.mark.fast
def test_duplicates_are_blocking(real_batch: Batch) -> None:
    doubled = Batch(
        real_batch.kind, real_batch.venue, real_batch.asset,
        pl.concat([real_batch.frame, real_batch.frame]), real_batch.schema,
    )
    issues = check_duplicates(doubled, ["event_time", "asset", "timeframe"])
    assert issues and issues[0].kind is IssueKind.DUPLICATE


@pytest.mark.fast
def test_incoherent_ohlc_is_blocking(real_batch: Batch) -> None:
    broken = Batch(
        real_batch.kind, real_batch.venue, real_batch.asset,
        real_batch.frame.with_columns(pl.col("low") * 10),  # low above high
        real_batch.schema,
    )
    assert check_ohlc_coherence(broken)


@pytest.mark.fast
def test_gap_is_reported_not_repaired(real_batch: Batch) -> None:
    """Anomalies are recorded, never silently interpolated away (§4.2)."""
    dropped = real_batch.frame.filter(
        pl.col("event_time") != real_batch.frame["event_time"][100]
    )
    issues = check_gaps(
        Batch(real_batch.kind, real_batch.venue, real_batch.asset, dropped, real_batch.schema),
        timedelta(minutes=1), timedelta(minutes=1),
    )
    assert len(issues) == 1
    assert issues[0].kind is IssueKind.GAP
    assert issues[0].detail["gap_seconds"] == 120.0


@pytest.mark.fast
def test_non_positive_price_is_blocking(real_batch: Batch) -> None:
    zeroed = Batch(
        real_batch.kind, real_batch.venue, real_batch.asset,
        real_batch.frame.with_columns(pl.lit(0.0).alias("close")), real_batch.schema,
    )
    assert check_ranges(zeroed, ["close"], [])


@pytest.mark.fast
def test_schema_carries_three_timestamps() -> None:
    names = [f.name for f in schemas.OHLCV]
    assert names[:3] == ["event_time", "ingest_time", "available_at"]
    for schema in schemas.BY_KIND.values():
        assert {"event_time", "ingest_time", "available_at"} <= {f.name for f in schema}
