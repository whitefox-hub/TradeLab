"""Ordering invariants of the pipeline, on data built to expose each failure.

Written after a defect survived for days because the comment above it asserted
the invariant the line violated. Reading code is not verification; each test here
constructs data where the wrong order produces a visibly wrong answer.

Small by design. The point is the order, not the sample. Which is also why
every spec here sets `require_power=False`: these samples are deliberately
too small to resolve anything, and the §8.8 precondition would reject them
before the ordering under test ever ran. The waiver is explicit rather than
accidental — that distinction is the whole reason the check exists.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel
from tradelab.engines.tier_a.engine import run
from tradelab.orchestration.study import StudySpec, run_study
from tradelab.validation.synthetic import make_noise


def _same_training_different_future(
    n: int = 6_000, seed: int = 41, other_seed: int = 999, oos_fraction: float = 0.3,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Two frames with byte-identical training bars and unrelated held-out bars.

    The held-out window is replaced by an independent path spliced on at the
    same price, so nothing about the training window changes — not one float.
    """
    base = make_noise(n=n, seed=seed)
    close = base["close"].to_numpy().astype(np.float64)
    split = int(len(close) * (1 - oos_fraction))

    other = make_noise(n=n, seed=other_seed)["close"].to_numpy().astype(np.float64)
    alt = close.copy()
    alt[split:] = close[split - 1] * other[split:] / other[split - 1]
    perturbed = base.with_columns(
        close=pl.Series(alt), high=pl.Series(alt * 1.001), low=pl.Series(alt * 0.999),
        open=pl.Series(np.concatenate([[alt[0]], alt[:-1]])),
    )
    assert np.array_equal(close[:split], alt[:split])
    return base, perturbed


@pytest.mark.medium
def test_selection_scores_do_not_move_when_only_the_future_changes() -> None:
    """The ordering invariant stated directly, rather than through a proxy.

    If the screening loop scores configurations on the training window, then
    replacing the held-out window with unrelated data cannot change a single
    score. If it scores the full series, every score moves. There is no
    threshold to choose and no fixture behaviour to reason about: the expected
    difference is exactly zero.

    This replaced a test that asserted a poor out-of-sample Sharpe on data whose
    relationship reversed halfway. That test never worked. Reinstating the leak
    left it passing, for two compounding reasons:

      * it read `gate.metrics.get("sharpe_annualised", 0.0)`, and the gate
        returns early below its trade minimum without ever writing that key, so
        the comparison was `0.0 < 3.0`;
      * the fixture did not reverse anything a threshold rule can feel. An
        inverted price path is the same cycle at a different phase, and RSI
        finds it either way — measured out-of-sample Sharpe was 53.1 with the
        leak fixed and 53.3 with it present.

    Selection bias is also harder to see downstream than it looks, because §8.5
    promotes the neighbourhood median rather than the peak. Under the leak the
    chosen parameters here are *unchanged* while all forty scores move. Any test
    watching the winner, or the winner's performance, would still be blind.
    """
    from tradelab.orchestration.study import run_study

    base, perturbed = _same_training_different_future()
    spec = dict(name="inv", params=tuple(range(10, 40, 4)),
                entries=tuple(np.arange(55.0, 75.0, 4.0)),
                min_trades=15, oos_fraction=0.3, require_power=False)

    scores = [
        np.array([row["sharpe"] for row in run_study(frame, StudySpec(**spec)).tier_a_rows])
        for frame in (base, perturbed)
    ]
    moved = int(np.sum(scores[0] != scores[1]))
    assert len(scores[0]) == 40, "the grid changed; the counts below are stale"
    assert moved == 0, (
        f"{moved} of {len(scores[0])} selection scores changed when only the "
        f"held-out window was altered (largest move "
        f"{np.max(np.abs(scores[0] - scores[1])):.4f}). Selection is reading the "
        f"evaluation window."
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
    bars = make_noise(n=20_000, seed=31)
    spec = StudySpec(name="eff", params=(8, 14), entries=(50.0, 52.0),
                     min_trades=10, timeframe="4h", require_power=False)
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
                     min_trades=5, timeframe="4h", require_power=False)
    result = run_study(bars, spec)
    assert result.executed_trials == spec.planned_trials == 6
    assert result.gate.metrics.get("trials_considered", 0) == 6


@pytest.mark.fast
def test_walk_forward_never_offers_future_indices_to_the_selector() -> None:
    """The framework's half of the contract.

    `select(lo, hi)` is user-supplied and can cheat; what must hold is that the
    bounds it receives never overlap the window it is about to be evaluated on,
    and that the purge gap is genuinely absent from both sides.
    """
    from tradelab.validation.walkforward import run_walk_forward

    purge = 120
    seen: list[tuple[int, int, int, int]] = []

    def select(lo: int, hi: int) -> tuple[dict, int]:
        seen.append((lo, hi, -1, -1))
        return {"k": 1}, 3

    def evaluate(params: dict, lo: int, hi: int) -> np.ndarray:
        train_lo, train_hi, _, _ = seen[-1]
        seen[-1] = (train_lo, train_hi, lo, hi)
        return np.zeros(hi - lo)

    run_walk_forward(select, evaluate, n=12_000, n_windows=4, purge_bars=purge)

    assert seen, "no windows were produced"
    for train_lo, train_hi, test_lo, test_hi in seen:
        assert train_hi <= test_lo, "training reached into the evaluation window"
        assert test_lo - train_hi == purge, "the purge gap was not left empty"
        assert train_lo < train_hi < test_lo < test_hi


@pytest.mark.fast
def test_funding_is_charged_at_settlements_not_every_bar() -> None:
    """Funding is a payment at an instant, not a level that persists.

    Forward-filling it would charge every bar instead of three a day — a 6x
    overstatement on hourly bars, applied silently to every long-biased result.
    """
    n = 2_400
    close = np.full(n, 100.0)
    settlements = np.zeros(n)
    settlements[::8] = 1e-4
    carried = np.full(n, 1e-4)

    correct = run(close, np.ones(n), CostModel(0, 0, 0, 0), funding=settlements,
                  already_lagged=True)
    wrong = run(close, np.ones(n), CostModel(0, 0, 0, 0), funding=carried,
                already_lagged=True)

    assert wrong.funding_drag == pytest.approx(correct.funding_drag * 8, rel=0.02)


@pytest.mark.medium
def test_pooled_reports_the_score_it_did_not_select_on() -> None:
    """In-sample chooses, out-of-sample reports, and they must differ.

    Selecting on the out-of-sample score and then quoting it is choosing with the
    test set in hand and citing the test as evidence.
    """
    from tradelab.orchestration.pooled import PooledConfig, PooledResult

    rng = np.random.default_rng(7)
    configs = [
        PooledConfig(
            params={"period": p, "entry": 1.0},
            pooled_returns=rng.normal(0.0, 0.01, 500),
            is_returns=rng.normal(0.001 * (p == 20), 0.01, 500),
            per_asset_sharpe={"A": 0.1}, total_trades=100, effective_n=50.0,
        )
        for p in (10, 20, 30)
    ]
    result = PooledResult(configs=configs, assets=["A"], trials=3)

    chosen = result.best()
    assert chosen.params["period"] == 20, "selection did not use the in-sample score"
    assert chosen.sharpe != chosen.is_sharpe, (
        "the reported figure is the one selection used; they must be different "
        "windows"
    )
