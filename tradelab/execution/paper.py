"""A paper venue for strategies that rest limits. Plan.md §12.1 rung 2.

`SimulatedAdapter` fills everything at the mark, crossing the spread, with the
taker fee. That is right for a market order and wrong for a resting limit,
which fills at its own price and pays the maker fee: at Flash Strike's size the
difference is the whole of §0.2's maker argument.

So a LIMIT order with a price fills **at that price, at the maker fee**. The
caller is responsible for having decided that it filled; for Flash Strike that
decision is the bar model's trade-through, calibrated against aggTrades
(RESULT-008: 99.9% confirmed). A MARKET order is the simulated adapter's,
unchanged: at the mark, the spread crossed, the taker fee.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradelab.execution.adapters import Ack, SimulatedAdapter
from tradelab.execution.types import Fill, Order, OrderStatus, OrderType


@dataclass
class PaperAdapter(SimulatedAdapter):
    maker_fee: Decimal = Decimal("0.0002")

    def submit(self, order: Order, at: datetime) -> Ack:
        if order.order_type is not OrderType.LIMIT or order.limit_price is None:
            return super().submit(order, at)
        instrument = self.instruments.get(order.asset)
        if instrument is None:
            return self._reject(order, "no instrument")
        quantity = instrument.quantise(order.quantity)
        price = instrument.round_price(order.limit_price)
        if quantity <= 0:
            return self._reject(order, "quantises to zero")
        if not order.reduce_only and quantity * price < instrument.min_viable(price):
            return self._reject(order, "below min_viable")
        self._fills.append(Fill(order.asset, order.side, quantity, price,
                                quantity * price * self.maker_fee, at,
                                order.client_order_id))
        self._held[order.asset] = (self._held.get(order.asset, Decimal(0))
                                   + quantity * order.side.sign)
        return Ack(order.client_order_id, OrderStatus.FILLED, f"paper-{len(self._fills)}")
