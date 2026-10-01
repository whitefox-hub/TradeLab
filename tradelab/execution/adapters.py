"""Venue adapters. Plan.md §12.2. Adapter and data feed are all that differ between modes."""

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
    """What a venue says back. An acknowledgement is not a fill (§14.2)."""

    client_order_id: str
    status: OrderStatus
    venue_order_id: str = ""
    reason: str = ""


class VenueAdapter(ABC):
    mode: ExecutionMode

    @property
    def fills(self) -> list[Fill]:
        """Fills observed so far; the engine folds positions from this."""
        return []

    @abstractmethod
    def submit(self, order: Order, at: datetime) -> Ack: ...

    @abstractmethod
    def cancel_all(self, at: datetime) -> int:
        """Cancel every open order. Returns how many were cancelled."""

    @abstractmethod
    def positions(self) -> dict[str, Decimal]:
        """What the *venue* says is held, for reconciliation against our view."""


    @abstractmethod
    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        """The venue's answer for an id we may have sent. None if never seen."""

    def recover_fills(self, asset: str, client_order_id: str,
                      venue_order_id: str) -> int:
        """Bring in the trades of one order the process may have missed. Zero if none can be."""
        del asset, client_order_id, venue_order_id
        return 0

    def prime(self, symbols: list[str] | None = None) -> int:
        """Mark history already at the venue as not ours. Zero where there is none."""
        del symbols
        return 0


@dataclass
class SimulatedAdapter(VenueAdapter):
    """Local fill simulation for BACKTEST and SIMULATOR.

    Fills at price plus modelled cost; enforces tick/lot rounding and min_viable, but none
    of the venue-only rejections (§12.1).
    """

    mode: ExecutionMode = ExecutionMode.SIMULATOR
    instruments: dict[str, Instrument] = field(default_factory=dict)
    prices: dict[str, Decimal] = field(default_factory=dict)
    half_spread_bps: Decimal = Decimal(1)
    taker_fee: Decimal = Decimal("0.0005")
    _fills: list[Fill] = field(default_factory=list)
    _held: dict[str, Decimal] = field(default_factory=dict)
    rejections: list[Ack] = field(default_factory=list)

    @property
    def fills(self) -> list[Fill]:
        return self._fills

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

        slip = price * self.half_spread_bps / Decimal(10_000)
        fill_price = instrument.round_price(price + slip * order.side.sign)
        fee = quantity * fill_price * self.taker_fee

        self._fills.append(Fill(order.asset, order.side, quantity, fill_price, fee,
                                at, order.client_order_id))
        self._held[order.asset] = (
            self._held.get(order.asset, Decimal(0)) + quantity * order.side.sign
        )
        return Ack(order.client_order_id, OrderStatus.FILLED, f"sim-{len(self._fills)}")

    def _reject(self, order: Order, reason: str) -> Ack:
        ack = Ack(order.client_order_id, OrderStatus.REJECTED, reason=reason)
        self.rejections.append(ack)
        return ack

    def cancel_all(self, at: datetime) -> int:
        return 0

    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        for fill in self._fills:
            if fill.asset == asset and fill.client_order_id == client_order_id:
                return Ack(client_order_id, OrderStatus.FILLED)
        return None

    def positions(self) -> dict[str, Decimal]:
        return {a: q for a, q in self._held.items() if q != 0}


@dataclass
class HaltedAdapter(VenueAdapter):
    """Wraps another adapter and refuses everything except reduce-only exits (§14.1)."""

    inner: VenueAdapter
    reason: str = "halted"

    @property
    def mode(self) -> ExecutionMode:  # type: ignore[override]
        return self.inner.mode

    @property
    def fills(self) -> list[Fill]:
        """Delegated: without it fill folding stops while a halted run flattens."""
        return self.inner.fills

    def submit(self, order: Order, at: datetime) -> Ack:
        if not order.reduce_only:
            return Ack(order.client_order_id, OrderStatus.REJECTED,
                       reason=f"halted: {self.reason}")
        return self.inner.submit(order, at)

    def cancel_all(self, at: datetime) -> int:
        return self.inner.cancel_all(at)

    def positions(self) -> dict[str, Decimal]:
        return self.inner.positions()


    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        return self.inner.already_executed(asset, client_order_id)

    def recover_fills(self, asset: str, client_order_id: str,
                      venue_order_id: str) -> int:
        return self.inner.recover_fills(asset, client_order_id, venue_order_id)

    def prime(self, symbols: list[str] | None = None) -> int:
        return self.inner.prime(symbols)


def closing_orders(
    held: dict[str, Decimal], run_id: str = "kill",
) -> list[Order]:
    """Reduce-only orders that flatten everything, from the venue's position view."""
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
