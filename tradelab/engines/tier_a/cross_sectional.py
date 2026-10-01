"""Cross-sectional ranking. Plan.md §6.3.

Long-short by default (long-only top-k mostly measures crypto beta). The universe is
point-in-time: unlisted assets (NaN) are excluded, not ranked low. `rebalance` is declared,
not tuned.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tradelab.engines.tier_a.costs import CostModel


@dataclass(frozen=True, slots=True)
class CrossSectionalResult:
    returns: np.ndarray
    gross_returns: np.ndarray
    turnover: float
    rebalances: int
    avg_names: float
    effective_n: float

    @property
    def sharpe(self) -> float:
        std = self.returns.std(ddof=1)
        return float(self.returns.mean() / std) if std > 0 else 0.0

    @property
    def gross_sharpe(self) -> float:
        std = self.gross_returns.std(ddof=1)
        return float(self.gross_returns.mean() / std) if std > 0 else 0.0


def rank_positions(
    signals: np.ndarray, k: int, long_short: bool = True, rebalance: int = 1,
) -> np.ndarray:
    """Target weights from a (bars x assets) signal matrix.

    NaN = asset absent at that bar; excluded, not ranked last (§4.4).
    """
    n_bars, n_assets = signals.shape
    weights = np.zeros((n_bars, n_assets))

    for t in range(0, n_bars, rebalance):
        row = signals[t]
        present = np.flatnonzero(np.isfinite(row))
        needed = 2 * k if long_short else k
        if present.size < needed:
            continue
        order = present[np.argsort(row[present])]
        top = order[-k:]
        weights[t, top] = 1.0 / k
        if long_short:
            weights[t, order[:k]] = -1.0 / k

    held = np.maximum.accumulate(
        np.where(np.arange(n_bars) % rebalance == 0, np.arange(n_bars), 0)
    )
    return weights[held]


def run_cross_sectional(
    closes: np.ndarray, signals: np.ndarray, k: int = 5,
    costs: CostModel | None = None, long_short: bool = True,
    rebalance: int = 1, funding: np.ndarray | None = None,
) -> CrossSectionalResult:
    """Evaluate a ranking strategy over an aligned (bars x assets) panel."""
    from tradelab.engines.tier_a.costs import BINANCE_RETAIL

    costs = costs or BINANCE_RETAIL
    weights = rank_positions(signals, k, long_short, rebalance)

    lagged = np.vstack([np.zeros((1, weights.shape[1])), weights[:-1]])

    returns = np.zeros_like(closes)
    returns[1:] = (closes[1:] - closes[:-1]) / closes[:-1]
    returns = np.nan_to_num(returns)

    gross = np.nansum(lagged * returns, axis=1)
    traded = np.abs(np.diff(np.vstack([np.zeros((1, weights.shape[1])), lagged]), axis=0))
    cost = traded.sum(axis=1) * costs.per_side
    net = gross - cost
    if funding is not None:
        net = net - np.nansum(lagged * np.nan_to_num(funding), axis=1)

    rebalances = int(np.sum(traded.sum(axis=1) > 0))
    return CrossSectionalResult(
        returns=net, gross_returns=gross, turnover=float(traded.sum()),
        rebalances=rebalances,
        avg_names=float(np.mean((lagged != 0).sum(axis=1))),
        effective_n=float(max(rebalances, 1)),
    )
