"""Walk-forward validation. Plan.md §8.1.

A single train/test split assumes one relationship holds across the whole
history. Seven years of crypto does not oblige: the market of 2020 is not the
market of 2026, and a strategy validated against a regime that has ended is
validated against nothing.

The instinct to fix that by using less history is the wrong trade. Shortening the
window costs statistical power — measured on this data, going from seven years to
one raises the smallest resolvable effect by 2.7x — without addressing
non-stationarity at all; it just picks one regime instead of averaging several.

Walk-forward resolves it properly. Select on a training window, evaluate on the
period that follows, roll forward, repeat. Every selection uses only what
preceded it, so the concatenated out-of-sample track record is what the strategy
would actually have earned while being re-chosen periodically. All the history is
used and stationarity is never assumed across it.

Two outputs, and the second is the one people forget to look at:

**The concatenated OOS track record**, which is a genuine out-of-sample result
rather than a split someone chose.

**The sequence of selected parameters.** If every window picks something
different, there is no stable relationship to trade — and that is visible here
and nowhere else. A strategy can show a respectable walk-forward return while its
chosen parameters wander, which means the return came from the re-selection
rather than from anything the parameters describe.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Window:
    train_start: int
    train_end: int      # exclusive; the purge gap sits between this and test_start
    test_start: int
    test_end: int       # exclusive

    @property
    def train_size(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_size(self) -> int:
        return self.test_end - self.test_start


@dataclass
class WalkForwardResult:
    windows: list[Window]
    selections: list[dict]
    oos_returns: np.ndarray
    per_window_sharpe: list[float]
    total_trials: int
    purge_bars: int

    @property
    def n_windows(self) -> int:
        return len(self.windows)

    @property
    def parameter_churn(self) -> float:
        """Share of windows that changed their selection.

        Near zero means one configuration keeps winning, which is what a stable
        relationship looks like. Near one means the choice is being redrawn every
        window, and any return that produces came from re-selecting rather than
        from the parameters.
        """
        if len(self.selections) < 2:
            return 0.0
        changes = sum(
            1 for a, b in zip(self.selections, self.selections[1:], strict=False) if a != b
        )
        return changes / (len(self.selections) - 1)

    def most_common_selection(self) -> dict:
        from collections import Counter

        counts = Counter(tuple(sorted(s.items())) for s in self.selections)
        return dict(counts.most_common(1)[0][0])


def windows(
    n: int, n_windows: int = 6, train_fraction: float = 0.5,
    purge_bars: int = 0, anchored: bool = False,
) -> Iterator[Window]:
    """Sequential train/test windows with a purge gap between them.

    `anchored` grows the training set from the start instead of sliding it. Both
    are worth running: anchored asks whether more history helps, rolling asks
    whether old history still applies, and disagreement between them is itself
    a finding about stationarity.
    """
    usable = n - purge_bars
    test_size = int(usable * (1 - train_fraction) / n_windows)
    train_size = int(usable * train_fraction)
    if test_size < 1 or train_size < 1:
        raise ValueError("not enough data for the requested window layout")

    for k in range(n_windows):
        test_start = train_size + purge_bars + k * test_size
        test_end = min(test_start + test_size, n)
        if test_start >= n or test_end <= test_start:
            break
        train_end = test_start - purge_bars
        train_start = 0 if anchored else max(0, train_end - train_size)
        yield Window(train_start, train_end, test_start, test_end)


def run_walk_forward(
    select: Callable[[int, int], tuple[dict, int]],
    evaluate: Callable[[dict, int, int], np.ndarray],
    n: int, n_windows: int = 6, train_fraction: float = 0.5,
    purge_bars: int = 0, anchored: bool = False,
) -> WalkForwardResult:
    """Drive the loop.

    `select(train_start, train_end)` returns the chosen parameters and how many
    configurations it evaluated — the second value matters, because every window
    spends its own trials and the total is what deflates the final Sharpe (§8.3).
    """
    collected: list[np.ndarray] = []
    chosen: list[dict] = []
    sharpes: list[float] = []
    used: list[Window] = []
    trials = 0

    for window in windows(n, n_windows, train_fraction, purge_bars, anchored):
        params, window_trials = select(window.train_start, window.train_end)
        trials += window_trials
        returns = evaluate(params, window.test_start, window.test_end)

        used.append(window)
        chosen.append(params)
        collected.append(returns)
        std = returns.std(ddof=1) if returns.size > 1 else 0.0
        sharpes.append(float(returns.mean() / std) if std > 0 else 0.0)

    return WalkForwardResult(
        windows=used, selections=chosen,
        oos_returns=np.concatenate(collected) if collected else np.array([]),
        per_window_sharpe=sharpes, total_trials=trials, purge_bars=purge_bars,
    )
