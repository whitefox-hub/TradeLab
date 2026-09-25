"""The exit criterion for phase 3. Plan.md §17.

This is the most important test in the repository, and it is the one that decides
whether anything the system says afterwards is worth reading:

    the validator must reject a strategy built on pure noise,
    even after a hundred thousand trials

and its quieter twin, which matters just as much:

    a real edge below the detectable floor must return `inconclusive`,
    never `pass`

The second is about the system's willingness to say "I do not know". A validator
that passes it claims more than the data supports, and that is the failure this
whole project exists to prevent.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel
from tradelab.engines.tier_a.engine import run, threshold_position
from tradelab.features import momentum  # noqa: F401
from tradelab.features.materialise import materialise
from tradelab.validation.deflated import deflated_sharpe, min_track_record_length, sharpe_stats
from tradelab.validation.pbo import probability_of_backtest_overfitting
from tradelab.validation.synthetic import make_edge, make_noise


def _search(bars, periods, entries, costs: CostModel, timeframe: str = "1h"):
    """Run a grid and return (results, per-config return matrix)."""
    features = materialise(bars, [("rsi", {"period": p, "source": "close"}) for p in periods])
    close = bars["close"].to_numpy().astype(np.float64)
    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]

    results, net, gross, turnover = [], [], [], []
    for period in periods:
        signal = features[f"rsi__period{period}_sourceclose"].to_numpy().astype(np.float64)
        for entry in entries:
            position = threshold_position(signal, entry)
            results.append(run(close, position, costs, timeframe))
            lagged = np.concatenate([[0.0], position[:-1]])
            traded = np.abs(np.diff(np.concatenate([[0.0], lagged])))
            gross.append(lagged * returns)
            net.append(lagged * returns - traded * costs.per_side)
            turnover.append(traded.sum())
    return results, np.column_stack(net), np.column_stack(gross), np.asarray(turnover)


@pytest.mark.slow
def test_noise_survives_no_strategy() -> None:
    """A large search over data with no edge must produce no pass.

    The best of a big grid on noise always looks good — that is precisely the
    point of the Deflated Sharpe. What must not happen is the deflation letting
    it through.
    """
    bars = make_noise(n=30_000, seed=20260101)
    periods = list(range(5, 60))
    entries = np.arange(52.0, 80.0, 0.5)
    results, matrix, _, _ = _search(bars, periods, entries, BINANCE_RETAIL, "1h")
    trials = len(results)
    assert trials > 3000, "the search must be large enough for deflation to matter"

    best_idx = int(np.argmax([r.sharpe for r in results]))
    best = results[best_idx]
    # Effective observations, not bars. A position held for 200 bars yields 200
    # serially dependent returns; counting them as 200 observations overstates
    # every significance figure downstream (§7).
    stats = sharpe_stats(matrix[:, best_idx], effective_n=best.effective_n)

    dsr = deflated_sharpe(stats, trials=trials)
    assert dsr < 0.95, (
        f"The best of {trials:,} configurations on pure noise was accepted with "
        f"DSR={dsr:.3f} (raw Sharpe {best.sharpe:.2f}). Deflation is not working, "
        f"and nothing this system says downstream can be trusted."
    )


@pytest.mark.slow
def test_pbo_detects_that_selection_is_worthless_on_noise() -> None:
    """On noise, picking the in-sample winner must not beat picking at random.

    PBO answers the question a single backtest cannot: is the selection
    *procedure* any good? Above 0.5 it is worse than a coin toss.
    """
    bars = make_noise(n=20_000, seed=7)
    periods = list(range(10, 40, 2))
    entries = np.arange(55.0, 75.0, 2.0)
    _, net, gross, turnover = _search(bars, periods, entries, BINANCE_RETAIL, "1h")

    # On GROSS returns, selection on noise must be unreliable.
    on_gross = probability_of_backtest_overfitting(gross, n_blocks=10, turnover=turnover)
    assert on_gross.pbo > 0.35, (
        f"PBO={on_gross.pbo:.3f} on gross returns over pure noise. Selection cannot "
        f"be reliable on data with nothing to select."
    )

    # On NET returns it collapses to ~0, and that is not a bug. Net Sharpe
    # correlates about -0.95 with turnover, so the search selects the
    # configuration that trades least — which persists out of sample perfectly
    # well without being alpha. The diagnostic exists so this is visible instead
    # of being read as a clean bill of health.
    on_net = probability_of_backtest_overfitting(net, n_blocks=10, turnover=turnover)
    assert on_net.pbo < on_gross.pbo
    assert on_net.measures_turnover_not_alpha, (
        f"turnover correlation {on_net.turnover_correlation} did not flag that this "
        f"PBO is about trading frequency rather than skill."
    )


@pytest.mark.slow
def test_a_real_edge_is_not_rejected() -> None:
    """The other half: the validator must not reject everything.

    A test suite that only checks rejection is satisfied by a validator that
    rejects unconditionally, which is useless in a different direction.
    """
    bars, _ = make_edge(n=30_000, seed=11, edge_sharpe=3.0, signal_period=80)
    periods = list(range(10, 50, 2))
    entries = np.arange(45.0, 70.0, 1.0)
    free = CostModel(0.0, 0.0, 0.0, 0.0)
    results, matrix, _, _ = _search(bars, periods, entries, free, "1h")

    best_idx = int(np.argmax([r.sharpe for r in results]))
    stats = sharpe_stats(matrix[:, best_idx], effective_n=results[best_idx].effective_n)
    assert abs(stats.sharpe) > 0, "the planted edge produced no signal at all"
    assert deflated_sharpe(stats, trials=len(results)) > 0.5, (
        "A large planted edge was deflated away. The validator rejects everything, "
        "which is as useless as accepting everything."
    )


@pytest.mark.slow
def test_subthreshold_edge_is_inconclusive_not_pass() -> None:
    """The system's willingness to say "I do not know".

    A true edge below what the sample can resolve must not be reported as
    established. MinTRL is the operational form: when the track record needed
    exceeds the history available, the verdict is `inconclusive`.
    """
    bars, _ = make_edge(n=4_000, seed=13, edge_sharpe=0.15, signal_period=200)
    periods = list(range(10, 40, 2))
    entries = np.arange(50.0, 70.0, 2.0)
    results, matrix, _, _ = _search(bars, periods, entries, BINANCE_RETAIL, "1h")

    best_idx = int(np.argmax([r.sharpe for r in results]))
    stats = sharpe_stats(matrix[:, best_idx], effective_n=results[best_idx].effective_n)
    trials = len(results)

    required = min_track_record_length(stats, trials=trials)
    dsr = deflated_sharpe(stats, trials=trials)
    assert required > stats.n_obs or dsr < 0.95, (
        f"A sub-threshold edge was reported as established: DSR={dsr:.3f}, "
        f"MinTRL={required:.0f} against {stats.n_obs} observations. The correct "
        f"answer here is `inconclusive`."
    )


@pytest.mark.slow
def test_selection_never_sees_the_evaluation_window() -> None:
    """Configuration is chosen on training data, and only then evaluated.

    This failed silently for several days while a comment above the offending
    line asserted the opposite — the worst shape a defect can take, because a
    reader who checks the documentation concludes the invariant holds.

    The test constructs data where the two windows disagree: a signal that works
    in the first half and reverses in the second. A selector peeking at the
    evaluation window picks the configuration that wins there; one restricted to
    training cannot.
    """
    from tradelab.orchestration.study import StudySpec, run_study
    from tradelab.validation.synthetic import make_edge

    bars, _ = make_edge(n=20_000, seed=23, edge_sharpe=2.5, signal_period=100)
    # Reverse the second half, so what worked in training is wrong in the test
    # window and vice versa.
    close = bars["close"].to_numpy().astype(np.float64)
    half = len(close) // 2
    flipped = close.copy()
    flipped[half:] = close[half] ** 2 / close[half:]
    bars = bars.with_columns(
        close=pl.Series(flipped),
        high=pl.Series(flipped * 1.001),
        low=pl.Series(flipped * 0.999),
        open=pl.Series(np.concatenate([[flipped[0]], flipped[:-1]])),
    )

    spec = StudySpec(
        name="leak-probe", params=tuple(range(10, 40, 4)),
        entries=tuple(np.arange(55.0, 75.0, 2.0)), min_trades=30,
        # Verifying the selection boundary, not power. Waived explicitly, which
        # is the distinction §8.8's check exists to force.
        require_power=False,
    )
    result = run_study(bars, spec)

    # The honest outcome on data whose relationship reverses is a poor
    # out-of-sample figure. A selector with the test set in hand would have
    # found the configuration that survives the reversal and reported it well.
    assert result.gate.metrics.get("sharpe_annualised", 0) < 3.0, (
        "the out-of-sample figure is implausibly good on data whose relationship "
        "reverses halfway; selection is seeing the evaluation window"
    )
