"""Robustness by resampling. Plan.md §8.6, §14.6.

A backtest reports one number per metric, and each of those is a single draw from
a distribution nobody looked at. The maximum drawdown is the clearest case: it is
not a property of the strategy, it is what happened to occur in the one ordering
of trades that history produced. A different ordering of the *same trades* gives
a different figure, often much worse.

That distribution is not an academic nicety. §14.6 forbids retiring a strategy
for a drawdown inside its expected range — and it needs that range to exist. This
module is what produces it, and without it the rule protecting good strategies
from being killed during ordinary losses has no reference to point at.

Three resamplings, each removing a different way of mistaking one realisation for
the truth:

**Stationary bootstrap** (Politis-Romano) for confidence intervals on returns.
Blocks of random geometric length preserve serial dependence; an iid bootstrap
destroys it and returns intervals far narrower than the data earns.

**Trade reordering** for the drawdown distribution. Same trades, different
sequence.

**Input perturbation** for sensitivity to prices that could plausibly have been
slightly different.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Interval:
    point: float
    low: float
    high: float
    confidence: float

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class DrawdownDistribution:
    observed: float
    samples: np.ndarray
    percentile_of_observed: float

    def quantile(self, q: float) -> float:
        return float(np.quantile(self.samples, q))

    def is_within_expectation(self, drawdown: float, q: float = 0.95) -> bool:
        """Whether a drawdown is ordinary for this strategy (§14.6).

        Drawdowns are negative, so "worse than expected" means below the lower
        tail. A drawdown inside the distribution is information already held, and
        acting on it discards a working strategy.
        """
        return drawdown >= self.quantile(1.0 - q)


def optimal_block_length(returns: np.ndarray) -> float:
    """Expected block length for the stationary bootstrap.

    n^(1/3) is the standard rule of thumb. Longer blocks preserve more
    dependence at the cost of variety; shorter ones approach an iid bootstrap and
    lose the dependence that made the correction necessary.
    """
    return max(2.0, float(len(returns)) ** (1.0 / 3.0))


def stationary_bootstrap(
    returns: np.ndarray, n_samples: int = 2000, block_length: float | None = None,
    seed: int = 20260101,
) -> np.ndarray:
    """Resample with geometric blocks, preserving serial dependence.

    Returns a matrix of (n_samples x len(returns)) resampled series.
    """
    returns = np.asarray(returns, dtype=np.float64)
    n = returns.size
    if n < 3:
        raise ValueError("need at least three observations")
    expected = block_length or optimal_block_length(returns)
    p = 1.0 / expected
    rng = np.random.default_rng(seed)

    # Wrap indices so blocks near the end stay whole rather than truncating,
    # which would under-weight the tail of the sample.
    starts = rng.integers(0, n, size=(n_samples, n))
    continues = rng.random((n_samples, n)) > p
    idx = np.empty((n_samples, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, n):
        idx[:, t] = np.where(continues[:, t], (idx[:, t - 1] + 1) % n, starts[:, t])
    return returns[idx]


def sharpe_interval(
    returns: np.ndarray, confidence: float = 0.95, n_samples: int = 2000,
    seed: int = 20260101,
) -> Interval:
    """Confidence interval on the Sharpe ratio, dependence preserved."""
    samples = stationary_bootstrap(returns, n_samples, seed=seed)
    means = samples.mean(axis=1)
    stds = samples.std(axis=1, ddof=1)
    sharpes = np.divide(means, stds, out=np.zeros_like(means), where=stds > 0)
    alpha = (1.0 - confidence) / 2.0
    observed_std = np.std(returns, ddof=1)
    return Interval(
        point=float(np.mean(returns) / observed_std) if observed_std > 0 else 0.0,
        low=float(np.quantile(sharpes, alpha)),
        high=float(np.quantile(sharpes, 1.0 - alpha)),
        confidence=confidence,
    )


def _max_drawdown(trade_returns: np.ndarray) -> float:
    equity = np.cumprod(1.0 + trade_returns)
    return float((equity / np.maximum.accumulate(equity) - 1.0).min())


def drawdown_distribution(
    trade_returns: np.ndarray, n_samples: int = 5000, seed: int = 20260101,
) -> DrawdownDistribution:
    """Max drawdown across reorderings of the same trades.

    The observed drawdown is one draw from this. Reporting it as the strategy's
    drawdown is reporting the sample as the population, and it is usually
    optimistic: history happened to arrange the losses more kindly than most
    orderings would.
    """
    trade_returns = np.asarray(trade_returns, dtype=np.float64)
    if trade_returns.size < 2:
        raise ValueError("need at least two trades")
    rng = np.random.default_rng(seed)
    observed = _max_drawdown(trade_returns)

    samples = np.empty(n_samples)
    for i in range(n_samples):
        samples[i] = _max_drawdown(rng.permutation(trade_returns))

    return DrawdownDistribution(
        observed=observed,
        samples=samples,
        percentile_of_observed=float((samples <= observed).mean()),
    )


def perturb_prices(
    close: np.ndarray, half_spread: float = 5e-5, n_samples: int = 200,
    seed: int = 20260101,
) -> np.ndarray:
    """Jitter prices within the spread.

    A strategy whose result moves materially when each price shifts by a fraction
    of the bid-ask spread is fitted to the exact prints, not to the market.
    """
    rng = np.random.default_rng(seed)
    noise = rng.uniform(-half_spread, half_spread, size=(n_samples, close.size))
    return close[None, :] * (1.0 + noise)
