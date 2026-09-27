"""Execution types shared by every mode. Plan.md §12.2.

The strategy is written once and does not know which mode it runs in. Only the
pair (`data_feed`, `venue_adapter`) changes between backtest, simulator, testnet
and live; the decision code is byte-for-byte identical and its hash is recorded
on the run, so it is provable that what trades live is what passed validation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum


class ExecutionMode(StrEnum):
    BACKTEST = "backtest"
    SIMULATOR = "simulator"
    TESTNET = "testnet"
    LIVE = "live"

    @property
    def touches_real_money(self) -> bool:
        return self is ExecutionMode.LIVE

    @property
    def reaches_a_venue(self) -> bool:
        """Testnet reaches a matching engine; the simulator does not.

        The distinction matters because an entire family of failures — tick and
        lot rounding, min-notional rejection, signature errors, recvWindow drift
        — exists only past this boundary (§12.1).
        """
        return self in (ExecutionMode.TESTNET, ExecutionMode.LIVE)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Exchange constraints as of a point in time. Plan.md §4.4.

    Resolved from `asset_universe` at the simulated instant, never from today's
    parameters: a backtest that sizes with current `min_notional` places orders
    the venue would have rejected at the time.
    """

    asset: str
    tick_size: Decimal
    lot_size: Decimal
    min_notional: Decimal

    def min_viable(self, price: Decimal) -> Decimal:
        """The smallest position that can actually be opened (§14.1).

        Not `min_notional`. On BTCUSDT at 84,532 the lot size binds at 84.53
        against a notional minimum of 50, so reading `min_notional` alone gives
        a floor wrong by a factor of 1.7.
        """
        return max(self.min_notional, self.lot_size * price)

    def quantise(self, quantity: Decimal) -> Decimal:
        """Round down to a whole number of lots.

        Down, never nearest: rounding up can breach a limit the risk engine has
        just approved, and a position slightly smaller than intended is always
        safe where a slightly larger one is not.
        """
        if self.lot_size <= 0:
            return quantity
        return (quantity // self.lot_size) * self.lot_size

    def quantise_up(self, quantity: Decimal) -> Decimal:
        """Round up to a whole number of lots. For reductions only.

        The exception to `quantise`. When the order is a trim, rounding down
        leaves the position a fraction of a lot over the cap it is being cut to,
        and the next tick trims again for less than a lot, which quantises to
        nothing. Over-trimming by under a lot is the safe side of a reduction.
        """
        if self.lot_size <= 0:
            return quantity
        # Not -(-q // lot): Decimal's // truncates toward zero rather than
        # flooring, so that idiom rounds down here, silently.
        lots = (quantity / self.lot_size).to_integral_value(rounding=ROUND_CEILING)
        return lots * self.lot_size

    def round_price(self, price: Decimal) -> Decimal:
        if self.tick_size <= 0:
            return price
        return (price // self.tick_size) * self.tick_size


@dataclass(frozen=True, slots=True)
class Order:
    asset: str
    side: Side
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    reduce_only: bool = False
    #: Deterministic, so a retry after a timeout cannot create a second order
    #: (§14.3). Derived from the run and the decision, never random.
    client_order_id: str = ""
    strategy_id: uuid.UUID | None = None

    def with_id(self, run_id: str, sequence: int) -> Order:
        """Attach the deterministic id. Same run and sequence, same id."""
        digest = f"{run_id}:{sequence}:{self.asset}:{self.side}:{self.quantity}"
        return Order(
            asset=self.asset, side=self.side, quantity=self.quantity,
            order_type=self.order_type, limit_price=self.limit_price,
            reduce_only=self.reduce_only,
            client_order_id=uuid.uuid5(uuid.NAMESPACE_OID, digest).hex[:32],
            strategy_id=self.strategy_id,
        )


@dataclass(frozen=True, slots=True)
class Fill:
    asset: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal
    at: datetime
    client_order_id: str
    #: The venue's own id for this trade, where the venue gives one. Empty in
    #: the simulator, which has no trades of its own to name.
    venue_trade_id: str = ""

    @property
    def identity(self) -> str:
        """What makes this fill distinct, so it is folded exactly once.

        The venue's trade id first, because one order can produce several
        trades and every one of them carries the same client order id. Dedupe
        on the client id alone and a fill that arrived in three pieces is
        counted as one — which is the partial-fill failure §16.7 asks about,
        arriving as a position quietly smaller than the venue's.
        """
        return self.venue_trade_id or self.client_order_id


@dataclass
class Position:
    asset: str
    quantity: Decimal = Decimal(0)     # signed: negative is short
    avg_price: Decimal = Decimal(0)
    realised_pnl: Decimal = Decimal(0)
    opened_at: datetime | None = None
    last_entry_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.quantity != 0

    def notional(self, price: Decimal) -> Decimal:
        return abs(self.quantity) * price

    def unrealised(self, price: Decimal) -> Decimal:
        return self.quantity * (price - self.avg_price)

    def apply(self, fill: Fill) -> None:
        """Fold a fill in, tracking realised P&L across reversals."""
        signed = fill.quantity * fill.side.sign
        new_quantity = self.quantity + signed

        if self.quantity == 0 or (self.quantity > 0) == (signed > 0):
            # Opening or adding: the average price moves.
            total = abs(self.quantity) + abs(signed)
            self.avg_price = (
                (self.avg_price * abs(self.quantity) + fill.price * abs(signed)) / total
                if total else Decimal(0)
            )
            if self.quantity == 0:
                self.opened_at = fill.at
        else:
            # Reducing, closing or reversing. Realise against the closed part
            # only; a reversal keeps the remainder at the new price.
            closed = min(abs(signed), abs(self.quantity))
            direction = Decimal(1) if self.quantity > 0 else Decimal(-1)
            self.realised_pnl += closed * (fill.price - self.avg_price) * direction
            if abs(signed) > abs(self.quantity):
                self.avg_price = fill.price
            elif new_quantity == 0:
                self.avg_price = Decimal(0)
                self.opened_at = None

        self.quantity = new_quantity
        self.realised_pnl -= fill.fee
        self.last_entry_at = fill.at


@dataclass
class AccountState:
    """What the risk engine judges against. Owned by the engine, not the strategy."""

    equity: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    open_order_count: int = 0
    consecutive_errors: int = 0
    peak_equity: Decimal = Decimal(0)
    day_start_equity: Decimal = Decimal(0)
    halted: bool = False
    halt_reason: str = ""

    def gross_exposure(self, prices: dict[str, Decimal]) -> Decimal:
        return sum(
            (p.notional(prices[p.asset]) for p in self.positions.values()
             if p.is_open and p.asset in prices),
            Decimal(0),
        )

    def net_exposure(self, prices: dict[str, Decimal]) -> Decimal:
        return sum(
            (p.quantity * prices[p.asset] for p in self.positions.values()
             if p.is_open and p.asset in prices),
            Decimal(0),
        )

    @property
    def open_positions(self) -> int:
        return sum(1 for p in self.positions.values() if p.is_open)
