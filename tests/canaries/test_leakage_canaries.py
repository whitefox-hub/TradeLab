"""Deliberately broken features, and the detector each one must trip.

Plan.md §16.5. An anti-leakage system that has never caught a leak is not
demonstrated to work; these exist so the detectors are known to have teeth.

Every canary is registered in an isolated registry so it can never reach a real
feature set.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from tradelab.features.materialise import materialise
from tradelab.features.params import IntRange
from tradelab.features.registry import Registry, feature

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "btcusdt_1m_2024-03-07_head240.parquet"


@pytest.fixture
def bars() -> pl.DataFrame:
    return pl.read_parquet(FIXTURE).sort("event_time")


def _leaks(definition, params: dict, bars: pl.DataFrame, cut: int = 200) -> bool:
    """Whether withholding the future changes the feature at the cut point.

    A null under truncation against a value with full history counts as a leak,
    not as a missing comparison: it means the feature asked for data past the cut.
    """
    registry = Registry({definition.name: definition})
    column = definition.column(params)
    full = materialise(bars, [(definition.name, params)], registry)
    cut_frame = materialise(bars.head(cut + 1), [(definition.name, params)], registry)
    a, b = cut_frame[column][cut], full[column][cut]
    if a is None:
        return b is not None
    return b is None or abs(a - b) > 0


@pytest.mark.medium
def test_canary_future_return_is_caught(bars: pl.DataFrame) -> None:
    """The blatant case: a feature that simply returns tomorrow's move."""
    registry = Registry()

    @feature(family="momentum", lookback=1, params={}, registry=registry)
    def cheat_future_return() -> pl.Expr:
        return pl.col("close").shift(-1) / pl.col("close") - 1

    assert _leaks(cheat_future_return, {}, bars), (
        "Truncation invariance failed to notice a feature reading the next bar."
    )


@pytest.mark.medium
def test_canary_one_bar_peek_is_caught(bars: pl.DataFrame) -> None:
    """The subtle case, and the one that proves the detector has teeth.

    A rolling mean centred one bar ahead looks entirely ordinary in review: it is
    a mean of recent prices, with a sign error nobody would spot. This is the
    shape real look-ahead bugs take.
    """
    registry = Registry()

    @feature(family="trend", lookback=lambda p: p["period"],
             params={"period": IntRange(2, 60)}, registry=registry)
    def cheat_one_bar(period: int) -> pl.Expr:
        return pl.col("close").rolling_mean(period).shift(-1)

    assert _leaks(cheat_one_bar, {"period": 20}, bars), (
        "A one-bar peek went undetected. The truncation test is not doing its job."
    )


@pytest.mark.medium
def test_canary_understated_lookback_is_caught(bars: pl.DataFrame) -> None:
    """A feature that reaches 60 bars back while declaring 5.

    It passes truncation invariance — it never reads the future — so only the
    lookback honesty property catches it. And it matters: the purge is derived
    from this number, so understating it makes the purge too small and the
    resulting validation report looks perfectly normal.
    """
    registry = Registry()

    @feature(family="trend", lookback=5, params={}, registry=registry)
    def liar_long_memory() -> pl.Expr:
        return pl.col("close").rolling_mean(60)

    column = liar_long_memory.column({})
    row = bars.height - 1
    declared = liar_long_memory.lookback_bars({})

    tampered = bars.with_columns(
        pl.when(pl.int_range(0, pl.len()) < row - declared)
        .then(pl.col("close") * 1.5)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    baseline = materialise(bars, [("liar_long_memory", {})], registry)
    perturbed = materialise(tampered, [("liar_long_memory", {})], registry)
    drift = abs(baseline[column][row] - perturbed[column][row]) / baseline[column].std()
    assert drift > 1e-3, "An understated lookback went undetected."


@pytest.mark.medium
def test_canary_unknown_family_is_refused() -> None:
    """The taxonomy is closed (§6). Emergent families fragment importance
    clustering into noise, so a new one must be a deliberate edit."""
    registry = Registry()
    with pytest.raises(ValueError, match="unknown family"):

        @feature(family="my_new_idea", lookback=1, params={}, registry=registry)
        def invented() -> pl.Expr:
            return pl.col("close")


@pytest.mark.medium
def test_canary_out_of_space_parameter_is_refused() -> None:
    """A parameter outside its declared space must not silently compute."""
    registry = Registry()

    @feature(family="momentum", lookback=lambda p: p["period"],
             params={"period": IntRange(2, 30)}, registry=registry)
    def bounded(period: int) -> pl.Expr:
        return pl.col("close").rolling_mean(period)

    with pytest.raises(ValueError, match="outside its declared space"):
        bounded.expr(period=999)
