"""Label concurrency and sample uniqueness. Plan.md §7, §8.8.

The correction this makes is unglamorous and affects everything downstream.

A label opened at t and determined at t+3d spans three days. So does the one
opened an hour later. They are not two independent observations of the market —
they mostly observe the *same* three days. Counting them as two inflates the
sample, and every significance figure computed from that count inherits the
inflation: the Deflated Sharpe, the Minimum Track Record Length, the detectable
floor in §8.8. All of them become more confident than the data supports.

López de Prado's correction: a label's **uniqueness** is the average, over the
bars it spans, of one divided by how many labels span that bar. Summing the
uniqueness of every label gives the **effective** number of observations, which
is the number the statistics should have been using all along.

For a strategy holding for days on hourly bars the difference is not marginal.
It is the difference between believing you have 20,000 observations and knowing
you have 800.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True, slots=True)
class UniquenessResult:
    weights: np.ndarray            # per-label average uniqueness, in [0, 1]
    concurrency: np.ndarray        # per-bar count of active labels
    effective_n: float             # sum of weights: the honest observation count
    raw_n: int

    @property
    def inflation_factor(self) -> float:
        """How many times larger the naive count is than the effective one."""
        return self.raw_n / max(self.effective_n, 1e-9)


def concurrency(start_idx: np.ndarray, end_idx: np.ndarray, n_bars: int) -> np.ndarray:
    """Labels active at each bar, by index.

    A difference array rather than a loop over labels: O(n) instead of O(labels x span).
    """
    counts = np.zeros(n_bars + 1, dtype=np.int64)
    np.add.at(counts, start_idx, 1)
    np.add.at(counts, np.minimum(end_idx + 1, n_bars), -1)
    return np.cumsum(counts)[:n_bars]


def average_uniqueness(start_idx: np.ndarray, end_idx: np.ndarray, n_bars: int) -> UniquenessResult:
    """Uniqueness per label and the effective sample size."""
    active = concurrency(start_idx, end_idx, n_bars)
    inverse = np.divide(1.0, active, out=np.zeros(n_bars, dtype=np.float64), where=active > 0)
    cumulative = np.concatenate([[0.0], np.cumsum(inverse)])

    span = (end_idx - start_idx + 1).astype(np.float64)
    total = cumulative[np.minimum(end_idx + 1, n_bars)] - cumulative[start_idx]
    weights = np.divide(total, span, out=np.zeros_like(total), where=span > 0)

    return UniquenessResult(
        weights=weights,
        concurrency=active,
        effective_n=float(weights.sum()),
        raw_n=int(weights.size),
    )


def from_labels(labels: pl.DataFrame, n_bars: int | None = None) -> UniquenessResult:
    """Uniqueness from a triple-barrier output (§7).

    Uses `bars_held`, which is the determination time — how long the label was
    actually open — rather than the maximum horizon. Overlap is a property of
    what happened, not of what was permitted.
    """
    start = np.arange(labels.height)
    held = labels["bars_held"].to_numpy().astype(np.int64)
    end = start + np.maximum(held, 0)
    total_bars = n_bars if n_bars is not None else int(end.max() + 1)
    return average_uniqueness(start, np.minimum(end, total_bars - 1), total_bars)


def sequential_bootstrap(
    start_idx: np.ndarray, end_idx: np.ndarray, n_bars: int,
    size: int | None = None, seed: int = 20260101,
) -> np.ndarray:
    """Draw samples favouring labels that overlap what is already drawn least.

    Ordinary bootstrap over overlapping labels resamples the same few periods
    repeatedly and understates variance — which makes confidence intervals
    narrower than the data earns.
    """
    rng = np.random.default_rng(seed)
    n_labels = start_idx.size
    size = size or n_labels
    drawn: list[int] = []
    counts = np.zeros(n_bars, dtype=np.float64)

    for _ in range(size):
        # Uniqueness of each candidate given what has already been drawn.
        avg = np.empty(n_labels)
        for i in range(n_labels):
            lo, hi = start_idx[i], min(end_idx[i] + 1, n_bars)
            window = counts[lo:hi]
            avg[i] = np.mean(1.0 / (1.0 + window)) if hi > lo else 0.0
        probability = avg / avg.sum() if avg.sum() > 0 else np.full(n_labels, 1 / n_labels)
        pick = int(rng.choice(n_labels, p=probability))
        drawn.append(pick)
        counts[start_idx[pick]:min(end_idx[pick] + 1, n_bars)] += 1.0

    return np.asarray(drawn)
