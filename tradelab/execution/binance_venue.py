"""Binance USD-M venue adapter. Plan.md §12.1, §14.3, §14.4.

This is rung 3 and 4 of the ladder, and the reason §12.1 calls testnet
non-optional: an entire family of failures exists only past this boundary and
none of them can be produced by a local simulator.

    -1021  timestamp outside recvWindow (clock drift)
    -1022  invalid signature
    -2010  order would immediately trigger / insufficient margin
    -4164  order notional below the minimum
    -1003  request weight exceeded
    -4003  quantity less than zero after rounding

Every one is handled here by name rather than as a generic failure, because the
operator response differs: clock drift needs an NTP fix, a signature error needs
a key check, and a margin rejection is the strategy asking for something the
account cannot do.
"""

from __future__ import annotations

import hashlib
import hmac
import time
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

#: Venue codes that mean "this order will never work as sent" rather than "try
#: again". Retrying these burns rate limit and the error counter for nothing.
PERMANENT: frozenset[int] = frozenset({-1022, -2010, -4003, -4164, -4131})

#: Codes that indicate an environment fault rather than a bad order. They are
#: worth surfacing separately because the fix is operational, not strategic.
ENVIRONMENTAL: frozenset[int] = frozenset({-1021, -1003})


class VenueError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"binance {code}: {message}")
        self.code = code
        self.message = message

    @property
    def permanent(self) -> bool:
        return self.code in PERMANENT

    @property
    def environmental(self) -> bool:
        return self.code in ENVIRONMENTAL


@dataclass(frozen=True, slots=True)
class Credentials:
    """Separate keys per mode, never shared between testnet and live (§14.4).

    The mode is carried with the key so a live key cannot be handed to a testnet
    adapter or the reverse. That pairing is checked at construction rather than
    trusted, because the failure is silent in one direction and catastrophic in
    the other.
    """

    api_key: str
    api_secret: str
    mode: ExecutionMode

    def __post_init__(self) -> None:
        if self.mode not in (ExecutionMode.TESTNET, ExecutionMode.LIVE):
            raise ValueError(f"credentials are meaningless in {self.mode}")

    def sign(self, params: dict) -> str:
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return f"{query}&signature={signature}"


@dataclass
class BinanceVenue(VenueAdapter):
    """The real thing. Rounds before sending and never after.

    Rounding client-side is not an optimisation. The venue rejects an unrounded
    order, and a system that learns its own tick size only from rejections has
    made the venue its validator — at the cost of the error counter and, in
    LIVE, of the position it failed to open.
    """

    credentials: Credentials
    instruments: dict[str, Instrument] = field(default_factory=dict)
    recv_window_ms: int = 5_000
    client: httpx.Client | None = None
    _fills: list[Fill] = field(default_factory=list)
    _weight_used: int = 0
    #: Difference between our clock and the venue's, learned at startup. Clock
    #: drift is the single most common cause of -1021 and it is silent until an
    #: order is refused.
    _clock_offset_ms: int = 0

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

    def _http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(
                base_url=self.base_url, timeout=10.0,
                headers={"X-MBX-APIKEY": self.credentials.api_key},
            )
        return self.client

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._clock_offset_ms

    def sync_clock(self) -> int:
        """Measure our offset against the venue and carry it on every request.

        Without this, a host whose clock has drifted past `recvWindow` fails
        every signed call with -1021 and the cause is invisible from the error
        alone.
        """
        response = self._http().get("/fapi/v1/time")
        server_ms = int(response.json()["serverTime"])
        self._clock_offset_ms = server_ms - int(time.time() * 1000)
        return self._clock_offset_ms

    def _signed(self, method: str, path: str, params: dict) -> dict:
        params = {**params, "timestamp": self._now_ms(),
                  "recvWindow": self.recv_window_ms}
        response = self._http().request(
            method, f"{path}?{self.credentials.sign(params)}"
        )
        # Weight is reported per response and is the only reliable view of how
        # close the account is to a ban. Tracking it from our own call count
        # undercounts, because weights differ per endpoint.
        for header in ("x-mbx-used-weight-1m", "X-MBX-USED-WEIGHT-1M"):
            if header in response.headers:
                self._weight_used = int(response.headers[header])
                break

        payload = response.json()
        if isinstance(payload, dict) and "code" in payload and payload["code"] < 0:
            raise VenueError(int(payload["code"]), str(payload.get("msg", "")))
        response.raise_for_status()
        return payload

    # -- the adapter interface ----------------------------------------------

    def submit(self, order: Order, at: datetime) -> Ack:
        instrument = self.instruments.get(order.asset)
        if instrument is None:
            return Ack(order.client_order_id, OrderStatus.REJECTED,
                       reason="no instrument definition; refusing to guess precision")

        quantity = instrument.quantise(order.quantity)
        if quantity <= 0:
            return Ack(order.client_order_id, OrderStatus.REJECTED,
                       reason=f"quantises to zero at lot size {instrument.lot_size}")

        params = {
            "symbol": order.asset,
            "side": "BUY" if order.side is Side.BUY else "SELL",
            "type": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "quantity": format(quantity, "f"),
            # The deterministic id becomes the venue's idempotency key, which is
            # what makes a retry after a timeout safe (§14.3).
            "newClientOrderId": order.client_order_id,
        }
        if order.reduce_only:
            params["reduceOnly"] = "true"
        if order.order_type is OrderType.LIMIT:
            if order.limit_price is None:
                return Ack(order.client_order_id, OrderStatus.REJECTED,
                           reason="limit order without a price")
            params["price"] = format(instrument.round_price(order.limit_price), "f")
            params["timeInForce"] = "GTC"

        try:
            payload = self._signed("POST", "/fapi/v1/order", params)
        except VenueError as error:
            return Ack(order.client_order_id, OrderStatus.REJECTED,
                       reason=f"{error.code}: {error.message}")

        status = str(payload.get("status", "NEW"))
        if status == "FILLED":
            self._fills.append(Fill(
                asset=order.asset, side=order.side,
                quantity=Decimal(str(payload.get("executedQty", quantity))),
                price=Decimal(str(payload.get("avgPrice", "0"))),
                fee=Decimal(0),  # from the user data stream, not the order ack
                at=at, client_order_id=order.client_order_id,
            ))
            return Ack(order.client_order_id, OrderStatus.FILLED,
                       str(payload.get("orderId", "")))
        return Ack(order.client_order_id, OrderStatus.ACCEPTED,
                   str(payload.get("orderId", "")))

    def cancel_all(self, at: datetime) -> int:
        cancelled = 0
        for symbol in sorted(self.instruments):
            try:
                self._signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
                cancelled += 1
            except VenueError:
                # One symbol failing must not stop the others. The kill switch
                # cancelling four of five books is far better than none.
                continue
        return cancelled

    def positions(self) -> dict[str, Decimal]:
        payload = self._signed("GET", "/fapi/v2/positionRisk", {})
        return {
            row["symbol"]: Decimal(str(row["positionAmt"]))
            for row in payload
            if Decimal(str(row["positionAmt"])) != 0
        }

    # -- dead man's switch ---------------------------------------------------

    def arm_dead_mans_switch(self, symbol: str, timeout_ms: int = 30_000) -> int:
        """Ask the venue to cancel our orders if we stop talking to it (§14.1).

        The point is that it does not depend on us. A kill switch implemented in
        our own process cannot fire when the failure is our own process, and
        that is the failure most likely to leave orders resting.

        Must be re-armed inside the timeout; the countdown resets on each call.
        """
        payload = self._signed(
            "POST", "/fapi/v1/countdownCancelAll",
            {"symbol": symbol, "countdownTime": timeout_ms},
        )
        return int(payload.get("countdownTime", 0))

    def disarm_dead_mans_switch(self, symbol: str) -> None:
        """Countdown zero cancels the timer, not the orders."""
        self._signed("POST", "/fapi/v1/countdownCancelAll",
                     {"symbol": symbol, "countdownTime": 0})


def heartbeat_due(last_armed: datetime, timeout_ms: int, now: datetime,
                  safety: float = 0.5) -> bool:
    """Whether the switch needs re-arming, with margin.

    Re-arming at the last moment means a single slow request cancels the book.
    The default fires at half the window, so one missed beat is survivable and
    two are not — which is the intended behaviour.
    """
    elapsed_ms = (now - last_armed).total_seconds() * 1000
    return elapsed_ms >= timeout_ms * safety


def utc_now() -> datetime:
    return datetime.now(UTC)
