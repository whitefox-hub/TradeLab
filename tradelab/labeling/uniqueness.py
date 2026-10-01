"""Label concurrency and sample uniqueness (López de Prado). Plan.md §7, §8.8.

Uniqueness = mean over a label's bars of 1/concurrency; the sum is the effective sample
size that significance figures (DSR, MinTRL, §8.8 floor) must use instead of raw count.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True, slots=True)
class UniquenessResult:
    weights: np.ndarray
    concurrency: np.ndarray
    effective_n: float
    raw_n: int

    @property
    def inflation_factor(self) -> float:
        """How many times larger the naive count is than the effective one."""
        return self.raw_n / max(self.effective_n, 1e-9)


def concurrency(start_idx: np.ndarray, end_idx: np.ndarray, n_bars: int) -> np.ndarray:
    """Labels active at each bar, by index (difference array, O(n))."""
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

    Uses `bars_held` (determination time), not the maximum horizon.
    """
    start = np.arange(labels.height)
    held = labels["bars_held"].to_numpy().astype(np.int64)
    end = start + np.maximum(held, 0)
    total_bars = n_bars if n_bars is not None else int(end.max() + 1)
    return average_uniqueness(start, np.minimum(end, total_bars - 1), total_bars)
