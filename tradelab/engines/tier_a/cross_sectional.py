"""Cross-sectional ranking. Plan.md §6.3.

A threshold asks whether a signal is high in absolute terms. A ranking asks
whether it is high *relative to the rest of the universe right now*, which is a
different hypothesis and often the better-posed one: a funding z-score of 1.5
means one thing when the whole market is calm and another when everything is at
2.

Three design points that decide whether the result means anything.

**Long-short, not long-only.** A long-only basket of the top-k crypto assets is
mostly long crypto, so its Sharpe measures beta to a market that rose. Shorting
the bottom-k removes that factor and leaves the cross-sectional signal, which is
the thing being tested. Both are available and the difference between them is
itself the finding.

**The universe is point-in-time.** An asset that had not listed yet cannot be
ranked, and treating its absence as a low score would manufacture a short. Ranks
are computed over whatever exists at that bar.

**Rebalancing costs.** Re-ranking every bar produces enormous turnover, and §0.1
already showed turnover is what kills a strategy at this horizon. The rebalance
interval is a declared parameter, and the honest comparison holds it fixed
against the threshold study rather than tuning it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tradelab.engines.tier_a.costs import CostModel


@dataclass(frozen=True, slots=True)
class CrossSectionalResult:
    returns: np.ndarray          # per-bar portfolio return, net
    gross_returns: np.ndarray
    turnover: float
    rebalances: int
    avg_names: float             # mean assets held per bar
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

    NaN marks an asset absent at that bar and is excluded from the ranking
    rather than ranked last — the point-in-time universe of §4.4 applied to a
    cross-section.
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

    # Hold between rebalances rather than reverting to flat.
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

    # The same one-bar rule as everywhere else: a ranking computed from bar t
    # takes effect at t+1 (§9.1).
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
        # Independent bets are rebalances, not bars: holding a basket for ten
        # bars gives ten dependent returns and one decision.
        effective_n=float(max(rebalances, 1)),
    )
