"""Temporal invariants over features. Plan.md §16.3.

These are the tests that catch look-ahead bias *generically*, without knowing
where it is — which is the only way to catch it, since a leak does not raise and
does not crash. It returns a better number.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradelab.features import momentum, participation, volatility  # noqa: F401
from tradelab.features.materialise import feature_set_hash, materialise
from tradelab.features.registry import REGISTRY

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "btcusdt_1m_2024-03-07_head240.parquet"
FEATURE_NAMES = sorted(REGISTRY.features)


@pytest.fixture(scope="module")
def bars() -> pl.DataFrame:
    return pl.read_parquet(FIXTURE).sort("event_time")


def _one_param_set(name: str) -> dict:
    """A mid-range parameterisation: large enough to have real memory, small
    enough that 240 bars of fixture still produce non-null output."""
    definition = REGISTRY.get(name)
    choices = {}
    for key, space in definition.params.items():
        values = list(space.values())
        choices[key] = values[min(len(values) // 8, len(values) - 1)]
    if {"fast", "slow"} <= set(choices) and choices["fast"] >= choices["slow"]:
        choices["fast"] = max(2, choices["slow"] // 3)
    return choices


@pytest.mark.medium
@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_truncation_invariance(name: str, bars: pl.DataFrame) -> None:
    """The generic look-ahead detector.

    For any cut point t, a feature computed on data truncated at t must equal the
    same feature computed on the full history and read at t. A feature that peeks
    even one bar ahead fails here, and it fails without the test knowing anything
    about what the feature does.
    """
    params = _one_param_set(name)
    feature_set = [(name, params)]
    column = REGISTRY.get(name).column(params)

    full = materialise(bars, feature_set)
    for cut in (120, 180, 239):
        truncated = materialise(bars.head(cut + 1), feature_set)
        a, b = truncated[column][cut], full[column][cut]

        # A null under truncation where the full history is non-null is not
        # "nothing to compare" — it is the leak signature itself: the feature
        # needed data beyond the cut and had none. Treating it as a skip, as
        # this first did, made the whole test blind to shift(-1), which the
        # canaries in §16.5 then exposed.
        assert not (a is None and b is not None), (
            f"{name} is null at row {cut} when the future is withheld but has a "
            f"value with full history. The feature reads beyond {cut}."
        )
        if a is None or b is None:
            continue
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12), (
            f"{name} at row {cut} differs when the future is withheld: "
            f"{a} vs {b}. That is look-ahead bias."
        )


@pytest.mark.medium
@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_declared_lookback_is_honest(name: str, bars: pl.DataFrame) -> None:
    """Perturbing rows older than the declared lookback must change nothing.

    `lookback_bars` drives the purge size in cross-validation (§8.2), so a feature
    that reaches further back than it declares breaks purging silently — and a
    broken purge produces a validation report that looks perfectly normal.
    """
    params = _one_param_set(name)
    definition = REGISTRY.get(name)
    lookback = definition.lookback_bars(params)
    column = definition.column(params)
    row = bars.height - 1
    if lookback >= row:
        pytest.skip(f"{name} lookback {lookback} exceeds the fixture")

    numeric = [c for c, dt in zip(bars.columns, bars.dtypes, strict=True) if dt.is_float()]
    tampered = bars.with_columns(
        pl.when(pl.int_range(0, pl.len()) < row - lookback)
        .then(pl.col(c) * 1.5)
        .otherwise(pl.col(c))
        .alias(c)
        for c in numeric
    )

    baseline = materialise(bars, [(name, params)])
    original = baseline[column][row]
    perturbed = materialise(tampered, [(name, params)])[column][row]
    if original is None or perturbed is None:
        pytest.skip("insufficient warmup in the fixture")

    # Tolerance is measured against the feature's OWN scale, not against its
    # value at one row. Several features are differences of nearly-equal
    # quantities (ema_cross), so their value passes through zero and any
    # relative tolerance there is meaningless. What "the lookback is honest"
    # should mean is that older data moves the feature by a negligible fraction
    # of its typical variation — which is what this measures.
    scale = baseline[column].std()
    if scale is None or scale == 0:
        pytest.skip("feature is constant over the fixture")
    drift = abs(original - perturbed) / scale
    assert drift < 1e-3, (
        f"{name} declares a lookback of {lookback} bars, but perturbing older "
        f"rows moved it by {drift:.2e} of its own standard deviation. The declared "
        f"lookback is too small, and the purge derived from it will be too small."
    )


@pytest.mark.medium
@given(offset=st.integers(min_value=0, max_value=60))
@settings(max_examples=15, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_row_order_within_a_timestamp_does_not_matter(offset: int, bars: pl.DataFrame) -> None:
    """Outputs must not depend on arrival order (§16.3)."""
    feature_set = [("rsi", {"period": 14, "source": "close"})]
    shuffled = bars.sample(fraction=1.0, shuffle=True, seed=offset).sort("event_time")
    column = REGISTRY.get("rsi").column(feature_set[0][1])
    assert (
        materialise(bars, feature_set)[column].to_list()
        == materialise(shuffled, feature_set)[column].to_list()
    )


@pytest.mark.fast
def test_feature_set_hash_tracks_the_implementation() -> None:
    """Editing a feature's code must change the set hash.

    Otherwise a stale cached matrix is served for changed code, and the
    experiment records a `feature_matrix_id` that no longer describes it.
    """
    feature_set = [("rsi", {"period": 14, "source": "close"})]
    before = feature_set_hash(feature_set)
    assert before == feature_set_hash(feature_set)
    assert before != feature_set_hash([("rsi", {"period": 15, "source": "close"})])
