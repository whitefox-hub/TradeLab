"""The run loop, reconciliation and recovery. Plan.md §12.2, §14.2, §14.3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradelab.execution.adapters import Ack, SimulatedAdapter
from tradelab.execution.engine import ExecutionEngine, NotReconciled
from tradelab.execution.intent import IntentLog, IntentState
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Instrument,
    Order,
    OrderStatus,
    Position,
    Side,
)
from tradelab.risk.limits import Breach, RiskEngine, RiskLimits

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
BTC = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(50))
PRICE = Decimal(80_000)


def _engine(limits: RiskLimits | None = None, **account_kw) -> ExecutionEngine:
    account = AccountState(
        equity=Decimal(100_000), peak_equity=Decimal(100_000),
        day_start_equity=Decimal(100_000), **account_kw,
    )
    return ExecutionEngine(
        run_id="run-1", mode=ExecutionMode.SIMULATOR,
        adapter=SimulatedAdapter(instruments={"BTCUSDT": BTC},
                                 prices={"BTCUSDT": PRICE}),
        risk=RiskEngine(limits or RiskLimits(), account),
        instruments={"BTCUSDT": BTC},
    )


def _order(quantity: str = "0.01", side: Side = Side.BUY, **kw) -> Order:
    return Order("BTCUSDT", side, Decimal(quantity), **kw)


@pytest.mark.fast
def test_trading_before_reconciliation_is_an_error_not_a_warning() -> None:
    """A restart that trades without reconciling can double a position (§14.2)."""
    engine = _engine()
    with pytest.raises(NotReconciled, match=r"§14\.2"):
        engine.submit(_order(), PRICE, {}, NOW)


@pytest.mark.fast
def test_a_clean_reconciliation_opens_the_gate() -> None:
    engine = _engine()
    assert engine.reconcile(NOW) == []
    assert engine.submit(_order(), PRICE, {}, NOW).allowed


@pytest.mark.fast
def test_divergence_halts_and_is_never_silently_corrected() -> None:
    """Adopting the venue's number hides the bug that produced the gap, and the
    gap is the only evidence the bug exists."""
    engine = _engine()
    engine.account.positions["BTCUSDT"] = Position("BTCUSDT", quantity=Decimal("0.5"))
    divergences = engine.reconcile(NOW)

    assert len(divergences) == 1
    assert divergences[0].ours == Decimal("0.5")
    assert divergences[0].theirs == Decimal(0)
    assert engine.account.halted
    # Our view is left exactly as it was.
    assert engine.account.positions["BTCUSDT"].quantity == Decimal("0.5")


@pytest.mark.fast
def test_the_intent_is_written_before_the_venue_is_called() -> None:
    """If the process dies on the next line, startup must know to go looking."""
    engine = _engine()
    engine.reconcile(NOW)
    engine.submit(_order(), PRICE, {}, NOW)

    states = [i.state for i in engine.intents.records]
    assert states.index(IntentState.SENT) < states.index(IntentState.ACKNOWLEDGED)
    assert IntentState.WANTED in states


@pytest.mark.fast
def test_a_vetoed_order_is_logged_as_a_decision() -> None:
    """A log that keeps only what executed cannot explain a quiet day."""
    engine = _engine(RiskLimits(max_open_positions=0))
    engine.reconcile(NOW)
    decision = engine.submit(_order(), PRICE, {}, NOW)

    assert decision.breach is Breach.MAX_OPEN_POSITIONS
    vetoed = [i for i in engine.intents.records if i.state is IntentState.VETOED]
    assert len(vetoed) == 1
    assert "max_open_positions" in vetoed[0].reason
    # And nothing reached the venue.
    assert engine.adapter.fills == []


@pytest.mark.fast
def test_an_order_that_died_in_flight_is_unresolved_at_startup() -> None:
    """SENT with no later record is exactly the crash §14.3 describes."""
    log = IntentLog()
    engine = _engine()
    engine.intents = log
    engine.reconcile(NOW)

    class DiesOnSubmit(SimulatedAdapter):
        def submit(self, order, at):
            raise ConnectionError("socket closed after send")

    engine.adapter = DiesOnSubmit(instruments={"BTCUSDT": BTC}, prices={"BTCUSDT": PRICE})
    engine.submit(_order(), PRICE, {}, NOW)

    unresolved = log.unresolved()
    assert len(unresolved) == 1
    assert unresolved[0].state is IntentState.UNKNOWN


@pytest.mark.fast
def test_the_engine_survives_a_venue_exception_and_counts_it() -> None:
    engine = _engine(RiskLimits(max_consecutive_errors=2))
    engine.reconcile(NOW)

    class Broken(SimulatedAdapter):
        def submit(self, order, at):
            raise TimeoutError("no response")

    engine.adapter = Broken(instruments={"BTCUSDT": BTC}, prices={"BTCUSDT": PRICE})
    engine.submit(_order(), PRICE, {}, NOW)
    assert engine.account.consecutive_errors == 1
    assert not engine.account.halted

    engine.submit(_order(), PRICE, {}, NOW)
    assert engine.account.halted, "the error limit did not fire"


@pytest.mark.fast
def test_a_rejection_counts_as_an_error_and_a_fill_clears_it() -> None:
    engine = _engine()
    engine.reconcile(NOW)

    class Rejects(SimulatedAdapter):
        def submit(self, order, at):
            return Ack(order.client_order_id, OrderStatus.REJECTED, reason="-2010")

    rejecting = Rejects(instruments={"BTCUSDT": BTC}, prices={"BTCUSDT": PRICE})
    working, engine.adapter = engine.adapter, rejecting
    engine.submit(_order(), PRICE, {}, NOW)
    assert engine.account.consecutive_errors == 1

    engine.adapter = working
    engine.submit(_order(), PRICE, {}, NOW + timedelta(minutes=5))
    assert engine.account.consecutive_errors == 0


@pytest.mark.fast
def test_positions_move_only_from_fills() -> None:
    """If our view came from a second source it could agree with the venue while
    both were wrong, which would make reconciliation meaningless."""
    engine = _engine()
    engine.reconcile(NOW)
    engine.submit(_order("0.01"), PRICE, {}, NOW)
    assert engine.account.positions.get("BTCUSDT") is None

    assert engine.apply_fills(NOW) == 1
    assert engine.account.positions["BTCUSDT"].quantity == Decimal("0.01")
    # Idempotent: applying again must not double the position.
    assert engine.apply_fills(NOW) == 0
    assert engine.account.positions["BTCUSDT"].quantity == Decimal("0.01")


@pytest.mark.fast
def test_the_kill_switch_flattens_from_the_venue_view() -> None:
    engine = _engine()
    engine.reconcile(NOW)
    engine.submit(_order("0.01"), PRICE, {}, NOW)
    engine.apply_fills(NOW)

    orders = engine.kill(NOW)
    assert engine.account.halted
    assert len(orders) == 1
    assert orders[0].side is Side.SELL and orders[0].reduce_only
    # The flattening order actually reached the venue.
    assert engine.adapter.positions().get("BTCUSDT", Decimal(0)) == Decimal(0)


@pytest.mark.fast
def test_a_halted_engine_still_lets_an_exit_through() -> None:
    engine = _engine()
    engine.reconcile(NOW)
    engine.submit(_order("0.01"), PRICE, {}, NOW)
    engine.apply_fills(NOW)
    engine.halt("manual", NOW)

    # An entry is refused...
    assert engine.submit(_order("0.01"), PRICE, {}, NOW).breach is Breach.HALTED
    # ...and an exit is not. Written as two assertions on the same engine,
    # because the first version of this test was `x is False or True`, which is
    # true for every x and tested nothing.
    exit_order = _order("0.01", side=Side.SELL, reduce_only=True)
    assert engine.submit(exit_order, PRICE, {"BTCUSDT": PRICE}, NOW).allowed

    from tradelab.execution.adapters import HaltedAdapter

    assert isinstance(engine.adapter, HaltedAdapter)
    engine.apply_fills(NOW)
    assert engine.account.positions["BTCUSDT"].quantity == Decimal(0)


@pytest.mark.fast
def test_the_log_survives_a_restart(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    engine = _engine()
    engine.intents = IntentLog(path=path)
    engine.reconcile(NOW)
    engine.submit(_order(), PRICE, {}, NOW)

    written = path.read_text().strip().splitlines()
    assert len(written) >= 3
    assert '"state": "sent"' in path.read_text()
