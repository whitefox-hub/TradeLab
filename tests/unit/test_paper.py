"""The paper venue. A resting limit fills at its price and pays the maker fee."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradelab.execution.paper import PaperAdapter
from tradelab.execution.types import Instrument, Order, OrderStatus, OrderType, Side

NOW = datetime(2026, 10, 1, tzinfo=UTC)
ADA = Instrument("ADAUSDT", Decimal("0.0001"), Decimal("1"), Decimal("5"))


def _paper() -> PaperAdapter:
    return PaperAdapter(instruments={"ADAUSDT": ADA}, prices={"ADAUSDT": Decimal("0.26")},
                        half_spread_bps=Decimal("1.5"))


@pytest.mark.fast
def test_a_limit_fills_at_its_own_price_at_the_maker_fee() -> None:
    venue = _paper()
    ack = venue.submit(Order("ADAUSDT", Side.BUY, Decimal("100"), OrderType.LIMIT,
                             limit_price=Decimal("0.25")).with_id("r", 1), NOW)
    assert ack.status is OrderStatus.FILLED
    fill = venue.fills[-1]
    assert fill.price == Decimal("0.2500")
    assert fill.fee == Decimal("100") * Decimal("0.25") * Decimal("0.0002")
    assert venue.positions() == {"ADAUSDT": Decimal("100")}


@pytest.mark.fast
def test_a_market_order_still_crosses_the_spread_at_the_taker_fee() -> None:
    venue = _paper()
    venue.submit(Order("ADAUSDT", Side.SELL, Decimal("100")).with_id("r", 1), NOW)
    fill = venue.fills[-1]
    assert fill.price < Decimal("0.26")
    assert fill.fee == pytest.approx(Decimal("100") * fill.price * Decimal("0.0005"))


@pytest.mark.fast
def test_a_reduce_only_exit_is_not_refused_for_being_small() -> None:
    venue = _paper()
    venue.submit(Order("ADAUSDT", Side.BUY, Decimal("100"), OrderType.LIMIT,
                       limit_price=Decimal("0.25")).with_id("r", 1), NOW)
    ack = venue.submit(Order("ADAUSDT", Side.SELL, Decimal("10"), OrderType.LIMIT,
                             limit_price=Decimal("0.26"), reduce_only=True).with_id("r", 2), NOW)
    assert ack.status is OrderStatus.FILLED
