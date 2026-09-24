from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from tradelab.storage.paths import NON_BACKFILLABLE, Partition, glob_for


@pytest.mark.fast
def test_partition_layout_is_hive_partitioned() -> None:
    part = Partition(kind="trades", venue="binance", asset="BTCUSDT", day=date(2024, 3, 7))
    assert part.relative_path().as_posix() == (
        "trades/venue=binance/asset=BTCUSDT/year=2024/month=03/2024-03-07_r000.parquet"
    )


@pytest.mark.fast
def test_ohlcv_carries_timeframe_in_the_path() -> None:
    part = Partition(
        kind="ohlcv", venue="binance", asset="ETHUSDT", day=date(2024, 12, 31), timeframe="1h"
    )
    assert "tf=1h" in part.relative_path().as_posix()


@pytest.mark.fast
def test_revision_changes_the_filename_not_the_partition() -> None:
    """Corrections are new files, never UPDATEs (§3.2)."""
    base = Partition(kind="funding", venue="binance", asset="BTCUSDT", day=date(2024, 1, 1))
    revised = dataclasses.replace(base, revision=1)
    assert base.relative_path().parent == revised.relative_path().parent
    assert base.relative_path() != revised.relative_path()


@pytest.mark.fast
def test_non_backfillable_kinds_are_declared() -> None:
    """These have no historical REST endpoint. Capture must start early (§4.1)."""
    assert {"liquidations", "open_interest"} <= NON_BACKFILLABLE


@pytest.mark.fast
def test_glob_uses_a_single_recursive_wildcard() -> None:
    """DuckDB rejects multiple '**' in one path; asset selection is a predicate."""
    pattern = glob_for(Path("/data"), "trades")
    assert pattern.count("**") == 1
    assert pattern.endswith("*.parquet")
