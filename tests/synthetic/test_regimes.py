"""Regime classification. Plan.md §10, §16.3, §16.4."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tradelab.regimes.classifier import RegimeSpec, classify, coverage
from tradelab.validation.synthetic import make_noise, make_regime_switch


@pytest.mark.slow
def test_thresholds_never_see_the_future() -> None:
    """The leak this module is written around.

    A regime label is a feature in every sense that matters, so it must satisfy
    truncation invariance: the label for a bar must be identical whether or not
    the data after it exists. Thresholds taken over the whole sample produce
    labels that look entirely sensible and are contaminated — and the
    contamination flatters, because regimes defined with hindsight separate
    outcomes better than any regime identifiable at the time.
    """
    bars = make_noise(n=6_000, seed=17)
    spec = RegimeSpec(warmup=500, trend_period=50, vol_period=50)

    full = classify(bars, spec)
    for cut in (3_000, 4_500, 5_999):
        truncated = classify(bars.head(cut + 1), spec)
        assert truncated["regime"][cut] == full["regime"][cut], (
            f"regime at row {cut} changed when later data was withheld: "
            f"{truncated['regime'][cut]} vs {full['regime'][cut]}. The thresholds "
            f"are using the future."
        )


@pytest.mark.slow
def test_canary_full_sample_quantiles_are_caught() -> None:
    """The same classifier with hindsight thresholds must fail the test above.

    Without this, the invariance test could be passing because the data is bland
    rather than because the implementation is careful.
    """
    bars = make_noise(n=6_000, seed=17)
    close = bars["close"].to_numpy().astype(np.float64)
    returns = np.zeros(close.size)
    returns[1:] = np.log(close[1:] / close[:-1])
    vol = pl.Series(returns).rolling_std(50).to_numpy()

    def cheating_labels(values: np.ndarray) -> np.ndarray:
        # The tempting one-liner: "high volatility means above the median".
        threshold = np.nanquantile(values, 0.66)
        return values >= threshold

    full = cheating_labels(vol)
    truncated = cheating_labels(vol[:4_501])
    assert full[4_500] != truncated[4_500] or not np.array_equal(
        full[:4_501], truncated
    ), "a deliberately cheating classifier was not detected"


@pytest.mark.slow
def test_volatility_regimes_track_the_planted_switches() -> None:
    """Recovering something whose answer exists (§16.4).

    The generator alternates quiet and violent stretches, so the classifier must
    call the violent ones high-volatility far more often than the quiet ones.
    """
    bars, truth = make_regime_switch(n=20_000, seed=5, n_regimes=4)
    labels = classify(bars, RegimeSpec(warmup=1_000, vol_period=100, trend_period=100))

    is_high = (labels["volatility"] == "high_vol").to_numpy()
    classified = labels["volatility"].to_numpy() != "unknown"

    # Regimes 0 and 2 are quiet; 1 and 3 are violent (sigmas alternate).
    quiet = classified & np.isin(truth, [0, 2])
    violent = classified & np.isin(truth, [1, 3])
    assert is_high[violent].mean() > is_high[quiet].mean() + 0.3, (
        f"high-vol share was {is_high[violent].mean():.2f} in violent regimes versus "
        f"{is_high[quiet].mean():.2f} in quiet ones; the classifier is not tracking "
        f"the planted structure."
    )


@pytest.mark.fast
def test_unclassifiable_warmup_is_marked_not_guessed() -> None:
    """No history means no threshold, and that is reported rather than invented."""
    bars = make_noise(n=3_000, seed=9)
    labels = classify(bars, RegimeSpec(warmup=1_000))
    assert (labels["regime"][:999] == "unknown|unknown").all()
    assert "unknown|unknown" not in coverage(labels)


@pytest.mark.fast
def test_coverage_excludes_regimes_too_small_to_support_a_conclusion() -> None:
    """§15 wants a strategy positive in two regimes — two regimes with enough
    observations to mean something, not two labels that happen to appear."""
    bars = make_noise(n=6_000, seed=11)
    labels = classify(bars, RegimeSpec(warmup=500))
    covered = coverage(labels, min_bars=200)
    # Checked before the `all()`, which is vacuously true on an empty dict. If
    # classification ever stopped producing usable regimes this test would go
    # green on nothing, and §15's two-regime requirement is exactly what it is
    # here to police.
    assert len(covered) >= 2, (
        f"§15 needs two regimes with enough observations; got {covered}"
    )
    assert all(count >= 200 for count in covered.values())
