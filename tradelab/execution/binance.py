"""Binance USD-M futures, over the wire. Plan.md §12.1, §14.1, §14.2, §14.3, §14.4.

Rungs three and four of the ladder, and the reason §12.1 makes testnet
non-optional: a whole family of failures exists only past this boundary and the
local simulator cannot produce any of them.

    -1021  timestamp outside recvWindow (clock drift)
    -1022  invalid signature
    -2013  order does not exist
    -2015  key unknown, or not permitted from this address
    -2019  insufficient margin
    -1003  request weight exceeded
    -1111  precision over what the instrument allows
    -1121  invalid symbol
    -4164  order notional below the minimum

Each is handled by name rather than as a generic failure, because the operator
response differs: drift needs the host clock fixed, a signature error needs the
key checked, a margin rejection is the strategy asking for something the account
cannot do.

**Credentials come from the environment, never from the tree** (§14.4), and the
adapter refuses to start in LIVE with a key that can withdraw. Trading needs no
withdrawal permission, so a key that has one is a key whose worst mistake is
irreversible rather than embarrassing.

**An acknowledgement is not a fill.** Positions are folded from `fills` alone,
and a fill here always comes from a trade record — never from having sent an
order successfully, and never from the order response, which carries no fee and
no realised price. Conflating the two is how a system comes to believe it holds
a position the venue never opened (§14.2).

This module replaces the pair that preceded it: one written against mocks, with
the rounding and the dead man's switch, and one written against the sandbox,
with what the sandbox turned out to say. Neither was whole and keeping both
meant the tested half and the true half were never the same object.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from tradelab.execution.adapters import Ack, VenueAdapter
from tradelab.execution.types import (
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Side,
)

LIVE_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"

#: Binance tells "key unknown" and "signature invalid" apart, and so should we.
#: An adapter reporting both as "authentication failed" sends whoever is
#: debugging it to check the wrong one of the two.
UNKNOWN_KEY = -2015
BAD_SIGNATURE = -1022
TIMESTAMP_DRIFT = -1021
UNKNOWN_ORDER = -2013
INSUFFICIENT_MARGIN = -2019
RATE_LIMITED = -1003
BAD_PRECISION = -1111
UNKNOWN_SYMBOL = -1121
BELOW_MIN_NOTIONAL = -4164

#: Codes meaning "this order will never work as sent" rather than "try again".
#: Retrying one of these burns rate limit and the error counter for nothing.
PERMANENT: frozenset[int] = frozenset({
    BAD_SIGNATURE, INSUFFICIENT_MARGIN, BAD_PRECISION, UNKNOWN_SYMBOL,
    BELOW_MIN_NOTIONAL, -2010, -4003, -4131,
})

#: Codes indicating an environment fault rather than a bad order. Worth
#: separating because the fix is operational, not strategic.
ENVIRONMENTAL: frozenset[int] = frozenset({TIMESTAMP_DRIFT, RATE_LIMITED})

#: Sent on every signed request. Short, because a long window is a wider gap in
#: which a replayed request is still accepted.
RECV_WINDOW_MS = 5_000

#: Everything this adapter sends is a string or a number. Narrower than `dict`
#: so the http client can type-check what reaches it.
Params = Mapping[str, str | int]


class VenueError(RuntimeError):
    """The venue refused, and the code is kept so a caller can tell why."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"binance {code}: {message}")
        self.code = code
        self.message = message

    @property
    def permanent(self) -> bool:
        return self.code in PERMANENT

    @property
    def environmental(self) -> bool:
        return self.code in ENVIRONMENTAL


class UnsafeCredentials(RuntimeError):
    """A key that can do more than trade, on a path that trades real money."""


@dataclass(frozen=True, slots=True)
class Credentials:
    """Separate keys per mode, never shared between testnet and live (§14.4).

    The mode travels with the key so a live key cannot be handed to a testnet
    adapter or the reverse. Checked at construction rather than trusted,
    because the failure is silent in one direction and catastrophic in the
    other.
    """

    api_key: str
    api_secret: str
    mode: ExecutionMode

    def __post_init__(self) -> None:
        if self.mode not in (ExecutionMode.TESTNET, ExecutionMode.LIVE):
            raise ValueError(f"credentials are meaningless in {self.mode}")

    def sign(self, params: Params) -> str:
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return f"{query}&signature={signature}"


@dataclass
class BinanceFuturesAdapter(VenueAdapter):
    """Signed REST against Binance USD-M futures. Rounds before sending.

    Rounding client-side is not an optimisation. The venue rejects an unrounded
    order, and a system that learns its own tick size only from rejections has
    made the venue its validator — at the cost of the error counter and, in
    LIVE, of the position it failed to open.
    """

    credentials: Credentials
    instruments: dict[str, Instrument] = field(default_factory=dict)
    recv_window_ms: int = RECV_WINDOW_MS
    client: httpx.Client | None = None
    rejections: list[Ack] = field(default_factory=list)
    _fills: list[Fill] = field(default_factory=list)
    _seen_trades: set[int] = field(default_factory=set)
    #: Venue order id -> the client order id we sent it under. The trade record
    #: names the order but not our name for it, so without this a fill cannot
    #: be attributed to the decision that caused it and the §14.3 log loses the
    #: thread exactly where it matters.
    _our_orders: dict[str, str] = field(default_factory=dict)
    _weight_used: int = 0
    #: Difference between our clock and the venue's, learned at startup. Drift
    #: is the commonest cause of -1021 and it is silent until an order is
    #: refused.
    _clock_offset_ms: int = 0

    @classmethod
    def from_environment(
        cls, mode: ExecutionMode = ExecutionMode.TESTNET, *, symbols: list[str] | None = None
    ) -> BinanceFuturesAdapter:
        """Build from the environment, then check the key before trading.

        Defaults to TESTNET. Reaching LIVE takes a deliberate argument and a
        different pair of variables, so the two cannot be confused by an unset
        one falling back to the other.
        """
        prefix = "BINANCE_TESTNET" if mode is ExecutionMode.TESTNET else "BINANCE"
        key = os.environ.get(f"{prefix}_KEY", "").strip()
        secret = os.environ.get(f"{prefix}_SECRET", "").strip()
        if not key or not secret:
            raise VenueError(None, f"{prefix}_KEY and {prefix}_SECRET are not set")

        adapter = cls(credentials=Credentials(key, secret, mode))
        adapter.sync_clock()
        adapter.load_instruments(symbols)
        adapter.check_credentials()
        return adapter

    @property
    def mode(self) -> ExecutionMode:  # type: ignore[override]
        return self.credentials.mode

    @property
    def base_url(self) -> str:
        return TESTNET_BASE if self.mode is ExecutionMode.TESTNET else LIVE_BASE

    @property
    def fills(self) -> list[Fill]:
        return self._fills

    @property
    def weight_used(self) -> int:
        return self._weight_used

    # -- the wire ------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(
                base_url=self.base_url, timeout=20.0,
                headers={"X-MBX-APIKEY": self.credentials.api_key},
            )
        return self.client

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._clock_offset_ms

    def _read(self, response: httpx.Response) -> object:
        # Weight is reported per response and is the only reliable view of how
        # close the account is to a ban. Counting our own calls undercounts,
        # because weights differ per endpoint.
        for header in ("x-mbx-used-weight-1m", "X-MBX-USED-WEIGHT-1M"):
            if header in response.headers:
                self._weight_used = int(response.headers[header])
                break
        payload = response.json()
        if isinstance(payload, dict) and int(payload.get("code", 0)) < 0:
            raise VenueError(int(payload["code"]), str(payload.get("msg", "")))
        response.raise_for_status()
        return payload

    def _public(self, path: str,
                params: Params | None = None) -> object:
        return self._read(self._http().get(path, params=params or {}))

    def _signed(self, method: str, path: str,
                params: Params | None = None) -> object:
        query = {**(params or {}), "timestamp": self._now_ms(),
                 "recvWindow": self.recv_window_ms}
        return self._read(
            self._http().request(method, f"{path}?{self.credentials.sign(query)}")
        )

    def sync_clock(self) -> int:
        """Measure our offset against the venue and carry it on every request.

        Without this, a host whose clock has drifted past `recvWindow` fails
        every signed call with -1021 and the cause is invisible from the error.
        """
        server = self._public("/fapi/v1/time")
        assert isinstance(server, dict)
        self._clock_offset_ms = int(server["serverTime"]) - int(time.time() * 1000)
        return self._clock_offset_ms

    def load_instruments(self, symbols: list[str] | None = None) -> dict[str, Instrument]:
        """Read tick size, lot size and minimum notional from the venue.

        Asked rather than written down. A hard-coded floor is wrong the day the
        venue changes it and wrong immediately if it was mistyped — BTCUSDT sat
        at 100 in this tree while Binance was asking 50, which silently doubled
        the smallest position any sizing rule could take.
        """
        info = self._public("/fapi/v1/exchangeInfo")
        assert isinstance(info, dict)
        wanted = set(symbols) if symbols else None
        for entry in info.get("symbols", []):
            symbol = str(entry["symbol"])
            if wanted is not None and symbol not in wanted:
                continue
            if entry.get("status") != "TRADING":
                continue
            filters = {f["filterType"]: f for f in entry.get("filters", [])}
            self.instruments[symbol] = Instrument(
                asset=symbol,
                tick_size=Decimal(str(filters["PRICE_FILTER"]["tickSize"])),
                lot_size=Decimal(str(filters["LOT_SIZE"]["stepSize"])),
                min_notional=Decimal(str(
                    filters.get("MIN_NOTIONAL", {}).get("notional", "0"))),
            )
        if wanted is not None:
            missing = wanted - set(self.instruments)
            if missing:
                raise VenueError(None, f"not trading at this venue: {sorted(missing)}")
        return self.instruments

    # -- the safety check that only matters in LIVE --------------------------

    def check_credentials(self) -> None:
        """Refuse a withdrawal-capable key on a path that trades real money.

        Trading needs no withdrawal permission. A key that has one turns the
        worst available mistake from "lost money on a bad trade" into "lost the
        money". §14.4 keeps credentials out of the tree; this keeps the wrong
        credential out of production.

        Run on testnet too, and reported rather than refused there: the testnet
        grants withdrawal to every key, and refusing would make the check
        untestable on the only rung where it can be exercised safely.
        """
        account = self._signed("GET", "/fapi/v2/account")
        assert isinstance(account, dict)
        if not account.get("canTrade"):
            raise UnsafeCredentials("the key cannot trade; nothing can be sent")
        if account.get("canWithdraw") and self.mode.touches_real_money:
            raise UnsafeCredentials(
                "this key can withdraw. Trading needs no withdrawal permission, "
                "and a key that has one makes the worst available mistake "
                "irreversible. Create one without it.")

    # -- VenueAdapter --------------------------------------------------------

    def submit(self, order: Order, at: datetime) -> Ack:
        """Send one order, and report what came back rather than what was hoped.

        `newClientOrderId` carries the deterministic id (§14.3), but **the venue
        does not enforce it**. Measured on the testnet: the same id sent twice as
        a MARKET order was accepted both times, with two different venue order
        ids, and the position doubled. Binance frees a client id once the order
        leaves the book, which for a market order is at once.

        So a retry after a timeout opens a second position unless the caller
        checks first. `already_executed` is how: write the intent, send, and on
        recovery *ask about the id already written* rather than resending it.
        That is what §14.3 means by resolving from a known id instead of
        guessing from a position delta — and the guarantee is ours, not the
        venue's.
        """
        instrument = self.instruments.get(order.asset)
        if instrument is None:
            return self._reject(order,
                                "no instrument definition; refusing to guess precision")

        quantity = instrument.quantise(order.quantity)
        if quantity <= 0:
            return self._reject(order,
                                f"quantises to zero at lot size {instrument.lot_size}")

        params = {
            "symbol": order.asset,
            "side": "BUY" if order.side is Side.BUY else "SELL",
            "type": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "quantity": format(quantity, "f"),
            "newClientOrderId": order.client_order_id,
        }
        if order.reduce_only:
            params["reduceOnly"] = "true"
        if order.order_type is OrderType.LIMIT:
            if order.limit_price is None:
                return self._reject(order, "limit order without a price")
            params |= {"price": format(instrument.round_price(order.limit_price), "f"),
                       "timeInForce": "GTC"}

        try:
            response = self._signed("POST", "/fapi/v1/order", params)
        except VenueError as refused:
            return self._reject(order, f"{refused.code}: {refused.message}")

        assert isinstance(response, dict)
        venue_order_id = str(response.get("orderId", ""))
        if venue_order_id:
            self._our_orders[venue_order_id] = order.client_order_id

        # Collected on every accepted order, not only where the response reports
        # an executed quantity. A market order comes back status NEW with
        # executedQty 0 and fills microseconds later, so gating on that field
        # left the engine believing it held nothing while the venue held 0.002
        # BTC — the exact divergence §14.2 exists to catch, manufactured by the
        # adapter that feeds it.
        self.collect_fills(order.asset)
        return Ack(client_order_id=order.client_order_id,
                   status=_status(response.get("status")),
                   venue_order_id=venue_order_id)

    def _reject(self, order: Order, reason: str) -> Ack:
        ack = Ack(order.client_order_id, OrderStatus.REJECTED, reason=reason)
        self.rejections.append(ack)
        return ack

    def collect_fills(self, asset: str) -> int:
        """Read executed trades and fold in any not seen before.

        Deduplicated by the venue's trade id, because this runs after every
        order and again on recovery, and a fill counted twice is a position that
        drifts from the venue's without either side being wrong about its own
        arithmetic.

        The trade id travels on the `Fill` as well. One order that fills in
        three pieces produces three trades under one client order id, so the
        engine needs the venue's name for each to count them all.
        """
        trades = self._signed("GET", "/fapi/v1/userTrades",
                              {"symbol": asset, "limit": 100})
        assert isinstance(trades, list)
        added = 0
        for trade in trades:
            trade_id = int(trade["id"])
            if trade_id in self._seen_trades:
                continue
            self._seen_trades.add(trade_id)
            self._fills.append(Fill(
                asset=str(trade["symbol"]),
                side=Side.BUY if trade["buyer"] else Side.SELL,
                quantity=Decimal(str(trade["qty"])),
                price=Decimal(str(trade["price"])),
                fee=Decimal(str(trade["commission"])),
                at=datetime.fromtimestamp(int(trade["time"]) / 1000, UTC),
                # Empty where we did not send the order — a manual trade, a
                # liquidation, another process. Saying so is better than
                # attributing it to whatever we happened to send last.
                client_order_id=self._our_orders.get(str(trade.get("orderId", "")), ""),
                venue_trade_id=str(trade_id),
            ))
            added += 1
        return added

    def prime(self, symbols: list[str] | None = None) -> int:
        """Mark the trades already at the venue as seen, without folding them.

        `collect_fills` reads the recent trade history, and on the first call
        that history is not ours — it belongs to whatever ran before. Folding
        it makes our position the sum of everything the account has done
        lately, which is right only when that sum happens to be zero.

        It did happen to be zero in the drill that found this: the previous run
        had flattened, so seven stale trades netted out and the engine agreed
        with the venue for entirely the wrong reason. A run that had ended
        holding something would have started believing it held the opposite.

        Reconciliation cannot catch it, because reconciliation runs before the
        first fill and this happens after. So: `reconcile()`, then `prime()`,
        then trade.
        """
        primed = 0
        for symbol in symbols or sorted(self.instruments):
            trades = self._signed("GET", "/fapi/v1/userTrades",
                                  {"symbol": symbol, "limit": 100})
            assert isinstance(trades, list)
            for trade in trades:
                if int(trade["id"]) not in self._seen_trades:
                    self._seen_trades.add(int(trade["id"]))
                    primed += 1
        return primed

    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        """Has this id already been placed? None if the venue has never seen it.

        The idempotency the venue does not provide. §14.3 writes the intent
        before the order is sent precisely so a process that died between the
        two can ask this on restart instead of resending and opening a second
        position.
        """
        if not client_order_id:
            # The venue answers an empty id with -1102, which is not -2013 and
            # would therefore be raised — correct, but it reads as a venue
            # fault when it is a caller with nothing to ask about.
            raise ValueError("cannot resolve an empty client order id")
        try:
            found = self._signed(
                "GET", "/fapi/v1/order",
                {"symbol": asset, "origClientOrderId": client_order_id})
        except VenueError as missing:
            # -2013 is "order does not exist", which is the answer and not a
            # failure. Anything else — a rate limit, a bad signature — is a
            # failure, and reading it as "never sent" is how the caller is
            # invited to open the second position this method exists to stop.
            if missing.code == UNKNOWN_ORDER:
                return None
            raise
        assert isinstance(found, dict)
        return Ack(client_order_id=client_order_id,
                   status=_status(found.get("status")),
                   venue_order_id=str(found.get("orderId", "")))

    def cancel_all(self, at: datetime) -> int:
        """Cancel every resting order, and keep going when one symbol fails.

        A kill switch that clears four books of five is far better than one that
        stops at the first refusal (§14.1).
        """
        del at
        try:
            resting = self._signed("GET", "/fapi/v1/openOrders")
        except VenueError:
            # Cannot read what is open, so try every symbol we know instead.
            resting = [{"symbol": s} for s in sorted(self.instruments)]
        assert isinstance(resting, list)
        cancelled = 0
        for symbol in sorted({str(o["symbol"]) for o in resting}):
            try:
                self._signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
                cancelled += 1
            except VenueError:
                continue
        return cancelled

    def positions(self) -> dict[str, Decimal]:
        """What the venue says is held. The only view §14.2 reconciles against.

        Zero-quantity rows are dropped: the venue returns one per symbol it has
        ever known, and a flat position is not a held one.
        """
        account = self._signed("GET", "/fapi/v2/account")
        assert isinstance(account, dict)
        return {str(p["symbol"]): Decimal(str(p["positionAmt"]))
                for p in account.get("positions", [])
                if Decimal(str(p["positionAmt"])) != 0}

    def last_price(self, symbol: str) -> Decimal:
        """The venue's last traded price, for the risk engine to judge against.

        Unsigned, so it costs nothing to ask often. The deviation limit
        compares an order's price against this, which is only meaningful if it
        comes from the venue the order is going to.
        """
        quote = self._public("/fapi/v1/ticker/price", {"symbol": symbol})
        assert isinstance(quote, dict)
        return Decimal(str(quote["price"]))

    def equity(self) -> Decimal:
        """Margin balance as the venue computes it. §14.1 judges against this.

        Read, not tracked. A running total of our own drifts from the venue's
        on every fee and every funding payment, and a limit expressed as a
        fraction of equity is only a limit if the equity is the real one.
        """
        account = self._signed("GET", "/fapi/v2/account")
        assert isinstance(account, dict)
        return Decimal(str(account["totalMarginBalance"]))

    # -- dead man's switch ---------------------------------------------------

    def arm_dead_mans_switch(self, symbol: str, timeout_ms: int = 30_000) -> int:
        """Ask the venue to cancel our orders if we stop talking to it (§14.1).

        The point is that it does not depend on us. A kill switch inside our own
        process cannot fire when the failure *is* our own process, and that is
        the failure most likely to leave orders resting.

        Must be re-armed inside the timeout; the countdown resets on each call.
        """
        payload = self._signed("POST", "/fapi/v1/countdownCancelAll",
                               {"symbol": symbol, "countdownTime": timeout_ms})
        assert isinstance(payload, dict)
        return int(payload.get("countdownTime", 0))

    def disarm_dead_mans_switch(self, symbol: str) -> None:
        """Countdown zero cancels the timer, not the orders."""
        self._signed("POST", "/fapi/v1/countdownCancelAll",
                     {"symbol": symbol, "countdownTime": 0})


def _status(venue_status: object) -> OrderStatus:
    return {
        "NEW": OrderStatus.ACCEPTED,
        "PARTIALLY_FILLED": OrderStatus.PARTIAL,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELLED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.CANCELLED,
    }.get(str(venue_status), OrderStatus.PENDING)


def heartbeat_due(last_armed: datetime, timeout_ms: int, now: datetime,
                  safety: float = 0.5) -> bool:
    """Whether the switch needs re-arming, with margin.

    Re-arming at the last moment means one slow request cancels the book. The
    default fires at half the window, so a single missed beat is survivable and
    two are not — which is the intended behaviour.
    """
    elapsed_ms = (now - last_armed).total_seconds() * 1000
    return elapsed_ms >= timeout_ms * safety


def utc_now() -> datetime:
    return datetime.now(UTC)
