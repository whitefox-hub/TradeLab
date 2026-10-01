"""Promotion gate verdicts. Plan.md §8, §15.

The three verdicts must stay distinct. `inconclusive` is not a gentler `fail`:
one says the evidence points the wrong way, the other says there was not enough
evidence to point anywhere. Collapsing them costs the system its most useful
distinction — and it is the distinction the whole project is built around.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.validation.deflated import required_sharpe, sharpe_stats
from tradelab.validation.gates import Verdict, evaluate


@pytest.mark.fast
def test_too_few_trades_is_inconclusive_not_fail() -> None:
    """Below the minimum trade count there is nothing to judge.

    `fail` would imply the evidence pointed the other way; it did not exist.
    """
    rng = np.random.default_rng(1)
    result = evaluate(sharpe_stats(rng.normal(0.002, 0.01, 50)), trials=1000, n_oos_trades=40)
    assert result.verdict is Verdict.INCONCLUSIVE
    assert "below the 100 minimum" in result.reasons[0]


@pytest.mark.fast
def test_noise_from_an_adequately_powered_search_fails() -> None:
    """A negative result from a search that could have seen something is a `fail`."""
    rng = np.random.default_rng(2)
    result = evaluate(
        sharpe_stats(rng.normal(0.0, 0.01, 5000)), trials=100_000, n_oos_trades=500,
        min_detectable_sharpe=0.10,
    )
    assert result.verdict is Verdict.FAIL
    assert "does not exceed" in result.reasons[0]


@pytest.mark.fast
def test_noise_from_an_underpowered_search_is_inconclusive() -> None:
    """The same returns, judged differently because the search could not have seen.

    A negative result from an underpowered search says nothing about the
    hypothesis — only about the sample (§8.8). This pair is the clearest
    statement of why the distinction exists.
    """
    rng = np.random.default_rng(2)
    returns = rng.normal(0.0, 0.01, 5000)
    result = evaluate(
        sharpe_stats(returns), trials=100_000, n_oos_trades=500,
        min_detectable_sharpe=0.02,
    )
    assert result.verdict is Verdict.INCONCLUSIVE
    assert "underpowered" in result.reasons[0]


@pytest.mark.fast
def test_a_detectable_result_survives_a_pessimistic_target() -> None:
    """Declaring too small a target is a planning error, not grounds for discarding.

    The underpowered check qualifies a negative result and only a negative one.
    """
    rng = np.random.default_rng(3)
    result = evaluate(
        sharpe_stats(rng.normal(0.0015, 0.01, 5000)), trials=1000, n_oos_trades=500,
        min_detectable_sharpe=0.001,
    )
    assert result.verdict is Verdict.PASS


@pytest.mark.fast
def test_failing_a_baseline_is_a_fail() -> None:
    """Beating noise is not enough; it must beat the nulls too (§8.4)."""
    rng = np.random.default_rng(4)
    result = evaluate(
        sharpe_stats(rng.normal(0.0015, 0.01, 5000)), trials=1000, n_oos_trades=500,
        baseline_percentiles={"matched_random": 0.60},
    )
    assert result.verdict is Verdict.FAIL
    assert "matched_random" in result.reasons[0]


@pytest.mark.fast
def test_the_bar_rises_with_the_trial_count() -> None:
    """The arithmetic that contradicts 'millions of combinations' (§8.3).

    Testing more hypotheses raises what the survivor must clear, so a
    disciplined search is worth more than a large one.
    """
    n = 6 * 365
    bars = [required_sharpe(t, n) for t in (100, 1_000, 10_000, 1_000_000)]
    assert bars == sorted(bars)
    assert bars[-1] > bars[0] * 1.4


@pytest.mark.fast
def test_turnover_driven_pbo_is_reported_as_uninformative() -> None:
    """A low PBO driven by turnover carries no information about skill (§8.3)."""
    from tradelab.validation.pbo import PBOResult

    rng = np.random.default_rng(5)
    pbo = PBOResult(pbo=0.0, n_paths=252, median_oos_rank=0.99,
                    logits=np.ones(252), turnover_correlation=-0.95)
    result = evaluate(
        sharpe_stats(rng.normal(0.0015, 0.01, 5000)), trials=1000, n_oos_trades=500, pbo=pbo,
    )
    assert result.verdict is Verdict.FAIL
    assert "turnover-driven" in " ".join(result.reasons)
