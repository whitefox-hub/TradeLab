"""Adapters, reconciliation, capital ramp and portfolio. Plan.md §12-14.

Phases 5-8 were built before any strategy passed the validation gate, at the
operator's direction. That ordering makes these tests the only thing standing
behind this code, since no campaign will exercise it for a while.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest

from tradelab.execution.adapters import (
    HaltedAdapter,
    SimulatedAdapter,
    closing_orders,
)
from tradelab.execution.reconcile import (
    Attribution,
    attribute,
    capital_for_tier,
    reconcile,
    sizing_is_a_control,
)
from tradelab.execution.types import (
    ExecutionMode,
    Instrument,
    Order,
    OrderStatus,
    Side,
)
from tradelab.portfolio.allocate import (
    PortfolioTrials,
    allocate,
    cluster_strategies,
    joint_capacity,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
BTC = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(50))


def _adapter() -> SimulatedAdapter:
    return SimulatedAdapter(
        instruments={"BTCUSDT": BTC}, prices={"BTCUSDT": Decimal(80_000)}
    )


@pytest.mark.fast
def test_the_simulator_always_crosses_the_spread_against_you() -> None:
    """A simulator that fills at mid reports an edge the venue will not pay."""
    adapter = _adapter()
    adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 0), NOW)
    adapter.submit(Order("BTCUSDT", Side.SELL, Decimal("0.01")).with_id("r", 1), NOW)
    buy, sell = adapter.fills
    assert buy.price > Decimal(80_000), "a buy filled at or below mid"
    assert sell.price < Decimal(80_000), "a sell filled at or above mid"


@pytest.mark.fast
def test_the_simulator_enforces_rounding_it_can_know_about() -> None:
    """Tick and lot rounding are knowable offline, so not modelling them would
    report fills at sizes no venue accepts (§12.1)."""
    adapter = _adapter()
    ack = adapter.submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.0005")).with_id("r", 0), NOW
    )
    assert ack.status is OrderStatus.REJECTED
    assert "min_viable" in ack.reason or "zero" in ack.reason


@pytest.mark.fast
def test_the_kill_switch_still_lets_positions_close() -> None:
    """Halting by refusing everything traps the system holding its positions."""
    halted = HaltedAdapter(_adapter(), reason="manual")
    opening = Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 0)
    closing = Order("BTCUSDT", Side.SELL, Decimal("0.01"), reduce_only=True).with_id("r", 1)
    assert halted.submit(opening, NOW).status is OrderStatus.REJECTED
    assert halted.submit(closing, NOW).status is OrderStatus.FILLED


@pytest.mark.fast
def test_closing_orders_are_built_from_the_venue_view() -> None:
    """The kill switch runs exactly when our view and the venue's have diverged."""
    orders = closing_orders({"BTCUSDT": Decimal("0.5"), "ETHUSDT": Decimal(-2)})
    by_asset = {o.asset: o for o in orders}
    assert by_asset["BTCUSDT"].side is Side.SELL
    assert by_asset["ETHUSDT"].side is Side.BUY
    assert all(o.reduce_only for o in orders)
    assert all(o.client_order_id for o in orders)


@pytest.mark.fast
def test_only_testnet_and_live_reach_a_matching_engine() -> None:
    assert not ExecutionMode.SIMULATOR.reaches_a_venue
    assert ExecutionMode.TESTNET.reaches_a_venue
    assert not ExecutionMode.TESTNET.touches_real_money


@pytest.mark.fast
def test_identical_series_do_not_diverge() -> None:
    rng = np.random.default_rng(3)
    returns = rng.normal(0, 0.01, 250)
    result = reconcile(returns, returns.copy(), Attribution(0, 0, 0, 0), n_samples=300)
    assert not result.significant
    assert result.mean_divergence == pytest.approx(0.0, abs=1e-12)
    assert result.verdict == "within band"


@pytest.mark.fast
def test_a_real_divergence_is_detected_and_named() -> None:
    rng = np.random.default_rng(4)
    backtest = rng.normal(0.0005, 0.01, 500)
    simulator = backtest - 0.0008
    attribution = Attribution(costs=-0.0007, timing=-0.00005, fills=-0.00004, data=-0.00001)
    result = reconcile(backtest, simulator, attribution, n_samples=500)
    assert result.significant
    assert "costs" in result.verdict


@pytest.mark.fast
def test_offsetting_causes_are_not_reported_as_a_small_problem() -> None:
    """Two large opposite causes net to zero; signed shares would hide both."""
    attribution = Attribution(costs=+0.01, timing=-0.01, fills=0.0, data=0.0)
    assert attribution.total == pytest.approx(0.0)
    shares = attribution.shares()
    assert shares["costs"] == pytest.approx(0.5)
    assert shares["timing"] == pytest.approx(0.5)


@pytest.mark.fast
def test_reconciliation_refuses_an_unpaired_comparison() -> None:
    with pytest.raises(ValueError, match="equal lengths"):
        reconcile(np.zeros(10), np.zeros(9), Attribution(0, 0, 0, 0))


@pytest.mark.fast
def test_attribution_signs_point_the_right_way() -> None:
    """Positive means the simulator did better than the backtest expected."""
    zero = np.zeros(5)
    worse_fills = attribute(
        expected_costs=zero, actual_costs=zero,
        expected_fill=np.full(5, 100.0), actual_fill=np.full(5, 99.0),
        expected_signal_return=zero, actual_signal_return=zero,
        expected_data=zero, actual_data=zero,
    )
    assert worse_fills.fills < 0


@pytest.mark.fast
def test_l0_is_floored_to_something_you_can_actually_stand_on() -> None:
    """§14.5 set L0 at 1% of target; for a 5,000 target that is 50, and BTC
    cannot be opened at all at that size."""
    plan = capital_for_tier("L0", Decimal(5_000), 3, Decimal("84.53"))
    assert plan.capital == Decimal("253.59")
    assert plan.floored and plan.deployable


@pytest.mark.fast
def test_an_undeployable_strategy_says_so_before_the_ramp_starts() -> None:
    plan = capital_for_tier("L0", Decimal(200), 3, Decimal("84.53"))
    assert not plan.deployable
    assert "cannot hold" in plan.note


@pytest.mark.fast
def test_no_tier_is_skipped_and_each_has_a_minimum_duration() -> None:
    from tradelab.execution.reconcile import TIERS

    assert [name for name, _, _ in TIERS] == ["L0", "L1", "L2", "L3"]
    fractions = [f for _, f, _ in TIERS]
    assert fractions == sorted(fractions), "tiers must increase"
    assert all(days > 0 for _, _, days in TIERS[:-1])


@pytest.mark.fast
def test_sizing_stops_being_a_control_at_small_capital() -> None:
    """Below ~20 increments the realised position differs from the intended one
    by enough that realised risk is a rounding artefact (§14.1)."""
    assert not sizing_is_a_control(Decimal(1_000), Decimal("84.53"))
    assert sizing_is_a_control(Decimal(5_000), Decimal("84.53"))


@pytest.mark.fast
def test_correlated_strategies_land_in_one_cluster() -> None:
    """A cluster is one bet, however many strategies are in it (§13.1)."""
    rng = np.random.default_rng(7)
    base = rng.normal(0, 0.01, 400)
    returns = {
        "a": base + rng.normal(0, 0.001, 400),
        "b": base + rng.normal(0, 0.001, 400),
        "c": rng.normal(0, 0.01, 400),
    }
    clustering = cluster_strategies(returns)
    assert clustering.labels["a"] == clustering.labels["b"]
    assert clustering.labels["c"] != clustering.labels["a"]
    assert clustering.reliable


@pytest.mark.fast
def test_a_correlation_on_too_few_observations_is_flagged_as_noise() -> None:
    rng = np.random.default_rng(8)
    returns = {"a": rng.normal(0, 0.01, 20), "b": rng.normal(0, 0.01, 20)}
    assert not cluster_strategies(returns).reliable


@pytest.mark.fast
def test_a_crowded_cluster_does_not_get_a_share_per_member() -> None:
    """Three correlated strategies are one bet and must not draw three shares."""
    rng = np.random.default_rng(9)
    base = rng.normal(0, 0.01, 400)
    returns = {
        "a": base + rng.normal(0, 0.0005, 400),
        "b": base + rng.normal(0, 0.0005, 400),
        "c": base + rng.normal(0, 0.0005, 400),
        "lone": rng.normal(0, 0.01, 400),
    }
    clustering = cluster_strategies(returns)
    weights = allocate(returns, clustering)
    crowded = weights["a"] + weights["b"] + weights["c"]
    assert weights["lone"] > max(weights["a"], weights["b"], weights["c"])
    assert crowded == pytest.approx(1 - weights["lone"], abs=1e-9)


@pytest.mark.fast
def test_caps_bind_regardless_of_what_the_allocator_wants() -> None:
    rng = np.random.default_rng(11)
    returns = {"calm": rng.normal(0, 0.0001, 300), "wild": rng.normal(0, 0.05, 300)}
    weights = allocate(returns, cluster_strategies(returns), max_per_strategy=0.6)
    assert weights["calm"] == pytest.approx(0.6)
    assert sum(weights.values()) <= 1.0 + 1e-9


@pytest.mark.fast
def test_portfolio_sharpe_is_deflated_by_portfolio_trials() -> None:
    """§13.3: a marginal strategy clears a higher bar than the first, not lower."""
    few = PortfolioTrials(promotions=1, performance_driven_reallocations=0)
    many = PortfolioTrials(promotions=20, performance_driven_reallocations=15)
    assert many.total == 35
    assert many.deflated_sharpe(0.12, 500) < few.deflated_sharpe(0.12, 500)


@pytest.mark.fast
def test_capacity_does_not_add_up_across_contested_names() -> None:
    """Two strategies in the same asset draw on the same liquidity (§13.5)."""
    individual = {"a": 100_000.0, "b": 100_000.0}
    assert joint_capacity(individual, overlap={}) == pytest.approx(200_000.0)
    contested = joint_capacity(individual, overlap={"a": 1.0, "b": 1.0})
    assert contested < 200_000.0
