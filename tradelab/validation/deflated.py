"""Deflated Sharpe Ratio and Minimum Track Record Length. Plan.md §8.3.

Bailey & López de Prado. The idea the whole project turns on: the maximum of N
Sharpe estimates drawn from a null of zero skill is not zero — it grows like
sqrt(2 ln N). Reporting the best of a large search without subtracting that
expectation is reporting a number that noise produces reliably.

Which is why `trials` here must be the FULL count including everything discarded
in tier A screening (§9.1). Counting only the finalists is the most common way to
fool yourself, and it is an easy mistake to make honestly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


@dataclass(frozen=True, slots=True)
class SharpeStats:
    sharpe: float          # per-observation, not annualised
    skew: float
    kurtosis: float        # non-excess
    n_obs: int


def sharpe_stats(returns: np.ndarray, effective_n: float | None = None) -> SharpeStats:
    """Moments of a return series.

    `effective_n` overrides the raw count with the number of INDEPENDENT
    observations (§7). Pass it whenever the returns come from overlapping
    positions, which is almost always: a strategy holding for 240 bars produces
    240 serially dependent returns, and treating them as 240 observations
    overstates every significance figure derived from them — by a factor of 122
    in a measured case.
    """
    returns = np.asarray(returns, dtype=np.float64)
    returns = returns[np.isfinite(returns)]
    std = returns.std(ddof=1)
    if std == 0 or returns.size < 3:
        return SharpeStats(0.0, 0.0, 3.0, returns.size)
    centred = returns - returns.mean()
    n = round(effective_n) if effective_n is not None else int(returns.size)
    return SharpeStats(
        sharpe=float(returns.mean() / std),
        skew=float((centred**3).mean() / std**3),
        kurtosis=float((centred**4).mean() / std**4),
        n_obs=max(n, 3),
    )


def expected_max_sharpe(trials: int, sharpe_variance: float) -> float:
    """E[max Sharpe] across `trials` independent draws from a zero-skill null.

    This is the bar that a search of that size must clear to mean anything.
    """
    if trials <= 1:
        return 0.0
    gamma = EULER_MASCHERONI
    z1 = norm.ppf(1.0 - 1.0 / trials)
    z2 = norm.ppf(1.0 - 1.0 / (trials * math.e))
    return math.sqrt(max(sharpe_variance, 0.0)) * ((1 - gamma) * z1 + gamma * z2)


def deflated_sharpe(
    stats: SharpeStats, trials: int, sharpe_variance: float | None = None,
    benchmark: float | None = None,
) -> float:
    """Probability that the true Sharpe exceeds the benchmark.

    `benchmark` defaults to E[max Sharpe] under the null for this trial count —
    which is what makes it *deflated* rather than merely a t-test.
    """
    if stats.n_obs < 3:
        return 0.0
    if benchmark is None:
        variance = (
            sharpe_variance if sharpe_variance is not None
            else (1 + 0.5 * stats.sharpe**2) / max(stats.n_obs - 1, 1)
        )
        benchmark = expected_max_sharpe(trials, variance)

    denominator = 1.0 - stats.skew * stats.sharpe + 0.25 * (stats.kurtosis - 1.0) * stats.sharpe**2
    if denominator <= 0:
        return 0.0
    z = (stats.sharpe - benchmark) * math.sqrt(stats.n_obs - 1) / math.sqrt(denominator)
    return float(norm.cdf(z))


def min_track_record_length(
    stats: SharpeStats, trials: int = 1, confidence: float = 0.95,
    sharpe_variance: float | None = None,
) -> float:
    """Observations needed for this Sharpe to be significant at `confidence`.

    When this exceeds the history available, the honest verdict is
    `inconclusive` — never `pass`. A high Sharpe on a short sample is not
    evidence; it is a number the sample was too small to contradict.
    """
    variance = (
        sharpe_variance if sharpe_variance is not None
        else (1 + 0.5 * stats.sharpe**2) / max(stats.n_obs - 1, 1)
    )
    benchmark = expected_max_sharpe(trials, variance) if trials > 1 else 0.0
    edge = stats.sharpe - benchmark
    if edge <= 0:
        return math.inf
    denominator = 1.0 - stats.skew * stats.sharpe + 0.25 * (stats.kurtosis - 1.0) * stats.sharpe**2
    return 1.0 + max(denominator, 0.0) * (norm.ppf(confidence) / edge) ** 2


def required_sharpe(trials: int, n_obs: int, confidence: float = 0.95) -> float:
    """The Sharpe a search of `trials` must produce over `n_obs` to be credible.

    The number that makes the trial budget concrete: testing more hypotheses
    raises the bar the survivor has to clear, so a disciplined search is worth
    more than a large one.
    """
    variance = 1.0 / max(n_obs - 1, 1)
    return expected_max_sharpe(trials, variance) + norm.ppf(confidence) / math.sqrt(n_obs - 1)
