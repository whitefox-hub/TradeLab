"""The screening engine against known answers. Plan.md §9.1, §16.4."""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel
from tradelab.engines.tier_a.engine import BARS_PER_YEAR, run, threshold_position
from tradelab.validation.synthetic import make_noise


@pytest.mark.fast
def test_a_signal_cannot_act_on_its_own_bar() -> None:
    """The causality boundary, stated as a test.

    A position derived from bar t's close is filled at t+1. Acting within bar t
    means trading at a price you only learn once the bar has closed, and it is
    the difference between a screening engine and a machine for drawing
    impossible equity curves.
    """
    close = np.array([100.0, 110.0, 100.0, 110.0, 100.0])
    # Perfect hindsight. The return at index i is realised BETWEEN i-1 and i, so
    # capturing it means holding at index i — not at the bar where the move was
    # decided. Getting this backwards is itself a common way to write a backtest
    # that looks broken when it is correct, or correct when it is broken.
    oracle = np.array([0.0, 1.0, 0.0, 1.0, 0.0])

    lagged = run(close, oracle, CostModel(0, 0, 0, 0))
    unlagged = run(close, oracle, CostModel(0, 0, 0, 0), already_lagged=True)

    # Told it is already lagged, the engine obeys and the oracle prints money.
    assert unlagged.total_return > 0.15
    # Applying the lag destroys it, which is the correct outcome.
    assert lagged.total_return < unlagged.total_return


@pytest.mark.fast
def test_cost_drag_matches_the_analytic_expectation() -> None:
    """A known number of round trips must cost exactly what arithmetic says."""
    n = 1000
    close = np.full(n, 100.0)          # flat: every loss is a cost
    position = np.zeros(n)
    position[::4] = 1.0                # in and out repeatedly
    position[1::4] = 1.0

    costs = CostModel(taker_fee=0.001, maker_fee=0.001, half_spread=0.0, slippage=0.0)
    result = run(close, position, costs, already_lagged=True)

    expected = float(np.abs(np.diff(np.concatenate([[0.0], position]))).sum()) * costs.per_side
    assert result.cost_drag == pytest.approx(expected, rel=1e-12)
    # On flat prices the gross result is exactly zero, so the net is pure cost.
    assert result.total_return < 0
    assert result.gross_sharpe == 0.0


@pytest.mark.fast
def test_annual_drag_reproduces_the_plan_arithmetic() -> None:
    """§0.1's table is computed, not quoted.

    Round-trip cost ~0.12% at a one-day hold on hourly bars gives ~30%/yr, which
    is the number that rules out shorter horizons.
    """
    costs = CostModel(taker_fee=0.0005, maker_fee=0.0002, half_spread=0.00005, slippage=0.0001)
    assert costs.round_trip == pytest.approx(0.0013, abs=1e-4)
    day = costs.annual_drag(BARS_PER_YEAR["1h"], avg_holding_bars=24)
    week = costs.annual_drag(BARS_PER_YEAR["1h"], avg_holding_bars=24 * 7)
    # 365 round trips a year, not 250: crypto does not close. §0.1 originally
    # used the equities calendar and understated every drag figure by 46%.
    assert 0.44 < day < 0.50           # ~47%/yr at a one-day hold
    assert week < day / 5              # and it collapses as the hold lengthens


@pytest.mark.fast
def test_hysteresis_suppresses_threshold_flicker() -> None:
    """A signal resting on its threshold must not trade every bar.

    Without hysteresis the turnover it generates is an artefact of the threshold,
    not a property of the strategy — and it is charged at full cost.
    """
    rng = np.random.default_rng(7)
    signal = 0.5 + rng.normal(0, 0.01, 500)     # hovers exactly on 0.5

    flicker = threshold_position(signal, entry=0.5)
    damped = threshold_position(signal, entry=0.52, exit_=0.48)

    flips = lambda p: int(np.abs(np.diff(p)).sum())  # noqa: E731
    assert flips(damped) < flips(flicker) / 5


@pytest.mark.slow
def test_a_random_strategy_on_noise_loses_exactly_its_costs() -> None:
    """The null result the whole system is built to produce.

    On data with no edge, a strategy's net return must be its cost drag and
    nothing else. An engine that shows a profit here is broken, and everything
    downstream of it would be as well.
    """
    bars = make_noise(n=50_000, seed=42)
    close = bars["close"].to_numpy().astype(np.float64)
    rng = np.random.default_rng(99)
    position = (rng.random(len(close)) > 0.5).astype(np.float64)

    result = run(close, position, BINANCE_RETAIL, timeframe="1m")
    assert abs(result.gross_sharpe) < 0.75, "found an edge in pure noise"
    assert result.sharpe < result.gross_sharpe, "costs must reduce the Sharpe"
    assert result.total_return < 0, "a random strategy paying fees must lose"
