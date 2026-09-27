"""When a reading may be used. tradelab/strategy/flash_data.py, RESULT-008's void."""

from __future__ import annotations

import polars as pl
import pytest

from tradelab.collectors.binance.intraday import KLINE_SCHEMA, METRICS_SCHEMA
from tradelab.strategy.flash_data import AUX_USABLE_AFTER_MS, assemble

STEP = 300_000
T = 1_790_000_100_000 - 1_790_000_100_000 % STEP


def _klines(n: int, value: float) -> pl.DataFrame:
    rows = [(T + (i - 1) * STEP, T + i * STEP, value + i, value + i, value + i, value + i,
             1.0, 1.0, 1, 0.5, 0.5) for i in range(n)]
    return pl.DataFrame(rows, schema=KLINE_SCHEMA, orient="row")


@pytest.mark.fast
def test_a_bars_own_premium_and_open_interest_are_not_visible_at_its_close() -> None:
    """Published ~14 s and ~19 s after the bar closes, so a decision at the close
    cannot have them. They are usable at the next close."""
    premium = _klines(4, 100.0)                  # premium bar closing at T + i·5m = 100 + i
    metrics = pl.DataFrame([(T + i * STEP, 1000.0 + i, 0.0, None, 2.0 + i, None, None)
                            for i in range(4)], schema=METRICS_SCHEMA, orient="row")
    bars = assemble(_klines(4, 1.0), premium, metrics).sort("available_ms")
    at = {row["available_ms"]: row for row in bars.iter_rows(named=True)}
    assert AUX_USABLE_AFTER_MS == STEP
    assert at[T + STEP]["premium"] == 100.0, "the premium of the bar closing at T, one bar late"
    assert at[T + STEP]["open_interest"] == 1000.0
    assert at[T]["premium"] is None and at[T]["open_interest"] is None
