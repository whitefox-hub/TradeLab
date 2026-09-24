"""Walk-forward validation. Plan.md §8.1."""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.validation.walkforward import run_walk_forward, windows


@pytest.mark.fast
def test_training_never_overlaps_its_own_test_period() -> None:
    """The property the whole scheme rests on."""
    for window in windows(10_000, n_windows=5, train_fraction=0.5, purge_bars=100):
        assert window.train_end <= window.test_start
        assert window.train_start < window.train_end
        assert window.test_start < window.test_end


@pytest.mark.fast
def test_the_purge_gap_is_actually_left_empty() -> None:
    """Purging is only real if the bars are absent from both sides.

    A gap that appears in the window arithmetic but not in the indices is the
    kind of defect that produces a clean-looking validation and a leak.
    """
    purge = 250
    for window in windows(20_000, n_windows=4, purge_bars=purge):
        assert window.test_start - window.train_end == purge


@pytest.mark.fast
def test_rolling_and_anchored_differ_in_where_training_starts() -> None:
    """Both are worth running: anchored asks whether more history helps, rolling
    asks whether old history still applies, and disagreement is itself a finding
    about stationarity."""
    rolling = list(windows(20_000, n_windows=4, anchored=False))
    anchored = list(windows(20_000, n_windows=4, anchored=True))

    assert all(w.train_start == 0 for w in anchored)
    assert rolling[-1].train_start > 0
    assert anchored[-1].train_size > anchored[0].train_size


@pytest.mark.fast
def test_trials_accumulate_across_windows() -> None:
    """Every window spends its own trials, and the total is what deflates the
    final Sharpe (§8.3). Counting only the last window lowers the bar."""
    rng = np.random.default_rng(1)
    per_window = 40

    def select(lo: int, hi: int) -> tuple[dict, int]:
        return {"k": int(rng.integers(0, 5))}, per_window

    def evaluate(params: dict, lo: int, hi: int) -> np.ndarray:
        return rng.normal(0.0, 0.01, hi - lo)

    result = run_walk_forward(select, evaluate, n=10_000, n_windows=5)
    assert result.total_trials == per_window * result.n_windows


@pytest.mark.fast
def test_parameter_churn_separates_a_stable_choice_from_a_wandering_one() -> None:
    """The output people forget to look at.

    A strategy can post a respectable walk-forward return while its chosen
    parameters wander, and in that case the return came from re-selecting rather
    than from anything the parameters describe.
    """
    rng = np.random.default_rng(2)

    def evaluate(params: dict, lo: int, hi: int) -> np.ndarray:
        return rng.normal(0.0, 0.01, hi - lo)

    stable = run_walk_forward(lambda a, b: ({"k": 7}, 10), evaluate, n=10_000, n_windows=6)
    counter = iter(range(100))
    wandering = run_walk_forward(
        lambda a, b: ({"k": next(counter)}, 10), evaluate, n=10_000, n_windows=6)

    assert stable.parameter_churn == 0.0
    assert wandering.parameter_churn == 1.0
    assert stable.most_common_selection() == {"k": 7}


@pytest.mark.slow
def test_concatenated_returns_span_every_test_window() -> None:
    """The out-of-sample track record is the whole sequence, not one split."""

    def evaluate(params: dict, lo: int, hi: int) -> np.ndarray:
        return np.full(hi - lo, 0.001)

    result = run_walk_forward(lambda a, b: ({"k": 1}, 5), evaluate, n=20_000, n_windows=5)
    assert result.oos_returns.size == sum(w.test_size for w in result.windows)
    assert result.oos_returns.size > 0


@pytest.mark.fast
def test_an_impossible_layout_is_refused() -> None:
    """Too little data for the requested windows must fail loudly rather than
    silently producing one degenerate split."""
    with pytest.raises(ValueError, match="not enough data"):
        list(windows(50, n_windows=20, train_fraction=0.9, purge_bars=40))
