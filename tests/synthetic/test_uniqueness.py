"""Label concurrency and effective sample size. Plan.md §7, §8.8."""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.labeling.barriers import BarrierSpec, triple_barrier
from tradelab.labeling.uniqueness import average_uniqueness, concurrency, from_labels
from tradelab.validation.deflated import deflated_sharpe, sharpe_stats
from tradelab.validation.synthetic import make_noise


@pytest.mark.fast
def test_non_overlapping_labels_are_fully_unique() -> None:
    """Back-to-back labels observe disjoint periods, so nothing is shared."""
    start = np.array([0, 10, 20, 30])
    end = np.array([9, 19, 29, 39])
    result = average_uniqueness(start, end, n_bars=40)
    assert np.allclose(result.weights, 1.0)
    assert result.effective_n == pytest.approx(4.0)
    assert result.inflation_factor == pytest.approx(1.0)


@pytest.mark.fast
def test_identical_labels_share_everything() -> None:
    """Four labels over the same window are one observation, not four."""
    start = np.zeros(4, dtype=int)
    end = np.full(4, 9)
    result = average_uniqueness(start, end, n_bars=10)
    assert np.allclose(result.weights, 0.25)
    assert result.effective_n == pytest.approx(1.0)
    assert result.inflation_factor == pytest.approx(4.0)


@pytest.mark.fast
def test_concurrency_counts_active_labels_per_bar() -> None:
    start = np.array([0, 2, 4])
    end = np.array([5, 6, 8])
    active = concurrency(start, end, n_bars=10)
    assert active[0] == 1
    assert active[4] == 3          # all three open
    assert active[7] == 1
    assert active[9] == 0


@pytest.mark.slow
def test_longer_horizons_inflate_the_naive_sample_count() -> None:
    """The measurement that motivates the whole module.

    Holding longer does not produce more information; it produces more
    overlapping views of the same information. The naive count does not know
    that, and every statistic built on it becomes more confident than the data
    supports.
    """
    bars = make_noise(n=20_000, seed=21)
    inflations = []
    for hold in (10, 60, 240):
        labels = triple_barrier(bars, BarrierSpec(max_holding=hold, vol_period=100))
        inflations.append(from_labels(labels, n_bars=bars.height).inflation_factor)

    assert inflations[0] < inflations[1] < inflations[2]
    assert inflations[-1] > 50, (
        f"a 240-bar horizon inflated the count by only {inflations[-1]:.1f}x; "
        f"the concurrency calculation looks wrong"
    )


@pytest.mark.slow
def test_effective_count_changes_the_verdict() -> None:
    """Why this is not a refinement.

    The same returns, judged with the naive count and with the effective one,
    move from `established beyond doubt` to `cannot be established`. Everything
    the validator says depends on getting this number right.
    """
    bars = make_noise(n=20_000, seed=21)
    labels = triple_barrier(bars, BarrierSpec(max_holding=240, vol_period=100))
    unique = from_labels(labels, n_bars=bars.height)

    rng = np.random.default_rng(3)
    returns = rng.normal(0.0012, 0.01, unique.raw_n)

    naive = deflated_sharpe(sharpe_stats(returns), trials=1000)
    honest = deflated_sharpe(
        sharpe_stats(returns, effective_n=unique.effective_n), trials=1000
    )
    assert naive > 0.95, "the naive count should have accepted this comfortably"
    assert honest < 0.5, (
        f"effective count {unique.effective_n:.0f} still gave DSR={honest:.3f}; "
        f"the correction is not reaching the statistics"
    )


@pytest.mark.fast
def test_engine_reports_effective_observations() -> None:
    """The engine must expose it, or callers will reach for the bar count."""
    from tradelab.engines.tier_a.costs import BINANCE_RETAIL
    from tradelab.engines.tier_a.engine import run

    close = np.linspace(100.0, 120.0, 2_000)
    position = np.zeros(2_000)
    position[100:500] = 1.0        # one long hold
    position[900:1500] = 1.0       # and another
    result = run(close, position, BINANCE_RETAIL, "1h", already_lagged=True)

    assert result.effective_n < 10, (
        f"two holding periods reported {result.effective_n} effective observations"
    )
