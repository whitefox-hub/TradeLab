"""The run loop. One engine, four modes. Plan.md §12.2, §14.2, §14.3.

Adds around each order what strategies must not reimplement: risk veto, intent log,
deterministic id, and reconciliation before anything is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from tradelab.execution.adapters import HaltedAdapter, VenueAdapter, closing_orders
from tradelab.execution.intent import Intent, IntentLog, IntentState
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    OrderStatus,
    Position,
    Side,
)
from tradelab.risk.limits import Decision, RiskEngine


class NotReconciled(RuntimeError):
    """An order attempted before startup reconciliation (could double a position, §14.2)."""


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
    #: Set by a reconciliation gap and never cleared: later agreement does not fix the bug.
    _diverged: bool = False
    _applied: set[str] = field(default_factory=set)

    @property
    def account(self) -> AccountState:
        return self.risk.account

    # -- startup -------------------------------------------------------------

    def reconcile(self, at: datetime) -> list[Divergence]:
        """Compare our position view against the venue's. Plan.md §14.2.

        Mandatory before the first order. A gap halts; never adopt the venue's number silently.
        """
        theirs = self.adapter.positions()
        assets = set(theirs) | {a for a, p in self.account.positions.items() if p.is_open}

        divergences = [
            Divergence(asset, ours, theirs.get(asset, Decimal(0)))
            for asset in sorted(assets)
            if (ours := self._our_quantity(asset)) != theirs.get(asset, Decimal(0))
        ]

        if divergences:
            self._diverged = True
            detail = ", ".join(f"{d.asset} ours={d.ours} venue={d.theirs}" for d in divergences)
            self.halt(f"position divergence at reconciliation: {detail}", at)
        self._reconciled = True
        return divergences

    def _our_quantity(self, asset: str) -> Decimal:
        position = self.account.positions.get(asset)
        return position.quantity if position else Decimal(0)

    def recover(self, at: datetime) -> list[Divergence]:
        """Restart from the intent log, then reconcile. Plan.md §14.3.

        1. Rebuild positions from FILLED records; continue the sequence (restarting at zero
           would reuse ids the venue no longer refuses).
        2. Ask about each SENT/UNKNOWN id: never seen -> ABSENT, not resent.
        3. Fetch fills of every acknowledged order (may have filled while down).
        4. Reconcile; a remaining gap halts.  5. Prime remaining history as not ours.
        """
        if self._reconciled:
            raise RuntimeError(
                f"{self.run_id}: recover() is a startup step; this engine has "
                "already reconciled and may have traded")

        self._sequence = max(
            (i.sequence for i in self.intents.records if i.run_id == self.run_id),
            default=0)
        for intent in self.intents.records:
            if intent.state is IntentState.FILLED:
                self._fold_recorded(intent)

        reached: dict[str, tuple[str, str]] = {
            i.client_order_id: (i.asset, i.venue_order_id)
            for i in self.intents.records
            if i.state is IntentState.ACKNOWLEDGED and i.client_order_id
        }
        for intent in self.intents.unresolved():
            found = self.adapter.already_executed(intent.asset, intent.client_order_id)
            if found is None:
                state, reason = IntentState.ABSENT, "resolved after restart: never seen"
            elif found.status is OrderStatus.REJECTED:
                state, reason = IntentState.REJECTED, "resolved after restart: rejected"
            else:
                state = IntentState.ACKNOWLEDGED
                reason = f"resolved after restart: venue reports {found.status}"
                reached[intent.client_order_id] = (intent.asset, found.venue_order_id)
            self.intents.append(replace(
                intent, at=at, state=state, reason=reason,
                venue_order_id=found.venue_order_id if found else ""))

        for client_order_id, (asset, venue_order_id) in reached.items():
            self.adapter.recover_fills(asset, client_order_id, venue_order_id)
        self.apply_fills(at)

        divergences = self.reconcile(at)
        self.adapter.prime(sorted(self.instruments))
        return divergences

    def _fold_recorded(self, intent: Intent) -> None:
        if intent.price is None:
            raise ValueError(
                f"{self.run_id}: a fill on {intent.asset} was logged without its "
                "price, so the position cannot be rebuilt. The log predates "
                "recovery; reconcile against the venue by hand before resuming")
        identity = intent.venue_order_id or intent.client_order_id
        if identity in self._applied:
            return
        position = self.account.positions.setdefault(intent.asset, Position(intent.asset))
        position.apply(Fill(
            asset=intent.asset, side=Side(intent.side), quantity=intent.quantity,
            price=intent.price, fee=intent.fee or Decimal(0), at=intent.at,
            client_order_id=intent.client_order_id,
            venue_trade_id=intent.venue_order_id))
        self._applied.add(identity)

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
            # Vetoes are logged: a refused order is a decision.
            self.intents.record(order, self.run_id, self._sequence, IntentState.VETOED,
                                at, f"{decision.breach}: {decision.detail}")
            if decision.halts:
                self.halt(f"{decision.breach}: {decision.detail}", at)
            return decision

        # Written before the venue call, so a crash leaves a record to resolve on startup.
        self.intents.record(order, self.run_id, self._sequence, IntentState.SENT, at)
        self.risk.record_order(at)

        try:
            ack = self.adapter.submit(order, at)
        except Exception as error:
            # Broad on purpose: dying here leaves an order in flight untracked.
            # max_consecutive_errors stops the run.
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
        """Fold the adapter's fills into our position view; only fills move positions (§14.2)."""
        applied = 0
        for fill in self.adapter.fills:
            if not fill.identity:
                # An empty key would make the first fill swallow all the rest.
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
                venue_order_id=fill.venue_trade_id, price=fill.price, fee=fill.fee,
            )
            applied += 1
        return applied

    def trim_held(self, prices: dict[str, Decimal], at: datetime) -> list[Order]:
        """Cut every held position that has grown past its cap back to it. Called every tick.

        Reduce-only, so it passes a halt (RESULT-007). Judged on our view, and nothing is
        trimmed once reconciliation found that view wrong; the kill switch handles that state.
        """
        if self._diverged:
            return []
        orders = []
        for asset, excess in sorted(self.risk.held_excess(prices).items()):
            position = self.account.positions[asset]
            instrument = self.instruments.get(asset)
            size = abs(position.quantity)
            quantity = min(instrument.quantise_up(excess) if instrument else excess, size)
            if quantity <= 0:
                continue
            order = Order(asset, Side.SELL if position.quantity > 0 else Side.BUY,
                          quantity, reduce_only=True)
            self.submit(order, prices[asset], prices, at)
            orders.append(order)
        return orders

    # -- stopping ------------------------------------------------------------

    def halt(self, reason: str, at: datetime) -> None:
        """Stop trading without stopping the process; reduce-only exits still reach the venue."""
        self.risk.halt(reason)
        if not isinstance(self.adapter, HaltedAdapter):
            self.adapter = HaltedAdapter(self.adapter, reason=reason)

    def kill(self, at: datetime) -> list[Order]:
        """Cancel everything, flatten, stay halted. Plan.md §14.1.

        Built from the venue's position view: it runs when ours may be wrong.
        """
        self.halt("kill switch", at)
        self.adapter.cancel_all(at)
        orders = closing_orders(self.adapter.positions(), run_id=f"{self.run_id}-kill")
        for order in orders:
            self.intents.record(order, self.run_id, self._sequence, IntentState.SENT, at,
                                "kill switch")
            self.adapter.submit(order, at)
        return orders
