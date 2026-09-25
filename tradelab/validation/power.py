"""Statistical power, checked before a study runs. Plan.md §8.8.

§8.8 says the minimum detectable effect is fixed before searching. That was
implemented as a diagnosis afterwards: the gate reported "underpowered" once the
trials had already been spent and the counter had already moved. The counter is
monotonic, so a study that could never have concluded still raises the bar for
every study after it.

This makes it a precondition. Estimate the effective observations the design will
produce, compare against what the declared target needs, and refuse rather than
diagnose.

One rule governs the pilot used to size the study: **it reads trade counts and
never performance.** A pilot whose Sharpe is looked at is a trial, and would have
to be counted as one. A pilot that only measures how often the design trades
makes no selection, so it costs nothing against §8.3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq

from tradelab.validation.deflated import required_sharpe


class UnderpoweredStudy(RuntimeError):
    """Raised when a design cannot resolve the effect it is looking for."""


@dataclass(frozen=True, slots=True)
class PowerEstimate:
    target_sharpe_annual: float
    trials: int
    effective_n_available: float
    effective_n_needed: float
    resolvable_sharpe_annual: float

    @property
    def adequate(self) -> bool:
        return self.effective_n_available >= self.effective_n_needed

    @property
    def shortfall(self) -> float:
        return max(self.effective_n_needed / max(self.effective_n_available, 1e-9), 1.0)

    def describe(self) -> str:
        if self.adequate:
            return (
                f"adequate: {self.effective_n_available:,.0f} effective observations "
                f"against {self.effective_n_needed:,.0f} needed to resolve an annualised "
                f"Sharpe of {self.target_sharpe_annual:.2f} at {self.trials:,} trials"
            )
        return (
            f"underpowered by {self.shortfall:.1f}x: {self.effective_n_available:,.0f} "
            f"effective observations resolve only {self.resolvable_sharpe_annual:.2f}, "
            f"while the study targets {self.target_sharpe_annual:.2f} at "
            f"{self.trials:,} trials. Widen the universe or raise the trade "
            f"frequency; lowering the trial count alone will not close it."
        )


def effective_n_needed(
    target_sharpe_annual: float, trials: int, bars_per_year: float = 2_190,
    confidence: float = 0.95,
) -> float:
    """Observations required for `target_sharpe_annual` to clear the bar.

    Inverted numerically because `required_sharpe` has no closed-form inverse:
    the expected maximum of N draws involves the normal quantile twice.
    """
    annual = math.sqrt(bars_per_year)
    if target_sharpe_annual <= 0:
        return math.inf

    def gap(n: float) -> float:
        return required_sharpe(trials, int(n), confidence) * annual - target_sharpe_annual

    if gap(5e7) > 0:
        return math.inf
    lo = 20.0
    if gap(lo) < 0:
        return lo
    return float(brentq(gap, lo, 5e7, xtol=1.0))


def resolvable_sharpe(
    effective_n: float, trials: int, bars_per_year: float = 2_190,
    confidence: float = 0.95,
) -> float:
    """Smallest annualised Sharpe this design could distinguish from chance."""
    if effective_n < 20:
        return math.inf
    return required_sharpe(trials, int(effective_n), confidence) * math.sqrt(bars_per_year)


def assess(
    target_sharpe_annual: float, trials: int, effective_n_available: float,
    bars_per_year: float = 2_190,
) -> PowerEstimate:
    return PowerEstimate(
        target_sharpe_annual=target_sharpe_annual,
        trials=trials,
        effective_n_available=float(effective_n_available),
        effective_n_needed=effective_n_needed(target_sharpe_annual, trials, bars_per_year),
        resolvable_sharpe_annual=resolvable_sharpe(
            effective_n_available, trials, bars_per_year),
    )


def pilot_effective_n(
    close: np.ndarray, position: np.ndarray, oos_fraction: float = 0.3,
) -> float:
    """Effective observations a design will produce, from one evaluation.

    Reads the holding structure and nothing else. No return, Sharpe or profit is
    computed here, which is what keeps the pilot from being a trial.
    """
    split = int(len(close) * (1 - oos_fraction))
    held = position[split:]
    if held.size < 2:
        return 0.0
    entries = int(np.sum(np.diff(np.concatenate([[0.0], held])) > 0))
    flat_runs = int(np.sum(np.diff(np.concatenate([[0.0], (held == 0).astype(float)])) > 0))
    return float(max(entries + flat_runs, 0))


def require_power(
    target_sharpe_annual: float, trials: int, effective_n_available: float,
    bars_per_year: float = 2_190, strict: bool = True,
) -> PowerEstimate:
    """Refuse an underpowered design rather than discovering it afterwards."""
    estimate = assess(target_sharpe_annual, trials, effective_n_available, bars_per_year)
    if strict and not estimate.adequate:
        raise UnderpoweredStudy(estimate.describe())
    return estimate
