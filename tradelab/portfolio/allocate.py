"""Clustering and allocation across strategies. Plan.md §13.

No mean-variance (unstable under estimated returns). Trials count at portfolio level too
(§13.3, §8.3).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


@dataclass(frozen=True, slots=True)
class Clustering:
    labels: dict[str, int]
    n_obs: int
    #: Correlations rise under stress; risk uses the crisis-regime figure (§13.1).
    regime: str = "all"

    @property
    def reliable(self) -> bool:
        """Enough observations for the correlation to mean anything (§13.1)."""
        return self.n_obs >= 60

    def clusters(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for name, label in self.labels.items():
            out.setdefault(label, []).append(name)
        return {k: sorted(v) for k, v in sorted(out.items())}


def cluster_strategies(
    returns: dict[str, np.ndarray], threshold: float = 0.5, regime: str = "all",
) -> Clustering:
    """Hierarchical clustering on correlation distance of realised returns (§13.1).

    A cluster is one bet; never cluster on parameters, features or asset overlap.
    """
    names = sorted(returns)
    if len(names) < 2:
        return Clustering({n: 0 for n in names}, len(next(iter(returns.values()), [])), regime)

    matrix = np.array([returns[n] for n in names])
    corr = np.corrcoef(matrix)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)
    distance = np.sqrt(np.clip(0.5 * (1 - corr), 0, None))
    np.fill_diagonal(distance, 0.0)
    # squareform rejects float-noise asymmetry from corrcoef.
    distance = (distance + distance.T) / 2

    labels = fcluster(linkage(squareform(distance, checks=False), method="average"),
                      t=threshold, criterion="distance")
    return Clustering(
        {name: int(label) for name, label in zip(names, labels, strict=True)},
        n_obs=matrix.shape[1], regime=regime,
    )


def inverse_variance_weights(returns: dict[str, np.ndarray]) -> dict[str, float]:
    variances = {n: float(np.var(r, ddof=1)) for n, r in returns.items()}
    inverse = {n: (1.0 / v if v > 0 else 0.0) for n, v in variances.items()}
    total = sum(inverse.values())
    if total == 0:
        return dict.fromkeys(returns, 1.0 / len(returns))
    return {n: w / total for n, w in inverse.items()}


def allocate(
    returns: dict[str, np.ndarray], clustering: Clustering,
    max_per_strategy: float = 0.4, max_per_cluster: float = 0.6,
) -> dict[str, float]:
    """Capital across clusters first, then within them (§13.2); caps applied afterwards."""
    clusters = clustering.clusters()
    if not clusters:
        return {}

    # Between clusters: inverse variance of each cluster's equal-weight return.
    cluster_returns = {
        label: np.mean([returns[n] for n in members], axis=0)
        for label, members in clusters.items()
    }
    cluster_weights = inverse_variance_weights(cluster_returns)

    weights: dict[str, float] = {}
    for label, members in clusters.items():
        within = inverse_variance_weights({n: returns[n] for n in members})
        for name in members:
            weights[name] = cluster_weights[label] * within[name]

    return _apply_caps(weights, clusters, max_per_strategy, max_per_cluster)


def _apply_caps(
    weights: dict[str, float], clusters: dict[int, list[str]],
    max_per_strategy: float, max_per_cluster: float,
) -> dict[str, float]:
    """Cap, then redistribute freed mass to the uncapped; loop as that can breach new caps.

    Never renormalise after capping (it undoes the cap). A sum below one is cash, not a bug.
    """
    total = sum(weights.values())
    capped = {n: w / total for n, w in weights.items()} if total else dict(weights)

    fixed: set[str] = set()
    for _ in range(len(capped) + 1):
        over = [n for n, w in capped.items() if w > max_per_strategy + 1e-12
                and n not in fixed]
        if not over:
            break
        freed = sum(capped[n] - max_per_strategy for n in over)
        for name in over:
            capped[name] = max_per_strategy
            fixed.add(name)
        movable = {n: w for n, w in capped.items() if n not in fixed}
        headroom = sum(movable.values())
        if not movable or headroom <= 0:
            break  # nothing left to receive it; the remainder stays unallocated
        for name, weight in movable.items():
            capped[name] = weight + freed * weight / headroom

    # Cluster caps last, scaling down without redistributing the excess.
    for members in clusters.values():
        cluster_total = sum(capped[n] for n in members)
        if cluster_total > max_per_cluster:
            scale = max_per_cluster / cluster_total
            for name in members:
                capped[name] *= scale
    return capped


@dataclass(frozen=True, slots=True)
class PortfolioTrials:
    """§13.3: every promotion is a trial at the portfolio level."""

    promotions: int
    performance_driven_reallocations: int

    @property
    def total(self) -> int:
        return self.promotions + self.performance_driven_reallocations

    def deflated_sharpe(self, portfolio_sharpe: float, n_obs: int) -> float:
        """Portfolio Sharpe deflated by the portfolio's own trial count."""
        from tradelab.validation.deflated import SharpeStats, deflated_sharpe

        stats = SharpeStats(sharpe=portfolio_sharpe, n_obs=n_obs, skew=0.0, kurtosis=3.0)
        return deflated_sharpe(stats, trials=max(self.total, 1))


def joint_capacity(individual: dict[str, float], overlap: dict[str, float]) -> float:
    """Portfolio capacity, not the sum of the parts (§13.5).

    `overlap`: each strategy's share of flow in contested names; that part is not additive.
    """
    if not individual:
        return 0.0
    uncontested = sum(c * (1 - overlap.get(n, 0.0)) for n, c in individual.items())
    contested = min(
        (c for n, c in individual.items() if overlap.get(n, 0.0) > 0), default=0.0
    )
    return uncontested + contested
