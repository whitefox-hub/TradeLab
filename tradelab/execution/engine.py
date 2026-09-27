"""The run loop. One engine, four modes. Plan.md §12.2, §14.2, §14.3.

The strategy hands this engine orders and never learns which mode it is in. What
the engine adds around each order is the part that does not belong in a strategy
and must not be reimplemented per strategy: the risk veto, the intent log, the
deterministic id, and the reconciliation that has to happen before anything is
sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradelab.execution.adapters import HaltedAdapter, VenueAdapter, closing_orders
from tradelab.execution.intent import IntentLog, IntentState
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Instrument,
    Order,
    OrderStatus,
    Position,
)
from tradelab.risk.limits import Decision, RiskEngine


class NotReconciled(RuntimeError):
    """Raised when an order is attempted before startup reconciliation.

    A system that restarts after a crash and trades without reconciling can
    double an existing position (§14.2), so this is an exception rather than a
    warning: the failure it prevents is silent and expensive.
    """


@dataclass(frozen=True, slots=True)
class Divergence:
    asset: str
    ours: Decimal
    theirs: Decimal

    @property
    def gap(self) -> Decimal:
        return self.theirs - self.ours


@dataclass
class ExecutionEngine:
    run_id: str
    mode: ExecutionMode
    adapter: VenueAdapter
    risk: RiskEngine
    instruments: dict[str, Instrument]
    intents: IntentLog = field(default_factory=IntentLog)
    _sequence: int = 0
    _reconciled: bool = False
    _applied: set[str] = field(default_factory=set)

    @property
    def account(self) -> AccountState:
        return self.risk.account

    # -- startup -------------------------------------------------------------

    def reconcile(self, at: datetime) -> list[Divergence]:
        """Compare our position view against the venue's. Plan.md §14.2.

        Mandatory before the first order. A discrepancy halts immediately and is
        never silently corrected: adopting the venue's number would hide the bug
        that produced the gap, and the gap is the only evidence it exists.
        """
        theirs = self.adapter.positions()
        assets = set(theirs) | {a for a, p in self.account.positions.items() if p.is_open}

        divergences = [
            Divergence(asset, ours, theirs.get(asset, Decimal(0)))
            for asset in sorted(assets)
            if (ours := self._our_quantity(asset)) != theirs.get(asset, Decimal(0))
        ]

        if divergences:
            detail = ", ".join(f"{d.asset} ours={d.ours} venue={d.theirs}" for d in divergences)
            self.halt(f"position divergence at reconciliation: {detail}", at)
        self._reconciled = True
        return divergences

    def _our_quantity(self, asset: str) -> Decimal:
        position = self.account.positions.get(asset)
        return position.quantity if position else Decimal(0)

    def resume_unresolved(self, at: datetime) -> list[str]:
        """Settle orders that were sent but never acknowledged (§14.3).

        Their ids are deterministic, so the venue can be asked about each one by
        name rather than guessed at from a position delta.
        """
        pending = self.intents.unresolved()
        for intent in pending:
            self.intents.append(
                type(intent)(
                    at=at, run_id=intent.run_id, sequence=intent.sequence,
                    client_order_id=intent.client_order_id, asset=intent.asset,
                    side=intent.side, quantity=intent.quantity,
                    state=IntentState.UNKNOWN,
                    reason="sent before restart; resolve against the venue",
                )
            )
        return [i.client_order_id for i in pending]

    # -- the loop ------------------------------------------------------------

    def submit(
        self, order: Order, price: Decimal, prices: dict[str, Decimal],
        at: datetime, mid_price: Decimal | None = None,
    ) -> Decision:
        """One order through the whole path. Returns why, when it does not go."""
        if not self._reconciled:
            raise NotReconciled(
                f"{self.run_id}: reconcile() must run before the first order (§14.2)"
            )

        self._sequence += 1
        order = order.with_id(self.run_id, self._sequence)
        instrument = self.instruments.get(order.asset)

        self.intents.record(order, self.run_id, self._sequence, IntentState.WANTED, at)

        if instrument is None:
            decision = Decision(allowed=False, detail=f"no instrument for {order.asset}")
            self.intents.record(order, self.run_id, self._sequence, IntentState.VETOED,
                                at, decision.detail)
            return decision

        decision = self.risk.check(order, price, instrument, prices, at, mid_price)
        if not decision.allowed:
            # Vetoes are logged. A refused order is a decision, and a log that
            # keeps only what executed cannot explain a quiet day.
            self.intents.record(order, self.run_id, self._sequence, IntentState.VETOED,
                                at, f"{decision.breach}: {decision.detail}")
            if decision.halts:
                self.halt(f"{decision.breach}: {decision.detail}", at)
            return decision

        # Written before the venue is called, never after. If the process dies
        # on the next line, startup finds this record and knows to go looking.
        self.intents.record(order, self.run_id, self._sequence, IntentState.SENT, at)
        self.risk.record_order(at)

        try:
            ack = self.adapter.submit(order, at)
        except Exception as error:
            # Broad on purpose. A venue client can raise anything from a socket
            # error to a JSON decode failure, and an engine that dies on one
            # leaves an order in flight with nobody tracking it. The error is
            # counted, and max_consecutive_errors is what stops the run.
            self.risk.record_error()
            self.intents.record(order, self.run_id, self._sequence, IntentState.UNKNOWN,
                                at, f"{type(error).__name__}: {error}")
            self.risk.enforce(at)
            return Decision(allowed=False, detail=str(error))

        if ack.status is OrderStatus.REJECTED:
            self.risk.record_error()
            self.intents.record(order, self.run_id, self._sequence, IntentState.REJECTED,
                                at, ack.reason, ack.venue_order_id)
            self.risk.enforce(at)
            return Decision(allowed=False, detail=ack.reason)

        self.risk.record_success()
        self.intents.record(order, self.run_id, self._sequence, IntentState.ACKNOWLEDGED,
                            at, venue_order_id=ack.venue_order_id)
        return decision

    def apply_fills(self, at: datetime) -> int:
        """Fold the adapter's fills into our position view.

        Positions are updated from fills only. Nothing else is allowed to move
        them, which is what makes the reconciliation in §14.2 meaningful: if our
        view is derived from a second source it can agree with the venue while
        both are wrong.
        """
        applied = 0
        for fill in self.adapter.fills:
            if not fill.identity:
                # Every fill must be nameable or none of them can be counted:
                # an empty key makes the first fill swallow all the rest.
                raise ValueError(
                    f"{self.run_id}: a fill on {fill.asset} carries neither a "
                    f"venue trade id nor a client order id, so it cannot be "
                    f"folded exactly once")
            position = self.account.positions.setdefault(fill.asset, Position(fill.asset))
            if fill.identity in self._applied:
                continue
            position.apply(fill)
            self._applied.add(fill.identity)
            self.intents.record(
                Order(fill.asset, fill.side, fill.quantity,
                      client_order_id=fill.client_order_id),
                self.run_id, self._sequence, IntentState.FILLED, at,
                venue_order_id=fill.venue_trade_id,
            )
            applied += 1
        return applied

    # -- stopping ------------------------------------------------------------

    def halt(self, reason: str, at: datetime) -> None:
        """Stop trading without stopping the process.

        The adapter is wrapped rather than discarded, so reduce-only exits still
        reach the venue. A halt that also blocks closing orders leaves the system
        holding whatever caused the halt.
        """
        self.risk.halt(reason)
        if not isinstance(self.adapter, HaltedAdapter):
            self.adapter = HaltedAdapter(self.adapter, reason=reason)

    def kill(self, at: datetime) -> list[Order]:
        """Cancel everything, flatten, stay halted. Plan.md §14.1.

        Built from the *venue's* position view, because the kill switch runs in
        exactly the situations where our view and theirs have parted company.
        """
        self.halt("kill switch", at)
        self.adapter.cancel_all(at)
        orders = closing_orders(self.adapter.positions(), run_id=f"{self.run_id}-kill")
        for order in orders:
            self.intents.record(order, self.run_id, self._sequence, IntentState.SENT, at,
                                "kill switch")
            self.adapter.submit(order, at)
        return orders
