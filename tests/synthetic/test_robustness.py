"""Resampling robustness. Plan.md §8.6, §14.6."""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.validation.robustness import (
    drawdown_distribution,
    optimal_block_length,
    perturb_prices,
    sharpe_interval,
    stationary_bootstrap,
)


def _autocorrelated(n: int = 2000, rho: float = 0.3, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(0.0008, 0.01, n)
    out = np.zeros(n)
    for i in range(1, n):
        out[i] = rho * out[i - 1] + shocks[i]
    return out


@pytest.mark.slow
def test_iid_resampling_produces_intervals_the_data_does_not_earn() -> None:
    """The reason blocks exist.

    On a series with serial dependence an iid bootstrap returns a materially
    narrower interval — narrow enough, measured here, to exclude zero where the
    correct one does not. That is a different conclusion, not a rounding
    difference.
    """
    returns = _autocorrelated()
    rng = np.random.default_rng(11)

    proper = sharpe_interval(returns, n_samples=1500)
    naive = np.array([
        (lambda s: s.mean() / s.std(ddof=1))(rng.choice(returns, returns.size))
        for _ in range(1500)
    ])
    naive_width = float(np.quantile(naive, 0.975) - np.quantile(naive, 0.025))

    assert naive_width < proper.width * 0.9, (
        f"iid width {naive_width:.4f} against blocked {proper.width:.4f}; the "
        f"blocked bootstrap should be the wider one"
    )


@pytest.mark.fast
def test_block_length_grows_with_the_sample() -> None:
    assert optimal_block_length(np.zeros(1000)) > optimal_block_length(np.zeros(100))
    assert optimal_block_length(np.zeros(10)) >= 2.0


@pytest.mark.slow
def test_blocks_preserve_serial_structure() -> None:
    """A blocked resample must retain autocorrelation an iid one destroys."""
    returns = _autocorrelated(n=1000, rho=0.6)
    blocked = stationary_bootstrap(returns, n_samples=200, block_length=20, seed=3)

    def rho1(x: np.ndarray) -> float:
        return float(np.corrcoef(x[:-1], x[1:])[0, 1])

    kept = float(np.mean([rho1(row) for row in blocked]))
    rng = np.random.default_rng(3)
    destroyed = float(np.mean([rho1(rng.choice(returns, returns.size)) for _ in range(200)]))
    assert kept > destroyed + 0.2, (
        f"blocked resamples kept autocorrelation {kept:.3f} against {destroyed:.3f} "
        f"for iid; the blocks are not doing their job"
    )


@pytest.mark.slow
def test_observed_drawdown_is_one_draw_from_a_distribution() -> None:
    """The maximum drawdown is not a property of the strategy.

    It is what happened to occur in the single ordering history produced.
    Reordering the same trades gives a different figure, and usually a worse one.
    """
    rng = np.random.default_rng(13)
    trades = rng.normal(0.004, 0.03, 200)
    result = drawdown_distribution(trades, n_samples=2000)

    assert result.samples.std() > 0, "every reordering gave the same drawdown"
    assert result.quantile(0.05) < result.quantile(0.5) < 0
    assert result.quantile(0.05) < result.quantile(0.5) * 1.2


@pytest.mark.slow
def test_a_drawdown_inside_the_distribution_is_not_evidence_of_failure() -> None:
    """The rule in §14.6, and the distribution it depends on.

    Without this module the rule has no reference to point at, and a strategy
    inside its ordinary range gets retired for behaving normally.
    """
    rng = np.random.default_rng(17)
    trades = rng.normal(0.004, 0.03, 200)
    result = drawdown_distribution(trades, n_samples=2000)

    ordinary = result.quantile(0.20)
    extreme = result.quantile(0.001) * 2

    assert result.is_within_expectation(ordinary, q=0.95)
    assert not result.is_within_expectation(extreme, q=0.95)


@pytest.mark.fast
def test_price_perturbation_stays_within_the_spread() -> None:
    """A result that moves when prices shift by a fraction of the spread is
    fitted to the exact prints rather than to the market."""
    close = np.linspace(100.0, 110.0, 500)
    jittered = perturb_prices(close, half_spread=1e-4, n_samples=50)

    assert jittered.shape == (50, 500)
    assert np.abs(jittered / close[None, :] - 1.0).max() <= 1e-4 + 1e-12
    assert not np.allclose(jittered[0], jittered[1])
