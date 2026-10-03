"""The Binance spot adapter against a mocked venue."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tradelab.execution.binance import Credentials, UnsafeCredentials
from tradelab.execution.binance_spot import DEMO_BASE, LIVE_BASE, BinanceSpotAdapter
from tradelab.execution.types import ExecutionMode, Order, OrderStatus, Side

EXCHANGE_INFO = {"symbols": [{
    "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.00000000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
    ]}]}


class Venue:
    """Just enough of Binance spot: one market order fills as two trades."""

    def __init__(self, restrictions: dict | None = None, commission_asset: str = "BTC"):
        self.orders: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.btc = Decimal(0)
        self.restrictions = restrictions or {}
        self.commission_asset = commission_asset

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_790_000_000_000})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=EXCHANGE_INFO)
        if path == "/api/v3/account":
            balances = [{"asset": "USDT", "free": "1000", "locked": "0"}]
            if self.btc:
                balances.append({"asset": "BTC", "free": str(self.btc), "locked": "0"})
            return httpx.Response(200, json={"canTrade": True, "canWithdraw": True,
                                             "balances": balances})
        if path == "/sapi/v1/account/apiRestrictions":
            return httpx.Response(200, json=self.restrictions)
        if path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": q["symbol"], "price": "600.00"})
        if path == "/api/v3/order" and request.method == "POST":
            if q["newClientOrderId"] in self.orders:
                return httpx.Response(400, json={"code": -2010, "msg": "Duplicate order"})
            order_id = len(self.orders) + 1
            qty = Decimal(q["quantity"])
            self.orders[q["newClientOrderId"]] = {"orderId": order_id, "status": "FILLED"}
            buy = q["side"] == "BUY"
            for i, part in enumerate((qty / 2, qty / 2)):
                fee_asset = self.commission_asset if buy else "USDT"
                commission = part * Decimal("0.001") * (
                    1 if fee_asset == "BTC" else Decimal(80_000) if fee_asset == "USDT"
                    else Decimal(80_000) / 600)
                self.trades.append({
                    "symbol": "BTCUSDT", "id": 100 * order_id + i, "orderId": order_id,
                    "price": "80000.00", "qty": str(part), "commission": str(commission),
                    "commissionAsset": fee_asset, "time": 1_790_000_000_000, "isBuyer": buy})
                self.btc += (part - (commission if fee_asset == "BTC" else 0)) if buy else -part
            return httpx.Response(200, json={"orderId": order_id, "status": "FILLED",
                                             "executedQty": str(qty)})
        if path == "/api/v3/order":
            found = self.orders.get(q["origClientOrderId"])
            if found is None:
                return httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})
            return httpx.Response(200, json=found)
        if path == "/api/v3/myTrades":
            trades = [t for t in self.trades
                      if "orderId" not in q or str(t["orderId"]) == q["orderId"]]
            return httpx.Response(200, json=trades)
        return httpx.Response(404, json={"code": -1, "msg": f"unmocked {path}"})


def _adapter(venue: Venue, mode=ExecutionMode.TESTNET) -> BinanceSpotAdapter:
    a = BinanceSpotAdapter(credentials=Credentials("k", "s", mode), fill_wait_s=0)
    a.client = httpx.Client(base_url=a.base_url, transport=httpx.MockTransport(venue))
    a.sync_clock()
    a.load_instruments(["BTCUSDT"])
    return a


@pytest.mark.fast
def test_demo_and_live_are_different_hosts() -> None:
    assert BinanceSpotAdapter(Credentials("k", "s", ExecutionMode.TESTNET)).base_url == DEMO_BASE
    assert BinanceSpotAdapter(Credentials("k", "s", ExecutionMode.LIVE)).base_url == LIVE_BASE


@pytest.mark.fast
def test_instruments_are_read_from_the_venue() -> None:
    inst = _adapter(Venue()).instruments["BTCUSDT"]
    assert inst.lot_size == Decimal("0.00001")
    assert inst.min_notional == Decimal(5)
    assert inst.tick_size == Decimal("0.01")


@pytest.mark.fast
def test_a_commission_in_the_base_asset_leaves_the_position_equal_to_the_balance() -> None:
    """A buy pays its commission in BTC: the engine must hold what the venue holds, or the
    first reconciliation halts the book and the first sale asks for BTC that is not there."""
    venue = Venue()
    a = _adapter(venue)
    ack = a.submit(Order("BTCUSDT", Side.BUY, Decimal("0.00125")).with_id("r", 1),
                   datetime.now(UTC))
    assert ack.status is OrderStatus.FILLED
    assert len(a.fills) == 2
    held = sum(f.quantity for f in a.fills)
    assert held == venue.btc == a.positions()["BTCUSDT"]
    spent = sum(f.quantity * f.price + f.fee for f in a.fills)
    assert spent == Decimal("0.00125") * 80_000, "quantity times price plus fee is what left"


@pytest.mark.fast
def test_a_commission_in_the_quote_asset_is_the_fee_itself() -> None:
    venue = Venue()
    a = _adapter(venue)
    a.submit(Order("BTCUSDT", Side.BUY, Decimal("0.00125")).with_id("r", 1), datetime.now(UTC))
    a.submit(Order("BTCUSDT", Side.SELL, Decimal("0.00124")).with_id("r", 2), datetime.now(UTC))
    sold = [f for f in a.fills if f.side is Side.SELL]
    assert sum(f.quantity for f in sold) == Decimal("0.00124")
    assert sum(f.fee for f in sold) == Decimal("0.00124") * 80_000 * Decimal("0.001")


@pytest.mark.fast
def test_a_commission_in_bnb_is_valued_in_quote() -> None:
    venue = Venue(commission_asset="BNB")
    a = _adapter(venue)
    a.submit(Order("BTCUSDT", Side.BUY, Decimal("0.001")).with_id("r", 1), datetime.now(UTC))
    assert sum(f.quantity for f in a.fills) == Decimal("0.001")
    assert sum(f.fee for f in a.fills) == pytest.approx(Decimal("0.08"))


@pytest.mark.fast
def test_an_id_the_venue_never_saw_is_absent_and_one_it_saw_is_reported() -> None:
    a = _adapter(Venue())
    order = Order("BTCUSDT", Side.BUY, Decimal("0.001")).with_id("r", 1)
    assert a.already_executed("BTCUSDT", order.client_order_id) is None
    a.submit(order, datetime.now(UTC))
    assert a.already_executed("BTCUSDT", order.client_order_id).status is OrderStatus.FILLED


@pytest.mark.fast
def test_a_resent_id_is_not_a_second_order() -> None:
    venue = Venue()
    a = _adapter(venue)
    order = Order("BTCUSDT", Side.BUY, Decimal("0.001")).with_id("r", 1)
    a.submit(order, datetime.now(UTC))
    again = a.submit(order, datetime.now(UTC))
    assert again.status is OrderStatus.FILLED and len(venue.orders) == 1


@pytest.mark.fast
def test_history_already_at_the_venue_is_not_ours() -> None:
    venue = Venue()
    first = _adapter(venue)
    first.submit(Order("BTCUSDT", Side.BUY, Decimal("0.001")).with_id("r", 1),
                 datetime.now(UTC))
    fresh = _adapter(venue)
    assert fresh.prime(["BTCUSDT"]) == 2
    assert fresh.fills == []


@pytest.mark.fast
@pytest.mark.parametrize("restrictions, refused", [
    ({"enableWithdrawals": True, "enableSpotAndMarginTrading": True, "ipRestrict": True},
     "withdraw"),
    ({"enableWithdrawals": False, "enableSpotAndMarginTrading": False, "ipRestrict": True},
     "cannot trade"),
    ({"enableWithdrawals": False, "enableSpotAndMarginTrading": True, "ipRestrict": False},
     "trusted IPs"),
])
def test_live_refuses_a_key_that_can_do_more_or_less_than_trade(restrictions, refused) -> None:
    a = _adapter(Venue(restrictions), ExecutionMode.LIVE)
    with pytest.raises(UnsafeCredentials, match=refused):
        a.check_credentials()


@pytest.mark.fast
def test_live_accepts_a_trade_only_key_held_to_an_address() -> None:
    a = _adapter(Venue({"enableWithdrawals": False, "enableSpotAndMarginTrading": True,
                        "ipRestrict": True}), ExecutionMode.LIVE)
    a.check_credentials()


@pytest.mark.fast
def test_demo_does_not_ask_for_key_restrictions() -> None:
    """Demo keys answer for a simulated account; the real-money checks are for LIVE."""
    a = _adapter(Venue({"enableWithdrawals": True}))
    a.check_credentials()
