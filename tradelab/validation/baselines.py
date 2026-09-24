"""Null baselines. Plan.md §8.4.

No result is interpreted in absolute terms. A Sharpe of 1.8 means nothing until
you know what a strategy with no skill would have produced on the same data, with
the same trade count, paying the same costs — and often that is 1.6.

Three nulls, each removing a different way of being fooled:

**Buy and hold** — did the strategy beat simply owning the asset? In a market
that rose 10x, most long-biased strategies look excellent and add nothing.

**Matched random entry** — the important one. Same number of trades, same holding
distribution, same costs, random timing. It isolates *timing skill* from
everything else: exposure, drift, and the mechanical effect of trading a
trending asset.

**Permuted signal** — the signal is shuffled against the price. It destroys any
relationship while preserving both marginal distributions, so surviving it means
the relationship, not the shapes, carried the result.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tradelab.engines.tier_a.costs import CostModel
from tradelab.engines.tier_a.engine import run


@dataclass(frozen=True, slots=True)
class BaselineResult:
    name: str
    percentile: float        # where the strategy sits in this null distribution
    null_mean: float
    null_std: float
    strategy_sharpe: float
    n_samples: int

    @property
    def beats_null(self) -> bool:
        return self.percentile >= 0.95


def buy_and_hold(close: np.ndarray, costs: CostModel, timeframe: str) -> float:
    position = np.ones_like(close)
    return run(close, position, costs, timeframe, already_lagged=True).sharpe


def matched_random_entry(
    close: np.ndarray, position: np.ndarray, costs: CostModel, timeframe: str,
    n_samples: int = 1000, seed: int = 20260101,
) -> tuple[float, np.ndarray]:
    """Random entries matched on trade count and holding-period distribution.

    Matching matters. An unmatched random baseline that trades once is not a
    comparison; it is a different strategy. Preserving the run-length structure
    means the null differs from the strategy in *timing alone*.
    """
    rng = np.random.default_rng(seed)
    n = close.size

    # Run lengths of the real position, so the null keeps its holding structure.
    changes = np.flatnonzero(np.diff(np.concatenate([[0.0], position, [0.0]])))
    runs = [(changes[i + 1] - changes[i]) for i in range(0, len(changes) - 1, 2)]
    if not runs:
        return 0.0, np.zeros(n_samples)

    sharpes = np.empty(n_samples)
    for s in range(n_samples):
        fake = np.zeros(n)
        for length in runs:
            start = rng.integers(0, max(n - length, 1))
            fake[start:start + length] = 1.0
        sharpes[s] = run(close, fake, costs, timeframe, already_lagged=True).sharpe
    return float(np.median(sharpes)), sharpes


def permuted_signal(
    close: np.ndarray, signal: np.ndarray, threshold: float, costs: CostModel,
    timeframe: str, n_samples: int = 1000, seed: int = 20260101,
) -> np.ndarray:
    """Shuffle the signal against the price, preserving both distributions."""
    rng = np.random.default_rng(seed)
    sharpes = np.empty(n_samples)
    for s in range(n_samples):
        shuffled = rng.permutation(signal)
        sharpes[s] = run(close, (shuffled > threshold).astype(np.float64),
                         costs, timeframe, already_lagged=True).sharpe
    return sharpes


def evaluate(
    strategy_sharpe: float, null_sharpes: np.ndarray, name: str,
) -> BaselineResult:
    finite = null_sharpes[np.isfinite(null_sharpes)]
    return BaselineResult(
        name=name,
        percentile=float((finite <= strategy_sharpe).mean()) if finite.size else 0.0,
        null_mean=float(finite.mean()) if finite.size else 0.0,
        null_std=float(finite.std(ddof=1)) if finite.size > 1 else 0.0,
        strategy_sharpe=strategy_sharpe,
        n_samples=int(finite.size),
    )
