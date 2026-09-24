"""Point-in-time invariants over the archive. Plan.md §4.3, §16.3."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradelab.collectors import schemas
from tradelab.storage.query import scan
from tradelab.storage.writer import ImmutabilityError, write_partition

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "btcusdt_1m_2024-03-07_head240.parquet"


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "parquet"
    write_partition(
        pl.read_parquet(FIXTURE), schemas.OHLCV, root, "ohlcv", "binance",
        "BTCUSDT", date(2024, 3, 7), timeframe="1m",
    )
    return root


@pytest.mark.medium
@given(minute=st.integers(min_value=0, max_value=239))
# The archive fixture is read-only here, so reusing it across examples is safe.
@settings(max_examples=25, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_scan_never_returns_rows_that_were_not_yet_available(minute: int, archive: Path) -> None:
    """The generic point-in-time guarantee.

    For any cutoff, a scan must return nothing whose `available_at` is at or after
    it. This is the invariant every backtest silently depends on.
    """
    frame = pl.read_parquet(FIXTURE).sort("event_time")
    cutoff = frame["available_at"][minute]
    visible = scan(archive, "ohlcv", "BTCUSDT",
                   available_before=cutoff.isoformat(), columns="available_at")
    assert visible.height == minute
    if visible.height:
        assert visible["available_at"].max() < cutoff


@pytest.mark.fast
def test_partitions_are_immutable(archive: Path) -> None:
    """A correction is revision+1, never an overwrite (§3.2)."""
    with pytest.raises(ImmutabilityError, match="revision"):
        write_partition(
            pl.read_parquet(FIXTURE), schemas.OHLCV, archive, "ohlcv", "binance",
            "BTCUSDT", date(2024, 3, 7), timeframe="1m",
        )


@pytest.mark.fast
def test_revision_one_coexists_with_revision_zero(archive: Path) -> None:
    path = write_partition(
        pl.read_parquet(FIXTURE), schemas.OHLCV, archive, "ohlcv", "binance",
        "BTCUSDT", date(2024, 3, 7), timeframe="1m", revision=1,
    )
    assert path.exists()
    assert len(list(path.parent.glob("*.parquet"))) == 2
