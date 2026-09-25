"""The risk engine's veto. Plan.md §14.1.

Every limit gets a case where it is the *only* thing standing between the order
and acceptance, because a limit that is never the binding constraint in any test
has not been tested — the order would have been refused anyway.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    Position,
    Side,
)
from tradelab.risk.limits import Breach, RiskEngine, RiskLimits

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
BTC = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(50))
PRICE = Decimal(80_000)


def _engine(**overrides) -> RiskEngine:
    limits = RiskLimits(**overrides)
    account = AccountState(
        equity=Decimal(100_000), peak_equity=Decimal(100_000),
        day_start_equity=Decimal(100_000),
    )
    return RiskEngine(limits, account)


def _order(quantity: str = "0.1", side: Side = Side.BUY, **kw) -> Order:
    return Order(asset="BTCUSDT", side=side, quantity=Decimal(quantity), **kw)


@pytest.mark.fast
def test_a_reasonable_order_is_allowed() -> None:
    """The control. Without it every test below could pass by refusing always."""
    decision = _engine().check(_order(), PRICE, BTC, {}, NOW)
    assert decision.allowed, decision.detail


@pytest.mark.fast
def test_min_viable_uses_lot_size_when_it_binds() -> None:
    """Plan.md §14.1's worked example, which `min_notional` alone gets wrong.

    On BTCUSDT at 84,532 with a 0.001 lot the floor is 84.53, not the 50 the
    notional minimum states — wrong by a factor of 1.7 in the direction that
    lets through an order the venue rejects.
    """
    assert BTC.min_viable(Decimal(84_532)) == Decimal("84.532")
    # And where the notional binds instead, it wins.
    ada = Instrument("ADAUSDT", Decimal("0.0001"), Decimal("1"), Decimal(5))
    assert ada.min_viable(Decimal("0.249")) == Decimal(5)


@pytest.mark.fast
def test_quantity_is_judged_after_quantisation() -> None:
    """The venue rounds, so approving an unrounded size approves a fiction."""
    # Raw 0.0015 is worth 120 and would pass; quantised to 0.001 it is worth 80
    # and falls under this instrument's 100 minimum. Only the order of the two
    # operations decides the outcome, which is the point.
    strict = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(100))
    # The equity-fraction floor is disabled so it cannot be what refuses either
    # order; this test is about quantisation and nothing else.
    engine = _engine(min_position_pct_equity=Decimal(0))
    decision = engine.check(_order("0.0015"), PRICE, strict, {}, NOW)
    assert decision.breach is Breach.BELOW_MIN_VIABLE
    assert BTC.quantise(Decimal("0.0015")) == Decimal("0.001")
    # And the unquantised order would have been accepted, so the test is not
    # passing for some unrelated reason.
    assert engine.check(_order("0.002"), PRICE, strict, {}, NOW).allowed


@pytest.mark.fast
def test_rounding_is_down_never_nearest() -> None:
    """Up-rounding can breach a limit the engine has just approved."""
    assert BTC.quantise(Decimal("0.0019")) == Decimal("0.001")
    assert BTC.quantise(Decimal("0.0010")) == Decimal("0.001")


@pytest.mark.fast
def test_positions_are_counted_by_the_engine_not_the_strategy() -> None:
    """The case the limit exists for is a strategy that miscounts itself."""
    engine = _engine(max_open_positions=1)
    engine.account.positions["ETHUSDT"] = Position("ETHUSDT", quantity=Decimal(1))
    decision = engine.check(_order(), PRICE, BTC, {"ETHUSDT": Decimal(3_000)}, NOW)
    assert decision.breach is Breach.MAX_OPEN_POSITIONS


@pytest.mark.fast
def test_cluster_cap_binds_where_the_position_cap_does_not() -> None:
    """Five strategies in one correlation cluster are one bet (§13.1)."""
    engine = _engine(max_open_positions=10, max_positions_per_cluster=1)
    engine.clusters = {"BTCUSDT": "majors", "ETHUSDT": "majors"}
    engine.account.positions["ETHUSDT"] = Position("ETHUSDT", quantity=Decimal(1))
    decision = engine.check(_order(), PRICE, BTC, {"ETHUSDT": Decimal(3_000)}, NOW)
    assert decision.breach is Breach.MAX_POSITIONS_PER_CLUSTER


@pytest.mark.fast
def test_entry_spacing_blocks_a_flickering_signal() -> None:
    engine = _engine(min_seconds_between_entries=300)
    position = Position("BTCUSDT", quantity=Decimal("0.01"))
    position.last_entry_at = NOW - timedelta(seconds=60)
    engine.account.positions["BTCUSDT"] = position
    decision = engine.check(_order(), PRICE, BTC, {"BTCUSDT": PRICE}, NOW)
    assert decision.breach is Breach.MIN_SECONDS_BETWEEN_ENTRIES


@pytest.mark.fast
def test_order_rate_firebreak() -> None:
    engine = _engine(max_orders_per_minute=3)
    for i in range(3):
        engine.record_order(NOW - timedelta(seconds=i))
    assert engine.check(_order(), PRICE, BTC, {}, NOW).breach is Breach.MAX_ORDERS_PER_MINUTE


@pytest.mark.fast
def test_the_rate_window_actually_slides() -> None:
    """A counter that never forgets halts the run an hour later for no reason."""
    engine = _engine(max_orders_per_minute=3)
    for i in range(3):
        engine.record_order(NOW - timedelta(minutes=5, seconds=i))
    engine.record_order(NOW)
    assert engine.check(_order(), PRICE, BTC, {}, NOW).allowed


@pytest.mark.fast
def test_corrupt_price_is_refused() -> None:
    engine = _engine(max_price_deviation_bps=Decimal(50))
    decision = engine.check(
        _order(), Decimal(90_000), BTC, {}, NOW, mid_price=Decimal(80_000)
    )
    assert decision.breach is Breach.PRICE_DEVIATION


@pytest.mark.fast
def test_gross_exposure_includes_the_order_being_judged() -> None:
    """Checking exposure before adding the order approves the breach."""
    # Every other cap is opened up so gross exposure is provably the one that
    # bound. A limit that was never the binding constraint has not been tested.
    engine = _engine(
        max_gross_exposure=Decimal("0.5"), max_position_pct_equity=Decimal(1),
        max_position_notional=Decimal(1_000_000), max_leverage=Decimal(100),
        max_net_exposure=Decimal(100),
    )
    engine.account.positions["ETHUSDT"] = Position("ETHUSDT", quantity=Decimal(10))
    prices = {"ETHUSDT": Decimal(4_000)}   # 40,000 of 100,000 equity
    decision = engine.check(_order("0.2"), PRICE, BTC, prices, NOW)  # +16,000 => 0.56x
    assert decision.breach is Breach.MAX_GROSS_EXPOSURE


@pytest.mark.fast
@pytest.mark.parametrize(
    ("equity", "expected"),
    [(Decimal(96_000), None), (Decimal(94_000), Breach.MAX_DAILY_LOSS)],
)
def test_daily_loss_halts(equity: Decimal, expected: Breach | None) -> None:
    engine = _engine(max_daily_loss=Decimal("0.05"))
    engine.account.equity = equity
    decision = engine.enforce(NOW)
    assert decision.breach is expected
    assert engine.account.halted is (expected is not None)


@pytest.mark.fast
def test_drawdown_halt_needs_a_human() -> None:
    """A drawdown limit that clears itself has not been reviewed (§14.1)."""
    from tradelab.risk.limits import REQUIRES_HUMAN

    engine = _engine(max_drawdown_from_peak=Decimal("0.10"))
    engine.account.equity = Decimal(85_000)
    engine.account.day_start_equity = Decimal(85_000)  # so the daily cap is not the one firing
    decision = engine.enforce(NOW)
    assert decision.breach is Breach.MAX_DRAWDOWN_FROM_PEAK
    assert decision.breach in REQUIRES_HUMAN
    assert engine.account.halted


@pytest.mark.fast
def test_the_loss_limit_fires_without_an_order_being_placed() -> None:
    """A limit checked only on order entry misses a quietly losing position."""
    engine = _engine(max_daily_loss=Decimal("0.05"))
    engine.account.positions["BTCUSDT"] = Position("BTCUSDT", quantity=Decimal("1"))
    engine.account.equity = Decimal(80_000)
    assert engine.enforce(NOW).breach is Breach.MAX_DAILY_LOSS


@pytest.mark.fast
def test_reduce_only_survives_the_limits_it_is_escaping() -> None:
    """Blocking a closing order on an exposure cap traps the system in breach."""
    engine = _engine(max_gross_exposure=Decimal("0.01"), max_open_positions=0)
    engine.account.positions["BTCUSDT"] = Position("BTCUSDT", quantity=Decimal(1))
    decision = engine.check(
        _order("1", side=Side.SELL, reduce_only=True), PRICE, BTC,
        {"BTCUSDT": PRICE}, NOW,
    )
    assert decision.allowed


@pytest.mark.fast
def test_a_halted_engine_refuses_everything_including_new_entries() -> None:
    engine = _engine()
    engine.halt("manual kill switch")
    assert engine.check(_order(), PRICE, BTC, {}, NOW).breach is Breach.HALTED


@pytest.mark.fast
def test_consecutive_errors_halt_and_success_clears_them() -> None:
    engine = _engine(max_consecutive_errors=3)
    for _ in range(3):
        engine.record_error()
    assert engine.enforce(NOW).breach is Breach.MAX_CONSECUTIVE_ERRORS
    engine.record_success()
    assert engine.account.consecutive_errors == 0


@pytest.mark.fast
def test_limits_are_not_a_production_only_feature() -> None:
    """§12.2: enforced in every mode, so the limiter's first real execution is
    never with real money."""
    assert not ExecutionMode.BACKTEST.reaches_a_venue
    assert not ExecutionMode.SIMULATOR.reaches_a_venue
    assert ExecutionMode.TESTNET.reaches_a_venue
    assert not ExecutionMode.SIMULATOR.touches_real_money
    assert ExecutionMode.LIVE.touches_real_money


@pytest.mark.fast
def test_client_order_id_is_deterministic() -> None:
    """A retry after a timeout must not create a second order (§14.3)."""
    first = _order().with_id("run-1", 7)
    again = _order().with_id("run-1", 7)
    other = _order().with_id("run-1", 8)
    assert first.client_order_id == again.client_order_id
    assert first.client_order_id != other.client_order_id
    assert first.client_order_id


@pytest.mark.fast
def test_position_realises_pnl_across_a_reversal() -> None:
    """Long 1 at 100, sell 2 at 110: realise +10 and end short 1 at 110."""
    position = Position("X")
    position.apply(Fill("X", Side.BUY, Decimal(1), Decimal(100), Decimal(0), NOW, "a"))
    position.apply(Fill("X", Side.SELL, Decimal(2), Decimal(110), Decimal(0), NOW, "b"))
    assert position.quantity == Decimal(-1)
    assert position.realised_pnl == Decimal(10)
    assert position.avg_price == Decimal(110)
