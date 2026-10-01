"""Backtest ↔ simulator reconciliation, with the gap decomposed by cause. Plan.md §12.5."""

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
        """Fraction of the absolute divergence each bucket explains (offsets must not cancel)."""
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
    """Split the gap into the four buckets §12.5 names. Positive: simulator did better."""
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
    """Paired comparison (same market, same period) with a bootstrap interval on the difference."""
    if backtest_returns.shape != simulator_returns.shape:
        raise ValueError(
            f"paired comparison needs equal lengths, got "
            f"{backtest_returns.shape} and {simulator_returns.shape}"
        )
    difference = simulator_returns - backtest_returns
    interval = sharpe_interval(difference, n_samples=n_samples)
    return Reconciliation(
        n_periods=int(difference.size),
        mean_divergence=float(difference.mean()),
        ci_low=interval.low,
        ci_high=interval.high,
        significant=interval.excludes_zero,
        attribution=attribution,
    )


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

    Floor = positions_target x the largest min viable size; above target, not deployable.
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

    Below ~`minimum` increments (`lot_size x price`), sizing is a rounding artefact.
    """
    if increment <= 0:
        return True
    return capital / increment >= minimum
