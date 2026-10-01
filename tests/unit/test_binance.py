"""The Binance adapter. Plan.md §12.1, §14.1, §14.2, §14.3, §14.4.

Two files used to stand here: one written against mocks before credentials
existed, and one written against the sandbox after they did. They disagreed,
and the mocked one was wrong — it asserted an idempotency guarantee the venue
does not give, using a handler that answered FILLED to whatever arrived. They
are one file now, for the same reason the adapters are one module: a tested
half and a true half that are different objects test nothing.

Every fixture below is a response the venue actually sent, down to the fields
that made the original code wrong.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from tradelab.execution.binance import (
    LIVE_BASE,
    TESTNET_BASE,
    UNKNOWN_ORDER,
    BinanceFuturesAdapter,
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

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
BTC = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(50))
TESTNET = Credentials("key", "secret", ExecutionMode.TESTNET)

MARKET_ACK = {"orderId": 28605172275, "status": "NEW", "executedQty": "0",
              "avgPrice": "0.00", "symbol": "BTCUSDT"}
TRADE = {"id": 4227901, "symbol": "BTCUSDT", "qty": "0.002", "price": "84419.10",
         "commission": "0.06753528", "buyer": True, "time": 1790474093000}


def _adapter(handler, credentials: Credentials = TESTNET,
             instruments: dict | None = None) -> BinanceFuturesAdapter:
    adapter = BinanceFuturesAdapter(
        credentials=credentials,
        instruments={"BTCUSDT": BTC} if instruments is None else dict(instruments),
    )
    adapter.client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=adapter.base_url,
        headers={"X-MBX-APIKEY": credentials.api_key},
    )
    return adapter


def _routed(order_response, trades: list | None = None):
    """A handler that answers the order endpoint and the trade endpoint apart.

    `submit` reads trades after every accepted order now, so a handler that
    answers everything the same way no longer models the venue at all.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if "userTrades" in request.url.path:
            return httpx.Response(200, json=trades or [])
        return httpx.Response(200, json=order_response,
                              headers={"x-mbx-used-weight-1m": "12"})
    return handler


def _seen(store: dict, order_response=MARKET_ACK, trades: list | None = None):
    """Records the query of every non-trade request into `store`."""
    inner = _routed(order_response, trades)

    def handler(request: httpx.Request) -> httpx.Response:
        if "userTrades" not in request.url.path:
            store.update(parse_qs(request.url.query.decode()))
        return inner(request)
    return handler


@pytest.mark.fast
def test_credentials_carry_their_mode_and_refuse_the_wrong_one() -> None:
    """A live key handed to a simulator is silent; the reverse is catastrophic."""
    with pytest.raises(ValueError, match="meaningless"):
        Credentials("k", "s", ExecutionMode.SIMULATOR)
    assert Credentials("k", "s", ExecutionMode.LIVE).mode is ExecutionMode.LIVE


@pytest.mark.fast
def test_testnet_and_live_never_share_a_base_url() -> None:
    assert _adapter(_routed(MARKET_ACK)).base_url == TESTNET_BASE
    live = BinanceFuturesAdapter(credentials=Credentials("k", "s", ExecutionMode.LIVE))
    assert live.base_url == LIVE_BASE
    assert TESTNET_BASE != LIVE_BASE


@pytest.mark.fast
def test_the_signature_is_hmac_sha256_over_the_query() -> None:
    params = {"symbol": "BTCUSDT", "timestamp": 1790000000000}
    body, _, signature = TESTNET.sign(params).rpartition("&signature=")
    assert signature == hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest()


@pytest.mark.fast
def test_every_signed_request_carries_a_recv_window() -> None:
    """Without it the venue applies its own default and -1021 becomes likelier."""
    seen: dict = {}
    _adapter(_seen(seen)).submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 1), NOW)
    assert seen["recvWindow"] == ["5000"]
    assert "timestamp" in seen


@pytest.mark.fast
def test_clock_offset_is_applied_to_the_timestamp() -> None:
    """Drift is the commonest cause of -1021 and is invisible until an order is
    refused."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 9_999_999_999_999})
        return _routed(MARKET_ACK)(request)

    adapter = _adapter(handler)
    assert adapter.sync_clock() != 0
    assert adapter._now_ms() > 9_000_000_000_000


@pytest.mark.fast
def test_request_weight_is_read_from_the_response() -> None:
    """Counting our own calls undercounts: weights differ per endpoint."""
    adapter = _adapter(_routed(MARKET_ACK))
    adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 1), NOW)
    assert adapter.weight_used == 12


@pytest.mark.fast
def test_quantity_is_rounded_before_sending_not_after_rejection() -> None:
    """A system that learns its lot size from rejections has made the venue its
    validator, at the cost of the error counter and the position."""
    seen: dict = {}
    _adapter(_seen(seen)).submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.0179")).with_id("r", 1), NOW)
    assert seen["quantity"] == ["0.017"], "unrounded quantity reached the venue"


@pytest.mark.fast
def test_limit_price_is_rounded_to_the_tick() -> None:
    seen: dict = {}
    _adapter(_seen(seen)).submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.01"), OrderType.LIMIT,
              limit_price=Decimal("80000.17")).with_id("r", 1), NOW)
    assert seen["price"] == ["80000.1"]
    assert seen["timeInForce"] == ["GTC"]


@pytest.mark.fast
def test_an_order_without_an_instrument_is_refused_rather_than_guessed() -> None:
    ack = _adapter(_routed(MARKET_ACK)).submit(
        Order("DOGEUSDT", Side.BUY, Decimal(100)).with_id("r", 1), NOW)
    assert ack.status is OrderStatus.REJECTED
    assert "refusing to guess" in ack.reason


@pytest.mark.fast
def test_the_floors_are_asked_for_rather_than_written_down() -> None:
    """BTCUSDT sat at a minimum notional of 100 in this tree while Binance was
    asking 50, which silently doubled the smallest position any sizing rule
    could take. A number copied by hand is wrong the day the venue moves it and
    wrong immediately if it was mistyped."""
    info = {"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "50"}]},
        {"symbol": "DEADUSDT", "status": "BREAK", "filters": []},
    ]}
    adapter = _adapter(lambda r: httpx.Response(200, json=info), instruments={})
    loaded = adapter.load_instruments()

    assert loaded["BTCUSDT"].min_notional == Decimal(50)
    assert loaded["BTCUSDT"].lot_size == Decimal("0.001")
    assert "DEADUSDT" not in loaded, "a symbol that is not trading is not an instrument"


@pytest.mark.fast
def test_a_symbol_the_venue_does_not_list_is_refused_at_startup() -> None:
    """Better to fail loading than to fail every order for it later."""
    info = {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "50"}]}]}
    adapter = _adapter(lambda r: httpx.Response(200, json=info), instruments={})
    with pytest.raises(VenueError, match="LUNAUSDT"):
        adapter.load_instruments(["BTCUSDT", "LUNAUSDT"])


@pytest.mark.fast
@pytest.mark.parametrize(
    ("code", "message", "permanent", "environmental"),
    [
        (-1021, "Timestamp for this request is outside of the recvWindow", False, True),
        (-1003, "Too many requests", False, True),
        (-1022, "Signature for this request is not valid", True, False),
        (-2010, "Order would immediately trigger", True, False),
        (-4164, "Order's notional must be no smaller than 50", True, False),
        (-1111, "Precision is over the maximum defined for this asset", True, False),
        (-1121, "Invalid symbol", True, False),
        (-2019, "Margin is insufficient.", True, False),
        (-2027, "Exceeded the maximum allowable position at current leverage.",
         True, False),
    ],
)
def test_venue_errors_are_classified_not_lumped_together(
    code: int, message: str, permanent: bool, environmental: bool
) -> None:
    """The operator response differs per code: drift needs the host clock fixed,
    a signature error needs a key check, a margin rejection is the strategy
    asking for something the account cannot do."""
    error = VenueError(code, message)
    assert error.permanent is permanent
    assert error.environmental is environmental


@pytest.mark.fast
def test_a_venue_rejection_becomes_a_rejected_ack_not_an_exception() -> None:
    """The engine counts rejections; an exception would bypass that path."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -2010, "msg": "insufficient margin"})

    adapter = _adapter(handler)
    ack = adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("r", 1), NOW)
    assert ack.status is OrderStatus.REJECTED
    assert "-2010" in ack.reason
    assert adapter.rejections == [ack], "a rejection the operator cannot count"


@pytest.mark.fast
def test_positions_reports_only_open_ones() -> None:
    """The venue returns a row per symbol it has ever known, and a flat position
    is not a held one."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"positions": [
            {"symbol": "BTCUSDT", "positionAmt": "0.005"},
            {"symbol": "ETHUSDT", "positionAmt": "0"},
            {"symbol": "SOLUSDT", "positionAmt": "-3"},
        ]})

    assert _adapter(handler).positions() == {
        "BTCUSDT": Decimal("0.005"), "SOLUSDT": Decimal(-3)}


@pytest.mark.fast
def test_the_price_risk_judges_against_comes_from_the_venue_it_trades_on() -> None:
    """A deviation limit comparing an order against a price from somewhere else
    measures the gap between two venues, not the gap it was written for."""
    adapter = _adapter(lambda r: httpx.Response(200, json={"symbol": "BTCUSDT",
                                                           "price": "84420.90"}))
    assert adapter.last_price("BTCUSDT") == Decimal("84420.90")


@pytest.mark.fast
def test_equity_is_read_from_the_venue_not_tracked() -> None:
    """A running total of our own drifts on every fee and funding payment, and
    a limit written as a fraction of equity only binds if the equity is real."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalMarginBalance": "4987.31",
                                         "positions": []})

    assert _adapter(handler).equity() == Decimal("4987.31")


@pytest.mark.fast
def test_a_market_order_fills_although_its_acknowledgement_reports_nothing() -> None:
    """The defect the sandbox found, as the sandbox found it.

    Fills used to be collected only where `executedQty` was above zero. For a
    market order that field is "0" and the status is NEW, so the branch never
    ran: the venue held 0.002 BTC and the engine, which folds positions from
    fills alone, held nothing. §14.2 exists to detect that divergence, and the
    adapter feeding it was manufacturing it.
    """
    adapter = _adapter(_routed(MARKET_ACK, [TRADE]))
    ack = adapter.submit(
        Order("BTCUSDT", Side.BUY, Decimal("0.002")).with_id("run-1", 3), NOW)

    assert ack.status is OrderStatus.ACCEPTED
    assert len(adapter.fills) == 1, "the acknowledgement was believed over the trade"
    assert adapter.fills[0].quantity == Decimal("0.002")
    assert adapter.fills[0].fee == Decimal("0.06753528"), "a fee no ack carried"


@pytest.mark.fast
def test_a_fill_carries_the_venue_trade_id_so_a_partial_can_be_counted() -> None:
    """One order filling in three pieces makes three trades under one client
    order id. The engine folds each fill once by identity, so without the
    venue's own name for each the last two vanish and the position lands
    quietly smaller than the venue's — the partial-fill failure §16.7 asks
    about."""
    pieces = [{**TRADE, "id": 4227901 + i, "qty": "0.001"} for i in range(3)]
    adapter = _adapter(_routed(MARKET_ACK, pieces))
    adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.003")).with_id("r", 1), NOW)

    assert len({f.identity for f in adapter.fills}) == 3
    assert sum(f.quantity for f in adapter.fills) == Decimal("0.003")


@pytest.mark.fast
def test_a_fill_is_attributed_to_the_order_we_sent_it_under() -> None:
    """The trade record names the venue's order id, not ours. Without the map
    between them a fill reaches the intent log with an empty client order id,
    and the §14.3 audit trail loses the thread at the one place it is for:
    which decision produced which trade."""
    adapter = _adapter(_routed(MARKET_ACK, [{**TRADE, "orderId": 28605172275}]))
    order = Order("BTCUSDT", Side.BUY, Decimal("0.002")).with_id("run-1", 3)
    adapter.submit(order, NOW)

    assert adapter.fills[0].client_order_id == order.client_order_id


@pytest.mark.fast
def test_a_trade_we_did_not_cause_is_left_unattributed() -> None:
    """A manual trade, a liquidation, another process. Saying "we do not know"
    beats attributing it to whatever we happened to send last."""
    adapter = _adapter(_routed(MARKET_ACK, [{**TRADE, "orderId": 999999}]))
    adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.002")).with_id("r", 1), NOW)

    assert adapter.fills[0].client_order_id == ""
    assert adapter.fills[0].identity == "4227901", "still foldable exactly once"


@pytest.mark.fast
def test_resolving_an_empty_id_is_a_caller_error_not_a_venue_one() -> None:
    """The venue answers an empty id with -1102, which is not -2013 and is
    therefore raised — correct, but it reads as the venue misbehaving when it
    is a caller with nothing to ask about."""
    adapter = _adapter(_routed(MARKET_ACK))
    with pytest.raises(ValueError, match="empty client order id"):
        adapter.already_executed("BTCUSDT", "")


@pytest.mark.fast
def test_the_history_already_at_the_venue_is_not_folded_as_ours() -> None:
    """A fresh engine reading the recent trade history takes on everything the
    account did before it started. In the drill that found this the stale
    trades netted to zero, so our position matched the venue's for entirely
    the wrong reason; a run that had ended holding something would have
    started out believing the opposite.

    Reconciliation cannot catch it — reconciliation runs before the first fill
    and this happens after.
    """
    stale = [{**TRADE, "id": 79980030 + i} for i in range(7)]
    fresh = {**TRADE, "id": 79981000}

    served = [stale]

    def handler(request: httpx.Request) -> httpx.Response:
        if "userTrades" in request.url.path:
            return httpx.Response(200, json=served[0])
        return httpx.Response(200, json=MARKET_ACK)

    adapter = _adapter(handler)
    assert adapter.prime(["BTCUSDT"]) == 7
    assert adapter.fills == [], "priming folded what it was meant to skip"

    served[0] = [*stale, fresh]
    adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.002")).with_id("r", 1), NOW)
    assert [f.venue_trade_id for f in adapter.fills] == ["79981000"]


@pytest.mark.fast
def test_the_trades_of_one_order_are_recovered_by_its_venue_id() -> None:
    """After a restart the recent history is primed away, so an order that
    filled while the process was dead has to be asked about by name."""
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(dict(request.url.params))
        return httpx.Response(200, json=[{**TRADE, "orderId": 28605172275}])

    adapter = _adapter(handler)
    assert adapter.recover_fills("BTCUSDT", "run-1-3", "28605172275") == 1
    assert asked[0]["orderId"] == "28605172275"
    assert adapter.fills[0].client_order_id == "run-1-3"
    assert adapter.recover_fills("BTCUSDT", "run-1-3", "28605172275") == 0
    assert adapter.prime(["BTCUSDT"]) == 0, "a recovered trade primed as history"


@pytest.mark.fast
def test_the_same_trade_is_not_folded_twice_across_orders() -> None:
    """Collecting on every order makes the dedupe by venue trade id
    load-bearing rather than decorative."""
    adapter = _adapter(_routed(MARKET_ACK, [TRADE]))
    for i in range(3):
        adapter.submit(Order("BTCUSDT", Side.BUY, Decimal("0.002")).with_id("r", i), NOW)
    assert len(adapter.fills) == 1


@pytest.mark.fast
def test_the_client_order_id_is_sent_and_is_derived_not_drawn() -> None:
    """The id goes out with the order, and the same order always computes the
    same one. That is what §14.3 needs, and it is all this shows."""
    seen: dict = {}
    order = Order("BTCUSDT", Side.BUY, Decimal("0.01")).with_id("run-9", 3)
    _adapter(_seen(seen)).submit(order, NOW)

    assert seen["newClientOrderId"] == [order.client_order_id]
    assert order.client_order_id == Order(
        "BTCUSDT", Side.BUY, Decimal("0.01")).with_id("run-9", 3).client_order_id


@pytest.mark.fast
def test_the_venue_does_not_refuse_a_repeated_client_order_id() -> None:
    """Measured, not assumed: two MARKET orders carrying the identical
    `newClientOrderId` were both accepted, with venue ids 28605172275 and
    28605172284, and the position went to 0.0040.

    Binance frees a client id as soon as the order leaves the book, which for a
    market order is at once. This file used to assert the opposite, with a mock
    that answered FILLED to anything and was never going to disagree.
    """
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "userTrades" in request.url.path:
            return httpx.Response(200, json=[])
        sent.append(request.url.query.decode())
        return httpx.Response(200, json={**MARKET_ACK,
                                         "orderId": 28605172275 + len(sent)})

    adapter = _adapter(handler)
    same = Order("BTCUSDT", Side.BUY, Decimal("0.002")).with_id("run-1", 3)
    first, second = adapter.submit(same, NOW), adapter.submit(same, NOW)

    assert first.status is OrderStatus.ACCEPTED
    assert second.status is OrderStatus.ACCEPTED, "the venue is not the guard"
    assert first.venue_order_id != second.venue_order_id
    assert "does not enforce" in (BinanceFuturesAdapter.submit.__doc__ or ""), \
        "the docstring claims a guarantee again"


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
def test_a_duplicate_id_on_the_book_resolves_to_the_order_already_there() -> None:
    """The other half of the venue's idempotency, measured on the testnet: while
    an order rests, a second one under the same id is refused with -4116.

    That refusal is the answer to a retry, not a failure. Reported as a
    rejection it counted towards `max_consecutive_errors`, so a process retrying
    after a timeout could halt itself over an order that was placed correctly
    the first time — and it said the order was not there when it was.
    """
    resting = Order("BTCUSDT", Side.BUY, Decimal("0.002"), OrderType.LIMIT,
                    limit_price=Decimal("80000")).with_id("run-1", 3)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/fapi/v1/order":
            return httpx.Response(400, json={"code": -4116,
                                             "msg": "ClientOrderId is duplicated."})
        if request.url.params.get("origClientOrderId") == resting.client_order_id:
            return httpx.Response(200, json={"orderId": 1316174295, "status": "NEW"})
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    ack = adapter.submit(resting, NOW)

    assert ack.status is OrderStatus.ACCEPTED
    assert ack.venue_order_id == "1316174295"
    assert adapter.rejections == [], "a placed order counted as a refusal"


@pytest.mark.fast
def test_drift_is_resynced_once_and_the_request_retried() -> None:
    """-1021 means the venue refused the request before reading it, so sending
    it again is safe — once the clock is corrected. Measured: 20 s of drift
    fails every signed call, and nothing recovered without a restart."""
    calls = {"time": 0, "signed": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            calls["time"] += 1
            return httpx.Response(200, json={"serverTime": 1_790_000_000_000})
        calls["signed"] += 1
        if calls["signed"] == 1:
            return httpx.Response(400, json={"code": -1021, "msg": "outside recvWindow"})
        return httpx.Response(200, json={"positions": []})

    assert _adapter(handler).positions() == {}
    assert calls == {"time": 1, "signed": 2}


@pytest.mark.fast
def test_drift_that_survives_a_resync_is_raised_not_looped_on() -> None:
    """A clock the venue still refuses after correcting is a host fault, and
    retrying forever would hide it behind the rate limit."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_790_000_000_000})
        return httpx.Response(400, json={"code": -1021, "msg": "outside recvWindow"})

    with pytest.raises(VenueError) as refused:
        _adapter(handler).positions()
    assert refused.value.code == -1021


@pytest.mark.fast
def test_an_unreadable_answer_is_not_read_as_never_sent() -> None:
    """The dangerous failure: rate limited or signature rejected, read as "no
    such order", answered by resending. Only -2013 means never sent."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": -1003, "msg": "Too many requests."})

    with pytest.raises(VenueError):
        _adapter(handler).already_executed("BTCUSDT", "run-1-3")


@pytest.mark.fast
def test_open_orders_are_read_from_the_venue_by_our_name_for_them() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "orderId": 7,
                                          "clientOrderId": "run-1-3"}])

    assert _adapter(handler).open_orders("BTCUSDT") == ["run-1-3"]
    assert seen["symbol"] == ["BTCUSDT"]


@pytest.mark.fast
def test_cancel_all_keeps_going_when_one_symbol_fails() -> None:
    """Cancelling four books of five is far better than none."""
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/openOrders":
            return httpx.Response(200, json=[{"symbol": s} for s in
                                             ("BTCUSDT", "ETHUSDT", "SOLUSDT")])
        symbol = parse_qs(request.url.query.decode())["symbol"][0]
        tried.append(symbol)
        if symbol == "ETHUSDT":
            return httpx.Response(400, json={"code": -1003, "msg": "banned"})
        return httpx.Response(200, json={"code": 200, "msg": "ok"})

    assert _adapter(handler).cancel_all(NOW) == 2
    assert set(tried) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


@pytest.mark.fast
def test_cancel_all_falls_back_to_known_symbols_when_it_cannot_read_the_book() -> None:
    """The kill switch runs when things are already wrong, so the call that
    lists open orders is exactly the one that may fail. Not knowing what rests
    is a reason to cancel everything we could have opened, not a reason to
    cancel nothing."""
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/openOrders":
            return httpx.Response(418, json={"code": -1003, "msg": "banned"})
        tried.append(parse_qs(request.url.query.decode())["symbol"][0])
        return httpx.Response(200, json={"code": 200, "msg": "ok"})

    adapter = _adapter(handler, instruments={
        "BTCUSDT": BTC,
        "SOLUSDT": Instrument("SOLUSDT", Decimal("0.01"), Decimal("0.1"), Decimal(5))})
    assert adapter.cancel_all(NOW) == 2
    assert set(tried) == {"BTCUSDT", "SOLUSDT"}


@pytest.mark.fast
def test_the_dead_mans_switch_is_armed_at_the_venue() -> None:
    """A kill switch inside our own process cannot fire when our process is the
    failure — which is the case most likely to leave orders resting."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.url.query.decode()))
        return httpx.Response(200, json={"symbol": "BTCUSDT", "countdownTime": "30000"})

    adapter = _adapter(handler)
    assert adapter.arm_dead_mans_switch("BTCUSDT", 30_000) == 30_000
    assert seen["countdownTime"] == ["30000"]

    adapter.disarm_dead_mans_switch("BTCUSDT")
    assert seen["countdownTime"] == ["0"]


@pytest.mark.fast
def test_the_heartbeat_re_arms_with_margin() -> None:
    """Re-arming at the last moment lets one slow request cancel the book."""
    assert not heartbeat_due(NOW, 30_000, NOW + timedelta(seconds=10))
    assert heartbeat_due(NOW, 30_000, NOW + timedelta(seconds=16))
    assert heartbeat_due(NOW, 30_000, NOW + timedelta(seconds=31))
