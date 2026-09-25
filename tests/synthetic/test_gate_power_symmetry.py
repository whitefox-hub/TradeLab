"""Two ways the gate could pass something it cannot resolve. Plan.md §8.8.

Both were found by a study that returned PASS on a figure the same call had
already declared indistinguishable from chance.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.validation.deflated import SharpeStats, required_sharpe
from tradelab.validation.gates import Verdict, evaluate


@pytest.mark.fast
def test_a_positive_result_below_the_floor_is_not_a_pass() -> None:
    """The underpowered check qualified negative results only.

    The reasoning was that a study finding something clearly resolvable made a
    planning error rather than a discovery to discard — but nothing tested that
    the positive result *was* resolvable. Measured on the cross-venue funding
    study: Sharpe 0.0495 per observation against a floor of 0.0651, PASS.
    """
    n = 640
    floor = required_sharpe(trials=1, n_obs=n)
    below = SharpeStats(sharpe=floor * 0.75, n_obs=n, skew=0.0, kurtosis=3.0)

    result = evaluate(below, trials=1, n_oos_trades=n, bars_per_year=365.25,
                      min_detectable_sharpe=0.5)
    assert result.verdict is Verdict.INCONCLUSIVE
    assert "resolve" in result.reasons[0]


@pytest.mark.fast
def test_a_powered_search_that_finds_nothing_still_fails() -> None:
    """The check above must not swallow this one.

    A search with the power to see an effect and no effect to see is a real
    negative, and turning it into "inconclusive" would remove the only verdict
    that can ever close a hypothesis.
    """
    n = 200_000
    nothing = SharpeStats(sharpe=0.0, n_obs=n, skew=0.0, kurtosis=3.0)
    result = evaluate(nothing, trials=10, n_oos_trades=n, bars_per_year=365.25,
                      min_detectable_sharpe=0.5)
    assert result.verdict is Verdict.FAIL


@pytest.mark.fast
def test_extreme_moments_stop_the_deflated_sharpe_being_read() -> None:
    """Positive skew *raises* the deflated Sharpe, which is exactly backwards
    for a series a few days dominate.

    Measured: skew 14.4, kurtosis 308, one day worth 67% of the total return.
    Under normal moments the same Sharpe deflates to 0.894, under those moments
    to 0.965 — across the 0.95 threshold, in the direction of confidence.
    """
    lumpy = SharpeStats(sharpe=0.05, n_obs=640, skew=14.4, kurtosis=308.5)
    result = evaluate(lumpy, trials=1, n_oos_trades=640, bars_per_year=365.25)
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.metrics["moments_out_of_range"] is True
    assert "skew" in result.reasons[0]


@pytest.mark.fast
def test_ordinary_moments_pass_through_untouched() -> None:
    """The guard must not fire on the mildly non-normal, which is everything."""
    normal = SharpeStats(sharpe=0.20, n_obs=2_000, skew=-0.6, kurtosis=5.0)
    result = evaluate(normal, trials=1, n_oos_trades=2_000, bars_per_year=365.25)
    assert "moments_out_of_range" not in result.metrics


@pytest.mark.fast
def test_one_day_dominating_a_series_is_visible_in_its_moments() -> None:
    """The link the guard relies on, verified rather than assumed."""
    rng = np.random.default_rng(5)
    returns = rng.normal(0, 0.001, 640)
    returns[100] = 0.5                      # one day worth more than all the rest
    centred = returns - returns.mean()
    std = returns.std(ddof=1)
    skew = float((centred**3).mean() / std**3)
    kurtosis = float((centred**4).mean() / std**4)
    assert skew > 3.0
    assert kurtosis > 30.0
