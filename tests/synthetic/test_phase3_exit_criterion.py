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

    on_gross = probability_of_backtest_overfitting(gross, n_blocks=10, turnover=turnover)
    assert on_gross.pbo > 0.35, (
        f"PBO={on_gross.pbo:.3f} on gross returns over pure noise. Selection cannot "
        f"be reliable on data with nothing to select."
    )

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
    """Configurations are scored on training bars only. Plan.md §8.7.

    This failed silently for several days while a comment above the offending
    line asserted the opposite — the worst shape a defect can take, because a
    reader who checks the documentation concludes the invariant holds.

    Stated as an identity rather than as a symptom: replace the held-out window
    with an unrelated price path, leave the training bars untouched, and every
    screening score must come back bit-identical. Scored on the full series,
    all of them move.

    The previous version of this test asserted that out-of-sample Sharpe stayed
    below 3.0 on data whose relationship reversed halfway. It never once
    exercised the defect. The study produced 70 out-of-sample trades, under the
    gate's minimum of 100, so the gate returned before writing
    `sharpe_annualised` and the assertion reduced to `0 < 3.0`. Putting the leak
    back confirmed it: the test still passed.
    """
    import numpy as np

    from tradelab.orchestration.study import StudySpec, run_study
    from tradelab.validation.synthetic import make_noise

    oos_fraction = 0.3
    base = make_noise(n=6_000, seed=41)
    close = base["close"].to_numpy().astype(np.float64)
    split = int(len(close) * (1 - oos_fraction))

    other = make_noise(n=6_000, seed=999)["close"].to_numpy().astype(np.float64)
    alt = close.copy()
    alt[split:] = close[split - 1] * other[split:] / other[split - 1]
    perturbed = base.with_columns(
        close=pl.Series(alt), high=pl.Series(alt * 1.001), low=pl.Series(alt * 0.999),
        open=pl.Series(np.concatenate([[alt[0]], alt[:-1]])),
    )
    assert np.array_equal(close[:split], alt[:split]), "training bars must be identical"

    def scores(frame):
        spec = StudySpec(
            name="leak-probe", params=tuple(range(10, 40, 4)),
            entries=tuple(np.arange(55.0, 75.0, 4.0)), min_trades=15,
            oos_fraction=oos_fraction,
            require_power=False,
        )
        return np.array([r["sharpe"] for r in run_study(frame, spec).tier_a_rows])

    a, b = scores(base), scores(perturbed)
    moved = int(np.sum(a != b))
    assert moved == 0, (
        f"{moved} of {len(a)} screening scores changed when only the held-out "
        f"window was altered; selection is reading the evaluation window"
    )
