"""What the testnet said that the mocks did not. Plan.md §12.1, §14.2, §14.3.

Both cases here were written after the sandbox contradicted the adapter, and
both assert the contradiction rather than the belief that preceded it. The
mocks reproduce the responses the venue actually sent, down to the fields that
made the original code wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from tradelab.execution.binance_testnet import (
    TESTNET,
    UNKNOWN_ORDER,
    BinanceFuturesAdapter,
    VenueError,
)
from tradelab.execution.types import Order, OrderStatus, OrderType, Side

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

# Exactly what the testnet returns for a filled market order: the acknowledgement
# is written before the trade is, so it carries neither the quantity nor the
# price that the order went on to get.
MARKET_ACK = {"orderId": 28605172275, "status": "NEW", "executedQty": "0",
              "avgPrice": "0.00", "symbol": "BTCUSDT"}
TRADE = {"id": 4227901, "symbol": "BTCUSDT", "qty": "0.002", "price": "84419.10",
         "commission": "0.06753528", "buyer": True, "time": 1790474093000}


def _adapter(handler) -> BinanceFuturesAdapter:
    adapter = BinanceFuturesAdapter(key="k", secret="s", base=TESTNET)
    adapter.client = httpx.Client(transport=httpx.MockTransport(handler))
    return adapter


@pytest.mark.fast
def test_a_market_order_fills_although_its_acknowledgement_reports_nothing() -> None:
    """The defect the sandbox found, as the sandbox found it.

    The adapter used to call `collect_fills` only where `executedQty` was above
    zero. For a market order that field is "0" and the status is NEW, so the
    branch never ran: the venue held 0.002 BTC and the engine, which folds
    positions from fills alone, held nothing. §14.2 exists to detect that
    divergence, and the adapter feeding it was manufacturing it.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if "/fapi/v1/order" in request.url.path:
            return httpx.Response(200, json=MARKET_ACK)
        return httpx.Response(200, json=[TRADE])

    adapter = _adapter(handler)
    ack = adapter.submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.002"), OrderType.MARKET, None,
              client_order_id="run-1-3"), NOW)

    assert ack.status is OrderStatus.ACCEPTED
    assert len(adapter.fills) == 1, "the acknowledgement was believed over the trade"
    assert adapter.fills[0].quantity == Decimal("0.002")
    assert adapter.fills[0].fee == Decimal("0.06753528"), "a fee no ack carried"


@pytest.mark.fast
def test_the_same_trade_is_not_folded_twice_across_orders() -> None:
    """The other half of collecting unconditionally: it now runs on every
    order, so the dedupe by venue trade id is load-bearing rather than
    decorative."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/fapi/v1/order" in request.url.path:
            return httpx.Response(200, json=MARKET_ACK)
        return httpx.Response(200, json=[TRADE])

    adapter = _adapter(handler)
    for i in range(3):
        adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.002"),
                             OrderType.MARKET, None, client_order_id=f"r-{i}"), NOW)
    assert len(adapter.fills) == 1


@pytest.mark.fast
def test_the_venue_does_not_refuse_a_repeated_client_order_id() -> None:
    """Measured, not assumed: two MARKET orders carrying the identical
    `newClientOrderId` were both accepted, with venue ids 28605172275 and
    28605172284, and the position went to 0.0040.

    Binance frees a client id as soon as the order leaves the book, which for a
    market order is at once. The docstring used to claim the opposite, and a
    mock that answered FILLED to anything was never going to disagree.
    """
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.query.decode())
        return httpx.Response(200, json={**MARKET_ACK,
                                         "orderId": 28605172275 + len(sent)}
                              ) if "/fapi/v1/order" in request.url.path \
            else httpx.Response(200, json=[])

    adapter = _adapter(handler)
    same = Order("BTCUSDT", Side.BUY, Decimal("0.002"), OrderType.MARKET, None,
                 client_order_id="run-1-3")
    first, second = adapter.submit(same, NOW), adapter.submit(same, NOW)

    assert first.status is OrderStatus.ACCEPTED
    assert second.status is OrderStatus.ACCEPTED, "the venue is not the guard"
    assert first.venue_order_id != second.venue_order_id

    body = BinanceFuturesAdapter.submit.__doc__ or ""
    assert "does not enforce" in body, "the docstring claims a guarantee again"


@pytest.mark.fast
def test_a_known_id_can_be_resolved_instead_of_resent() -> None:
    """Where the idempotency actually lives. §14.3 writes the intent before the
    order goes out so a process that died in between asks about the id it knows
    rather than sending it a second time."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("origClientOrderId") == "run-1-3":
            return httpx.Response(200, json={"orderId": 28605172275,
                                             "status": "FILLED"})
        return httpx.Response(400, json={"code": UNKNOWN_ORDER,
                                         "msg": "Order does not exist."})

    adapter = _adapter(handler)
    found = adapter.already_executed("BTCUSDT", "run-1-3")
    assert found is not None
    assert found.status is OrderStatus.FILLED
    assert found.venue_order_id == "28605172275"
    assert adapter.already_executed("BTCUSDT", "never-sent") is None


@pytest.mark.fast
def test_an_unreadable_answer_is_not_read_as_never_sent() -> None:
    """The dangerous failure: rate limited or signature rejected, read as "no
    such order", answered by resending. Only -2013 means never sent."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": -1003, "msg": "Too many requests."})

    with pytest.raises(VenueError):
        _adapter(handler).already_executed("BTCUSDT", "run-1-3")
