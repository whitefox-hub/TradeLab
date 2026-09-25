"""Ordering invariants of the pipeline, on data built to expose each failure.

Written after a defect survived for days because the comment above it asserted
the invariant the line violated. Reading code is not verification; each test here
constructs data where the wrong order produces a visibly wrong answer.

Small by design. The point is the order, not the sample.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel
from tradelab.engines.tier_a.engine import run
from tradelab.orchestration.study import StudySpec, run_study
from tradelab.validation.synthetic import make_edge, make_noise


def _reversing(n: int = 12_000, seed: int = 23) -> pl.DataFrame:
    """Data whose relationship inverts halfway.

    A selector peeking at the evaluation window finds what survives the
    inversion; one restricted to training cannot. Without the inversion the test
    passes with the leak present, which is exactly what happened.
    """
    bars, _ = make_edge(n=n, seed=seed, edge_sharpe=2.5, signal_period=100)
    close = bars["close"].to_numpy().astype(np.float64)
    half = len(close) // 2
    flipped = close.copy()
    flipped[half:] = close[half] ** 2 / close[half:]
    return bars.with_columns(
        close=pl.Series(flipped),
        high=pl.Series(flipped * 1.001),
        low=pl.Series(flipped * 0.999),
        open=pl.Series(np.concatenate([[flipped[0]], flipped[:-1]])),
    )


@pytest.mark.medium
def test_selection_cannot_see_the_evaluation_window() -> None:
    """The defect that motivated this file."""
    spec = StudySpec(name="probe", params=tuple(range(10, 40, 6)),
                     entries=tuple(np.arange(55.0, 75.0, 4.0)), min_trades=20)
    result = run_study(_reversing(), spec)
    assert result.gate.metrics.get("sharpe_annualised", 0.0) < 3.0, (
        "implausibly good out-of-sample figure on data whose relationship "
        "reverses; selection is seeing the evaluation window"
    )


@pytest.mark.fast
def test_selection_scores_net_not_gross() -> None:
    """Gross selection picks whatever costs destroy.

    Two positions: one marginally better before fees and trading constantly, one
    marginally worse and trading rarely. Net ranks them the other way round, and
    net is what gets traded.
    """
    rng = np.random.default_rng(5)
    n = 4_000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))

    churning = ((np.arange(n) % 3) == 0).astype(float)
    marks = np.zeros(n)
    marks[::400] = 1
    last = np.maximum.accumulate(np.where(marks > 0, np.arange(n), -1))
    patient = ((np.arange(n) - last) < 200).astype(float)

    fast = run(close, churning, BINANCE_RETAIL, "4h")
    slow = run(close, patient, BINANCE_RETAIL, "4h")

    assert fast.gross_sharpe > slow.gross_sharpe, "fixture no longer sets up the trap"
    assert fast.sharpe < slow.sharpe, "costs should reverse the ranking"


@pytest.mark.fast
def test_position_cannot_act_on_the_bar_that_produced_it() -> None:
    """One shift, applied centrally, is the whole difference."""
    close = np.array([100.0, 110.0, 100.0, 110.0, 100.0])
    oracle = np.array([0.0, 1.0, 0.0, 1.0, 0.0])
    free = CostModel(0, 0, 0, 0)
    assert run(close, oracle, free).total_return < run(
        close, oracle, free, already_lagged=True).total_return


@pytest.mark.fast
def test_funding_is_charged_before_the_sharpe_is_taken() -> None:
    """Order matters: a Sharpe computed before costs is a different number."""
    n = 2_000
    close = np.full(n, 100.0)
    rate = np.zeros(n)
    rate[::8] = 1e-4
    result = run(close, np.ones(n), CostModel(0, 0, 0, 0), funding=rate,
                 already_lagged=True)
    assert result.funding_drag > 0
    assert result.sharpe < 0, "flat prices plus funding must give a negative Sharpe"
    assert result.gross_sharpe == 0.0, "gross must be untouched by costs"


@pytest.mark.medium
def test_effective_n_reaches_the_verdict() -> None:
    """The bar count must not be what the gate judges on.

    A long-holding strategy produces thousands of serially dependent bar returns
    and far fewer independent bets; §8.3's arithmetic is meaningless on the
    former.
    """
    # Thresholds low enough that the chosen configuration trades often, so the
    # gate reaches the statistics rather than stopping on the trade minimum. A
    # skipped test verifies nothing, and this invariant is one of the two the
    # whole validation rests on.
    bars = make_noise(n=20_000, seed=31)
    spec = StudySpec(name="eff", params=(8, 14), entries=(50.0, 52.0),
                     min_trades=10, timeframe="4h")
    result = run_study(bars, spec)

    reported = result.gate.metrics["n_obs"]
    assert reported < result.n_bars / 2, (
        f"the gate judged on {reported} observations against {result.n_bars} bars; "
        f"effective sample size is not reaching the statistics"
    )
    assert result.gate.metrics["trials_considered"] == spec.planned_trials


@pytest.mark.fast
def test_trials_counted_are_the_whole_grid() -> None:
    """Every configuration evaluated raises the bar, including the discarded."""
    bars = make_noise(n=6_000, seed=33)
    spec = StudySpec(name="trials", params=(10, 20, 30), entries=(58.0, 62.0),
                     min_trades=5, timeframe="4h")
    result = run_study(bars, spec)
    assert result.executed_trials == spec.planned_trials == 6
    assert result.gate.metrics.get("trials_considered", 0) == 6
