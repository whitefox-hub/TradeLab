"""The materialised cache must be indistinguishable from deriving on the fly.

A cache that returns different numbers than the thing it caches changes every
result downstream, silently and everywhere at once. It is the most dangerous
piece of code added recently precisely because it is the least interesting one.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from tradelab.collectors import schemas
from tradelab.storage.materialise_bars import (
    is_stale,
    load_materialised,
    materialise_asset,
    target_path,
)
from tradelab.storage.writer import write_partition
from tradelab.validation.synthetic import make_noise


@pytest.fixture
def archive(tmp_path):
    """A small 1m archive: three days of one asset."""
    root = tmp_path / "parquet"
    bars = make_noise(n=1440 * 3, seed=41)
    for offset in range(3):
        day = date(2020, 1, 1 + offset)
        chunk = bars.slice(offset * 1440, 1440).with_columns(
            ingest_time=pl.col("event_time"),
            revision=pl.lit(0, dtype=pl.Int16),
            venue=pl.lit("binance"),
            asset=pl.lit("TESTUSDT"),
            timeframe=pl.lit("1m"),
            quote_volume=pl.col("quote_volume"),
            taker_buy_quote=pl.col("quote_volume") * 0.5,
        )
        write_partition(chunk, schemas.OHLCV, root, "ohlcv", "binance",
                        "TESTUSDT", day, timeframe="1m")
    return root


@pytest.mark.medium
def test_cached_bars_equal_derived_bars(archive) -> None:
    """The invariant. Every downstream number depends on it."""
    from tradelab.storage.query import aggregate_bars

    derived = (
        aggregate_bars(archive, "TESTUSDT", "1h")
        .drop("event_time", "available_at")
        .rename({"bucket": "event_time"})
        .drop_nulls(subset=["open", "high", "low", "close"])
        .sort("event_time")
    )
    materialise_asset(archive, "TESTUSDT", "1h")
    cached = load_materialised(archive, "TESTUSDT", "1h").sort("event_time")

    assert cached.height == derived.height
    for column in ("open", "high", "low", "close", "volume", "trade_count"):
        np.testing.assert_allclose(
            cached[column].to_numpy().astype(np.float64),
            derived[column].to_numpy().astype(np.float64),
            rtol=1e-12, err_msg=f"{column} differs between cache and derivation",
        )


@pytest.mark.medium
def test_a_new_source_day_makes_the_cache_stale(archive) -> None:
    """A cache that cannot notice its source changed is worse than none."""
    materialise_asset(archive, "TESTUSDT", "1h")
    assert not is_stale(archive, "TESTUSDT", "1h")

    extra = make_noise(n=1440, seed=99).with_columns(
        ingest_time=pl.col("event_time"), revision=pl.lit(0, dtype=pl.Int16),
        venue=pl.lit("binance"), asset=pl.lit("TESTUSDT"), timeframe=pl.lit("1m"),
        taker_buy_quote=pl.col("quote_volume") * 0.5,
    )
    write_partition(extra, schemas.OHLCV, archive, "ohlcv", "binance",
                    "TESTUSDT", date(2020, 1, 4), timeframe="1m")

    assert is_stale(archive, "TESTUSDT", "1h"), (
        "a day appeared in the source and the cache did not notice"
    )


@pytest.mark.fast
def test_a_missing_cache_is_stale(archive) -> None:
    assert is_stale(archive, "TESTUSDT", "4h")
    assert load_materialised(archive, "TESTUSDT", "4h") is None


@pytest.mark.medium
def test_rematerialising_is_idempotent(archive) -> None:
    """Same source, same bytes. Otherwise `config_hash` drifts under a no-op."""
    first = materialise_asset(archive, "TESTUSDT", "1h")
    before = target_path(archive, "TESTUSDT", "1h").read_bytes()
    second = materialise_asset(archive, "TESTUSDT", "1h")
    after = target_path(archive, "TESTUSDT", "1h").read_bytes()

    assert first.source_hash == second.source_hash
    assert before == after, "re-deriving unchanged data produced different bytes"
