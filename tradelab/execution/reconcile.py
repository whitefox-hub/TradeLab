"""Backtest ↔ simulator reconciliation. Plan.md §12.5.

Knowing *that* two return series diverge is useless. The decomposition is the
part that pays: 80% of the gap coming from worse-than-modelled fills tells you
what to fix, and a large divergence is evidence the backtest was wrong rather
than evidence the simulator is broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from tradelab.validation.robustness import sharpe_interval


@dataclass(frozen=True, slots=True)
class Attribution:
    """Where the divergence came from, in return units per period."""

    costs: float
    timing: float
    fills: float
    data: float

    @property
    def total(self) -> float:
        return self.costs + self.timing + self.fills + self.data

    def shares(self) -> dict[str, float]:
        """Fraction of the *absolute* divergence each bucket explains.

        Absolute, because two large offsetting causes are not a small problem
        and a signed share would report them as one.
        """
        magnitude = abs(self.costs) + abs(self.timing) + abs(self.fills) + abs(self.data)
        if magnitude == 0:
            return dict.fromkeys(("costs", "timing", "fills", "data"), 0.0)
        return {
            "costs": abs(self.costs) / magnitude,
            "timing": abs(self.timing) / magnitude,
            "fills": abs(self.fills) / magnitude,
            "data": abs(self.data) / magnitude,
        }


@dataclass(frozen=True, slots=True)
class Reconciliation:
    n_periods: int
    mean_divergence: float
    ci_low: float
    ci_high: float
    significant: bool
    attribution: Attribution

    @property
    def verdict(self) -> str:
        if not self.significant:
            return "within band"
        dominant = max(self.attribution.shares().items(), key=lambda kv: kv[1])
        return f"divergent, {dominant[1]:.0%} attributable to {dominant[0]}"


def attribute(
    expected_costs: np.ndarray, actual_costs: np.ndarray,
    expected_fill: np.ndarray, actual_fill: np.ndarray,
    expected_signal_return: np.ndarray, actual_signal_return: np.ndarray,
    expected_data: np.ndarray, actual_data: np.ndarray,
) -> Attribution:
    """Split the gap into the four buckets §12.5 names.

    Each is the mean of (actual minus expected) for one component, signed so that a
    positive number means the simulator did *better* than the backtest expected.
    """
    return Attribution(
        costs=float(np.mean(expected_costs - actual_costs)),
        fills=float(np.mean(actual_fill - expected_fill)),
        timing=float(np.mean(actual_signal_return - expected_signal_return)),
        data=float(np.mean(actual_data - expected_data)),
    )


def reconcile(
    backtest_returns: np.ndarray, simulator_returns: np.ndarray,
    attribution: Attribution, n_samples: int = 2_000,
) -> Reconciliation:
    """Paired comparison with a bootstrap interval on the difference.

    Paired, not two independent samples: both series are driven by the same
    market over the same period, so their common movement is not the question.
    The difference series is, and treating them as independent would widen the
    interval until nothing was ever significant.
    """
    if backtest_returns.shape != simulator_returns.shape:
        raise ValueError(
            f"paired comparison needs equal lengths, got "
            f"{backtest_returns.shape} and {simulator_returns.shape}"
        )
    difference = simulator_returns - backtest_returns
    # An interval on the *Sharpe* of the difference series. Since Sharpe is
    # mean/std and the standard deviation is positive, excluding zero is the
    # same statement as the mean difference excluding zero — and it inherits the
    # stationary bootstrap, which preserves the serial dependence a daily
    # divergence series certainly has.
    interval = sharpe_interval(difference, n_samples=n_samples)
    return Reconciliation(
        n_periods=int(difference.size),
        mean_divergence=float(difference.mean()),
        ci_low=interval.low,
        ci_high=interval.high,
        significant=interval.excludes_zero,
        attribution=attribution,
    )


#: §14.5's ladder. Capital is a fraction of target, subject to the floor below.
TIERS: tuple[tuple[str, Decimal, int], ...] = (
    ("L0", Decimal("0.01"), 14),
    ("L1", Decimal("0.10"), 28),
    ("L2", Decimal("0.33"), 42),
    ("L3", Decimal("1.00"), 0),
)


@dataclass(frozen=True, slots=True)
class TierPlan:
    tier: str
    capital: Decimal
    minimum_days: int
    floored: bool
    deployable: bool
    note: str = ""


def capital_for_tier(
    tier: str, target_capital: Decimal, positions_target: int,
    max_min_viable: Decimal,
) -> TierPlan:
    """Capital at a tier, with §14.1's floor applied.

    §14.5 as originally written set L0 at 1% of target. For a 5,000 target that
    is 50, and BTC cannot be opened at all at that size — a tier you cannot
    stand on is not a tier. The floor is the capital needed to hold the intended
    number of positions at the largest asset's minimum viable size.

    Where the floor exceeds the target, the honest answer is that the strategy
    is not deployable at that capital, said before the ramp starts rather than
    after L0 fails to fill.
    """
    fraction, days = next(
        (f, d) for name, f, d in TIERS if name == tier
    )
    nominal = fraction * target_capital
    floor = Decimal(positions_target) * max_min_viable
    capital = max(nominal, floor)
    floored = capital > nominal

    if capital > target_capital:
        return TierPlan(
            tier, capital, days, floored, deployable=False,
            note=(
                f"floor of {floor} exceeds the {target_capital} target: this "
                f"strategy cannot hold {positions_target} positions at this capital"
            ),
        )
    return TierPlan(tier, capital, days, floored, deployable=True,
                    note="floored to the minimum viable position set" if floored else "")


def sizing_is_a_control(capital: Decimal, increment: Decimal, minimum: int = 20) -> bool:
    """Whether position sizing still does anything at this capital (§14.1).

    Positions quantise to `lot_size x price`. Wanting 150 of BTC when the
    increment is 84.53 yields 84.53 or 169.06 — realised risk differs from
    intended risk by up to 44%. Below roughly twenty increments of headroom,
    sizing has stopped being a control and become a rounding artefact.
    """
    if increment <= 0:
        return True
    return capital / increment >= minimum
