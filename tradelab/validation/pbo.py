"""Probability of Backtest Overfitting via CSCV. Plan.md §8.3.

Bailey, Borwein, López de Prado & Zhu. It asks a question no single backtest can:
**is the procedure that picked this configuration any better than picking at
random?**

The method splits the returns of every candidate into S blocks, forms every
balanced in-sample / out-of-sample partition, picks the in-sample winner in each,
and records where that winner ranks out-of-sample. PBO is how often it lands in
the bottom half. Above 0.5, the selection rule is worse than a coin toss, and the
reported winner is an artefact of the search.

**Feed it gross returns.** Measured on pure noise, PBO on NET returns came out at
0.000 — apparently perfect selection on data with nothing to select. The cause is
not a broken estimator: with heterogeneous turnover, net Sharpe correlates -0.95
with turnover, so the procedure selects the configuration that trades least, and
trading least is genuinely persistent out of sample. PBO faithfully reports that
persistence. On the same data with costs removed, PBO rises to 0.667.

So net-return PBO answers "is the selected thing persistent?", which turnover
satisfies without any alpha at all. Gross-return PBO answers the question
actually being asked. `turnover_correlation` is reported so the confusion is
visible rather than silent, and §8.4's turnover-matched random baseline is the
control that catches what PBO cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np


@dataclass(frozen=True, slots=True)
class PBOResult:
    pbo: float
    n_paths: int
    median_oos_rank: float
    logits: np.ndarray
    turnover_correlation: float | None = None

    @property
    def overfit(self) -> bool:
        return self.pbo > 0.5

    @property
    def measures_turnover_not_alpha(self) -> bool:
        """True when the ranking is dominated by how much each config trades.

        A low PBO here says nothing about alpha: it says the search found that
        trading less costs less, which was never in doubt.
        """
        return self.turnover_correlation is not None and self.turnover_correlation < -0.7


def _sharpe(block: np.ndarray) -> np.ndarray:
    std = block.std(axis=0, ddof=1)
    mean = block.mean(axis=0)
    return np.where(std > 0, mean / np.where(std > 0, std, 1.0), 0.0)


def probability_of_backtest_overfitting(
    returns: np.ndarray, n_blocks: int = 10, turnover: np.ndarray | None = None,
) -> PBOResult:
    """`returns` is (observations x configurations), preferably GROSS.

    Pass `turnover` per configuration to get the diagnostic that reveals when the
    result is about trading frequency rather than skill.
    """
    if returns.ndim != 2 or returns.shape[1] < 2:
        raise ValueError("PBO needs a matrix of at least two configurations")
    if n_blocks % 2:
        raise ValueError("n_blocks must be even so partitions are balanced")

    n_obs, n_config = returns.shape
    bounds = np.linspace(0, n_obs, n_blocks + 1).astype(int)
    blocks = [returns[bounds[i]:bounds[i + 1]] for i in range(n_blocks)]

    logits = []
    for chosen in combinations(range(n_blocks), n_blocks // 2):
        rest = [i for i in range(n_blocks) if i not in chosen]
        in_sample = np.vstack([blocks[i] for i in chosen])
        out_sample = np.vstack([blocks[i] for i in rest])

        best = int(np.argmax(_sharpe(in_sample)))
        oos = _sharpe(out_sample)
        # Relative rank of the in-sample winner among all configurations, OOS.
        rank = float((oos <= oos[best]).sum()) / n_config
        rank = min(max(rank, 1.0 / (n_config + 1)), 1.0 - 1.0 / (n_config + 1))
        logits.append(np.log(rank / (1.0 - rank)))

    logits_arr = np.asarray(logits)
    correlation = None
    if turnover is not None and np.std(turnover) > 0:
        full = _sharpe(returns)
        if np.std(full) > 0:
            correlation = float(np.corrcoef(np.asarray(turnover), full)[0, 1])

    return PBOResult(
        pbo=float((logits_arr <= 0).mean()),
        n_paths=logits_arr.size,
        median_oos_rank=float(np.median(1 / (1 + np.exp(-logits_arr)))),
        logits=logits_arr,
        turnover_correlation=correlation,
    )
