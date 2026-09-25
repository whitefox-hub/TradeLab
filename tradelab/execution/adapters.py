"""Venue adapters. Plan.md §12.2.

Four modes, one engine. The adapter and the data feed are the only things that
change between them; the decision code is byte-for-byte identical, so the
strategy running live is provably the one that passed validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradelab.execution.types import (
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    OrderStatus,
    Side,
)


@dataclass(frozen=True, slots=True)
class Ack:
    """What a venue says back. Deliberately poorer than a Fill.

    An acknowledgement is not a fill, and conflating them is how a system comes
    to believe it holds a position the venue never opened (§14.2).
    """

    client_order_id: str
    status: OrderStatus
    venue_order_id: str = ""
    reason: str = ""


class VenueAdapter(ABC):
    mode: ExecutionMode

    @abstractmethod
    def submit(self, order: Order, at: datetime) -> Ack: ...

    @abstractmethod
    def cancel_all(self, at: datetime) -> int:
        """Cancel every open order. Returns how many were cancelled."""

    @abstractmethod
    def positions(self) -> dict[str, Decimal]:
        """What the *venue* says is held, for reconciliation against our view."""


@dataclass
class SimulatedAdapter(VenueAdapter):
    """Local fill simulation for BACKTEST and SIMULATOR.

    Fills at the given price plus a modelled cost. It cannot reject for reasons
    a real venue would — signature errors, recvWindow drift, margin — which is
    precisely why §12.1 makes testnet non-optional. What it *does* enforce is
    tick and lot rounding, because those are knowable offline and a simulator
    that ignores them reports fills at sizes no venue would accept.
    """

    mode: ExecutionMode = ExecutionMode.SIMULATOR
    instruments: dict[str, Instrument] = field(default_factory=dict)
    prices: dict[str, Decimal] = field(default_factory=dict)
    half_spread_bps: Decimal = Decimal(1)
    taker_fee: Decimal = Decimal("0.0005")
    fills: list[Fill] = field(default_factory=list)
    _held: dict[str, Decimal] = field(default_factory=dict)
    rejections: list[Ack] = field(default_factory=list)

    def submit(self, order: Order, at: datetime) -> Ack:
        instrument = self.instruments.get(order.asset)
        price = self.prices.get(order.asset)
        if instrument is None or price is None:
            return self._reject(order, "no instrument or price")

        quantity = instrument.quantise(order.quantity)
        if quantity <= 0:
            return self._reject(order, "quantises to zero")
        if quantity * price < instrument.min_viable(price):
            return self._reject(order, "below min_viable")

        # Cross the spread in the direction that hurts, always. A simulator that
        # fills at mid reports an edge the venue will not pay.
        slip = price * self.half_spread_bps / Decimal(10_000)
        fill_price = instrument.round_price(price + slip * order.side.sign)
        fee = quantity * fill_price * self.taker_fee

        self.fills.append(Fill(order.asset, order.side, quantity, fill_price, fee,
                               at, order.client_order_id))
        self._held[order.asset] = (
            self._held.get(order.asset, Decimal(0)) + quantity * order.side.sign
        )
        return Ack(order.client_order_id, OrderStatus.FILLED, f"sim-{len(self.fills)}")

    def _reject(self, order: Order, reason: str) -> Ack:
        ack = Ack(order.client_order_id, OrderStatus.REJECTED, reason=reason)
        self.rejections.append(ack)
        return ack

    def cancel_all(self, at: datetime) -> int:
        return 0  # market orders fill immediately; nothing rests

    def positions(self) -> dict[str, Decimal]:
        return {a: q for a, q in self._held.items() if q != 0}


@dataclass
class HaltedAdapter(VenueAdapter):
    """Wraps another adapter and refuses everything except reduce-only exits.

    The kill switch leaves the system in this state rather than stopping the
    process, so that positions can still be closed while nothing new opens
    (§14.1).
    """

    inner: VenueAdapter
    reason: str = "halted"

    @property
    def mode(self) -> ExecutionMode:  # type: ignore[override]
        return self.inner.mode

    def submit(self, order: Order, at: datetime) -> Ack:
        if not order.reduce_only:
            return Ack(order.client_order_id, OrderStatus.REJECTED,
                       reason=f"halted: {self.reason}")
        return self.inner.submit(order, at)

    def cancel_all(self, at: datetime) -> int:
        return self.inner.cancel_all(at)

    def positions(self) -> dict[str, Decimal]:
        return self.inner.positions()


def closing_orders(
    held: dict[str, Decimal], run_id: str = "kill",
) -> list[Order]:
    """Reduce-only orders that flatten everything.

    Built from the *venue's* position view rather than ours, because the kill
    switch runs precisely in the situations where the two have diverged.
    """
    orders = []
    for sequence, (asset, quantity) in enumerate(sorted(held.items())):
        if quantity == 0:
            continue
        orders.append(
            Order(
                asset=asset,
                side=Side.SELL if quantity > 0 else Side.BUY,
                quantity=abs(quantity),
                reduce_only=True,
            ).with_id(run_id, sequence)
        )
    return orders
