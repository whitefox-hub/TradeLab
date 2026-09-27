"""The Binance adapter, against mocked responses. Plan.md §12.1, §14.3, §14.4.

Every case here is a real venue behaviour recorded as a fixture. The integration
test against the sandbox is separate and needs credentials; this file covers the
part that can be got right before they exist — signing, rounding, idempotency,
error classification and the dead man's switch.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from tradelab.execution.binance_venue import (
    LIVE_BASE,
    TESTNET_BASE,
    BinanceVenue,
    Credentials,
    VenueError,
    heartbeat_due,
)
from tradelab.execution.types import (
    ExecutionMode,
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Side,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
BTC = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(50))
TESTNET = Credentials("key", "secret", ExecutionMode.TESTNET)


def _venue(handler, credentials: Credentials = TESTNET) -> BinanceVenue:
    transport = httpx.MockTransport(handler)
    venue = BinanceVenue(credentials=credentials, instruments={"BTCUSDT": BTC})
    venue.client = httpx.Client(
        transport=transport, base_url=venue.base_url,
        headers={"X-MBX-APIKEY": credentials.api_key},
    )
    return venue


def _filled(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "orderId": 123, "status": "FILLED", "executedQty": "0.010",
        "avgPrice": "80000.0",
    }, headers={"x-mbx-used-weight-1m": "12"})


@pytest.mark.fast
def test_credentials_carry_their_mode_and_refuse_the_wrong_one() -> None:
    """A live key handed to a simulator is silent; the reverse is catastrophic."""
    with pytest.raises(ValueError, match="meaningless"):
        Credentials("k", "s", ExecutionMode.SIMULATOR)
    assert Credentials("k", "s", ExecutionMode.LIVE).mode is ExecutionMode.LIVE


@pytest.mark.fast
def test_testnet_and_live_never_share_a_base_url() -> None:
    assert _venue(_filled, TESTNET).base_url == TESTNET_BASE
    live = BinanceVenue(credentials=Credentials("k", "s", ExecutionMode.LIVE))
    assert live.base_url == LIVE_BASE
    assert TESTNET_BASE != LIVE_BASE


@pytest.mark.fast
def test_the_signature_is_hmac_sha256_over_the_query() -> None:
    params = {"symbol": "BTCUSDT", "timestamp": 1790000000000}
    signed = TESTNET.sign(params)
    body, _, signature = signed.rpartition("&signature=")
    expected = hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest()
    assert signature == expected


@pytest.mark.fast
def test_every_signed_request_carries_a_recv_window() -> None:
    """Without it the venue applies its own default and -1021 becomes likelier."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return _filled(request)

    venue = _venue(handler)
    venue.submit(Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 1), NOW)
    assert seen["recvWindow"] == ["5000"]
    assert "timestamp" in seen


@pytest.mark.fast
def test_clock_offset_is_applied_to_the_timestamp() -> None:
    """Clock drift is the commonest cause of -1021 and is invisible until an
    order is refused."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 9_999_999_999_999})
        return _filled(request)

    venue = _venue(handler)
    offset = venue.sync_clock()
    assert offset != 0
    assert venue._now_ms() > 9_000_000_000_000


@pytest.mark.fast
def test_quantity_is_rounded_before_sending_not_after_rejection() -> None:
    """A system that learns its tick size from rejections has made the venue its
    validator, at the cost of the error counter and the position."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return _filled(request)

    venue = _venue(handler)
    venue.submit(Order("BTCUSDT", Side.BUY, Decimal("0.0179")).with_id("r", 1), NOW)
    assert seen["quantity"] == ["0.017"], "unrounded quantity reached the venue"


@pytest.mark.fast
def test_limit_price_is_rounded_to_the_tick() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return _filled(request)

    venue = _venue(handler)
    venue.submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.01"), OrderType.LIMIT,
              limit_price=Decimal("80000.17")).with_id("r", 1),
        NOW,
    )
    assert seen["price"] == ["80000.1"]
    assert seen["timeInForce"] == ["GTC"]


@pytest.mark.fast
def test_the_client_order_id_is_sent_and_is_derived_not_drawn() -> None:
    """The id goes out with the order and the same order always computes the
    same one — which is what §14.3 needs, and is all this shows.

    It was named `..._becomes_the_venue_idempotency_key` and said it was what
    made a retry safe. The testnet says otherwise: two MARKET orders with one
    id were both accepted and the position doubled, because Binance frees a
    client id the moment the order leaves the book. The mock could not have
    caught that — it answered FILLED to whatever arrived. The guard is the
    caller resolving a known id before resending; see
    `BinanceFuturesAdapter.already_executed`.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return _filled(request)

    venue = _venue(handler)
    order = Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("run-9", 3)
    venue.submit(order, NOW)
    assert seen["newClientOrderId"] == [order.client_order_id]
    assert order.client_order_id == Order(
        "BTCUSDT", Side.BUY, Decimal("0.01")
    ).with_id("run-9", 3).client_order_id


@pytest.mark.fast
def test_an_order_without_an_instrument_is_refused_rather_than_guessed() -> None:
    venue = _venue(_filled)
    ack = venue.submit(Order("DOGEUSDT", Side.BUY, Decimal(100)).with_id("r", 1), NOW)
    assert ack.status is OrderStatus.REJECTED
    assert "refusing to guess" in ack.reason


@pytest.mark.fast
@pytest.mark.parametrize(
    ("code", "message", "permanent", "environmental"),
    [
        (-1021, "Timestamp for this request is outside of the recvWindow", False, True),
        (-1003, "Too many requests", False, True),
        (-1022, "Signature for this request is not valid", True, False),
        (-2010, "Order would immediately trigger", True, False),
        (-4164, "Order's notional must be no smaller than 5", True, False),
    ],
)
def test_venue_errors_are_classified_not_lumped_together(
    code: int, message: str, permanent: bool, environmental: bool
) -> None:
    """The operator response differs per code: drift needs NTP, a signature
    error needs a key check, a margin rejection is the strategy overreaching."""
    error = VenueError(code, message)
    assert error.permanent is permanent
    assert error.environmental is environmental


@pytest.mark.fast
def test_a_venue_rejection_becomes_a_rejected_ack_not_an_exception() -> None:
    """The engine counts rejections; an exception would bypass that path."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -2010, "msg": "insufficient margin"})

    ack = _venue(handler).submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 1), NOW
    )
    assert ack.status is OrderStatus.REJECTED
    assert "-2010" in ack.reason


@pytest.mark.fast
def test_request_weight_is_read_from_the_response() -> None:
    """Counting our own calls undercounts: weights differ per endpoint."""
    venue = _venue(_filled)
    venue.submit(Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 1), NOW)
    assert venue.weight_used == 12


@pytest.mark.fast
def test_cancel_all_keeps_going_when_one_symbol_fails() -> None:
    """Cancelling four books of five is far better than none."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = parse_qs(request.url.query.decode())["symbol"][0]
        calls.append(symbol)
        if symbol == "ETHUSDT":
            return httpx.Response(400, json={"code": -1003, "msg": "banned"})
        return httpx.Response(200, json={"code": 200, "msg": "ok"})

    venue = _venue(handler)
    venue.instruments["ETHUSDT"] = Instrument(
        "ETHUSDT", Decimal("0.01"), Decimal("0.001"), Decimal(20))
    venue.instruments["SOLUSDT"] = Instrument(
        "SOLUSDT", Decimal("0.01"), Decimal("0.1"), Decimal(5))

    assert venue.cancel_all(NOW) == 2
    assert set(calls) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


@pytest.mark.fast
def test_positions_reports_only_open_ones() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"symbol": "BTCUSDT", "positionAmt": "0.005"},
            {"symbol": "ETHUSDT", "positionAmt": "0"},
            {"symbol": "SOLUSDT", "positionAmt": "-3"},
        ])

    positions = _venue(handler).positions()
    assert positions == {"BTCUSDT": Decimal("0.005"), "SOLUSDT": Decimal(-3)}


@pytest.mark.fast
def test_the_dead_mans_switch_is_armed_at_the_venue() -> None:
    """A kill switch in our own process cannot fire when our process is the
    failure — which is the case most likely to leave orders resting."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return httpx.Response(200, json={"symbol": "BTCUSDT", "countdownTime": "30000"})

    venue = _venue(handler)
    assert venue.arm_dead_mans_switch("BTCUSDT", 30_000) == 30_000
    assert seen["countdownTime"] == ["30000"]

    venue.disarm_dead_mans_switch("BTCUSDT")
    assert seen["countdownTime"] == ["0"]


@pytest.mark.fast
def test_the_heartbeat_re_arms_with_margin() -> None:
    """Re-arming at the last moment lets one slow request cancel the book."""
    assert not heartbeat_due(NOW, 30_000, NOW + timedelta(seconds=10))
    assert heartbeat_due(NOW, 30_000, NOW + timedelta(seconds=16))
    # One missed beat survivable, two not.
    assert heartbeat_due(NOW, 30_000, NOW + timedelta(seconds=31))
