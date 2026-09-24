"""Purged cross-validation with embargo. Plan.md §8.2.

The defect this exists to prevent is invisible: a naive temporal split leaks
through *overlapping labels*. A label opened at t and determined at t+3d carries
information about the window [t, t+3d]; if t falls in train and t+3d falls in
test, the training set already knows part of the test outcome. Nothing raises.
The result is simply better than it should be.

Two mechanisms, and neither is a tunable:

**Purging** removes training samples whose label window overlaps the test window.
The window comes from `determined_at` — when the outcome actually became known
(§7) — not from the maximum horizon, which would be conservative and wasteful.

**Embargo** additionally drops a span immediately after the test set, because
serial correlation carries information across the boundary even when label
windows do not overlap.

The embargo length is derived from the feature set's declared lookback, and the
purge from the labels themselves. Neither is a number anyone chooses, which is
the point: a purge someone can tune is a purge that gets tuned down.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import combinations

import numpy as np

#: Module-level singleton so it is not constructed in a default argument.
NO_EMBARGO = np.timedelta64(0, "s")


@dataclass(frozen=True, slots=True)
class Split:
    train: np.ndarray
    test: np.ndarray
    purged: int
    embargoed: int

    @property
    def train_size(self) -> int:
        return self.train.size


def purge(
    event_time: np.ndarray, determined_at: np.ndarray,
    train_idx: np.ndarray, test_idx: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Drop training samples whose label window overlaps the test window."""
    if test_idx.size == 0 or train_idx.size == 0:
        return train_idx, 0
    test_start = event_time[test_idx].min()
    test_end = determined_at[test_idx].max()

    # Overlap in either direction: a label that opens before the test and closes
    # inside it, or one that opens inside it at all.
    overlaps = (determined_at[train_idx] >= test_start) & (event_time[train_idx] <= test_end)
    kept = train_idx[~overlaps]
    return kept, int(overlaps.sum())


def embargo(
    event_time: np.ndarray, train_idx: np.ndarray, test_idx: np.ndarray, span: np.timedelta64,
) -> tuple[np.ndarray, int]:
    """Drop training samples in the span immediately after the test set."""
    if span <= NO_EMBARGO or test_idx.size == 0:
        return train_idx, 0
    test_end = event_time[test_idx].max()
    banned = (event_time[train_idx] > test_end) & (event_time[train_idx] <= test_end + span)
    kept = train_idx[~banned]
    return kept, int(banned.sum())


def purged_kfold(
    event_time: np.ndarray, determined_at: np.ndarray, n_splits: int = 6,
    embargo_span: np.timedelta64 = NO_EMBARGO,
) -> Iterator[Split]:
    """Contiguous test folds in time order, with purge and embargo applied."""
    n = event_time.size
    bounds = np.linspace(0, n, n_splits + 1).astype(int)
    for k in range(n_splits):
        test_idx = np.arange(bounds[k], bounds[k + 1])
        train_idx = np.setdiff1d(np.arange(n), test_idx, assume_unique=True)
        train_idx, n_purged = purge(event_time, determined_at, train_idx, test_idx)
        train_idx, n_embargoed = embargo(event_time, train_idx, test_idx, embargo_span)
        yield Split(train_idx, test_idx, n_purged, n_embargoed)


def combinatorial_purged(
    event_time: np.ndarray, determined_at: np.ndarray,
    n_groups: int = 6, n_test_groups: int = 2,
    embargo_span: np.timedelta64 = NO_EMBARGO,
) -> Iterator[Split]:
    """CPCV (López de Prado): every combination of `n_test_groups` as the test set.

    Produces C(n_groups, n_test_groups) backtest paths instead of one, which is
    what turns a single Sharpe into a *distribution* — and that distribution is
    the input PBO needs (§8.3). One number cannot tell you how often the
    selection procedure would have chosen wrong.
    """
    n = event_time.size
    bounds = np.linspace(0, n, n_groups + 1).astype(int)
    groups = [np.arange(bounds[i], bounds[i + 1]) for i in range(n_groups)]

    for chosen in combinations(range(n_groups), n_test_groups):
        test_idx = np.concatenate([groups[i] for i in chosen])
        train_idx = np.setdiff1d(np.arange(n), test_idx, assume_unique=True)
        train_idx, n_purged = purge(event_time, determined_at, train_idx, test_idx)
        train_idx, n_embargoed = embargo(event_time, train_idx, test_idx, embargo_span)
        yield Split(train_idx, test_idx, n_purged, n_embargoed)


def n_paths(n_groups: int, n_test_groups: int) -> int:
    from math import comb

    return comb(n_groups, n_test_groups)


def derive_embargo(max_feature_lookback_bars: int, bar_seconds: int,
                   safety: float = 1.0) -> np.timedelta64:
    """Embargo from the declared feature lookback, never from preference.

    A feature reaching back `L` bars means a training sample within `L` bars
    after the test set was computed partly from test data. The declared lookback
    is therefore the honest embargo, which is also why §16.3 tests that the
    declaration is true — an understated lookback silently shortens this.
    """
    return np.timedelta64(int(max_feature_lookback_bars * bar_seconds * safety), "s")
