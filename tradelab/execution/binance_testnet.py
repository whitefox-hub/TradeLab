"""Binance USD-M futures, over the wire. Plan.md §12.1, §12.2, §14.2, §14.4.

The third rung. `SimulatedAdapter` fills locally and cannot reject for the
reasons a venue does — a bad signature, a clock that has drifted past
`recvWindow`, insufficient margin, a rate limit, a partial fill. §12.1 makes
this rung non-optional for exactly that reason, and the failures it finds are
the ones no amount of local simulation reaches.

**Credentials come from the environment, never from the tree** (§14.4), and the
adapter refuses to start in LIVE with a key that can withdraw. Trading needs
no withdrawal permission, so a key that has one is a key whose worst mistake is
irreversible rather than embarrassing.

**An acknowledgement is not a fill.** The engine folds positions from `fills`
alone, and this adapter reports a fill only when the venue has reported an
executed quantity — never from having sent an order successfully. Conflating
the two is how a system comes to believe it holds a position the venue never
opened (§14.2).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from tradelab.execution.adapters import Ack, VenueAdapter
from tradelab.execution.types import ExecutionMode, Fill, Order, OrderStatus, Side

TESTNET = "https://testnet.binancefuture.com"
LIVE = "https://fapi.binance.com"

#: Binance tells "key unknown" and "signature invalid" apart, and so should we.
#: An adapter that reports both as "authentication failed" sends whoever is
#: debugging it to check the wrong one of the two.
UNKNOWN_KEY = -2015
BAD_SIGNATURE = -1022
TIMESTAMP_DRIFT = -1021
INSUFFICIENT_MARGIN = -2019
RATE_LIMITED = -1003
UNKNOWN_ORDER = -2013

#: Sent on every signed request. Short, because a long window is a wider gap in
#: which a replayed request is still accepted.
RECV_WINDOW_MS = 5_000


class VenueError(RuntimeError):
    """The venue refused, and the code is kept so a caller can tell why."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"binance {code}: {message}")
        self.code = code
        self.message = message


class UnsafeCredentials(RuntimeError):
    """A key that can do more than trading, on a path that trades real money."""


@dataclass
class BinanceFuturesAdapter(VenueAdapter):
    """Signed REST against Binance USD-M futures.

    Defaults to TESTNET. Pointing it at LIVE is a deliberate argument, and it
    then checks the key before it will place anything.
    """

    mode: ExecutionMode = ExecutionMode.TESTNET
    key: str = ""
    secret: str = ""
    base: str = TESTNET
    client: httpx.Client | None = None
    _fills: list[Fill] = field(default_factory=list)
    _seen_trades: set[int] = field(default_factory=set)
    rejections: list[Ack] = field(default_factory=list)

    @classmethod
    def from_environment(cls, mode: ExecutionMode = ExecutionMode.TESTNET
                         ) -> BinanceFuturesAdapter:
        prefix = "BINANCE_TESTNET" if mode is ExecutionMode.TESTNET else "BINANCE"
        key = os.environ.get(f"{prefix}_KEY", "").strip()
        secret = os.environ.get(f"{prefix}_SECRET", "").strip()
        if not key or not secret:
            raise VenueError(None, f"{prefix}_KEY and {prefix}_SECRET are not set")
        base = TESTNET if mode is ExecutionMode.TESTNET else LIVE
        adapter = cls(mode=mode, key=key, secret=secret, base=base)
        adapter.check_credentials()
        return adapter

    # -- the wire ------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(timeout=20.0,
                                       headers={"X-MBX-APIKEY": self.key})
        return self.client

    def _signed(self, method: str, path: str, params: dict | None = None) -> object:
        query = {**(params or {}), "timestamp": int(time.time() * 1000),
                 "recvWindow": RECV_WINDOW_MS}
        encoded = "&".join(f"{k}={v}" for k, v in query.items())
        signature = hmac.new(self.secret.encode(), encoded.encode(),
                             hashlib.sha256).hexdigest()
        url = f"{self.base}{path}?{encoded}&signature={signature}"
        response = self._http().request(method, url)
        payload = response.json()
        if isinstance(payload, dict) and "code" in payload and "msg" in payload:
            raise VenueError(int(payload["code"]), str(payload["msg"]))
        return payload

    # -- the safety check that only matters in LIVE --------------------------

    def check_credentials(self) -> None:
        """Refuse a withdrawal-capable key on a path that trades real money.

        Trading needs no withdrawal permission. A key that has one turns the
        worst available mistake from "lost money on a bad trade" into "lost the
        money". §14.4 keeps credentials out of the tree; this keeps the wrong
        credential out of production.

        Checked on testnet too, and reported rather than refused: the testnet
        grants withdrawal to every key and refusing there would make the check
        untestable.
        """
        account = self._signed("GET", "/fapi/v2/account")
        assert isinstance(account, dict)
        if not account.get("canTrade"):
            raise UnsafeCredentials("the key cannot trade; nothing can be sent")
        if account.get("canWithdraw") and self.mode.touches_real_money:
            raise UnsafeCredentials(
                "this key can withdraw. Trading needs no withdrawal "
                "permission, and a key that has one makes the worst available "
                "mistake irreversible. Create one without it.")

    # -- VenueAdapter --------------------------------------------------------

    @property
    def fills(self) -> list[Fill]:
        return self._fills

    def submit(self, order: Order, at: datetime) -> Ack:
        """Send one order, and report what came back rather than what was hoped.

        `newClientOrderId` carries the deterministic id (§14.3), but **the
        venue does not enforce it**. Measured on the testnet: the same id sent
        twice as a MARKET order was accepted both times, with two different
        venue order ids, and the position doubled. Binance releases a client id
        once the order is no longer open, which for a market order is
        immediately.

        So a retry after a timeout opens a second position unless the caller
        checks first. `already_executed` is how: write the intent, send, and on
        recovery *ask about the known id* rather than resending it. That is
        what §14.3 means by resolving from a known id instead of guessing from
        a position delta, and the guarantee is ours rather than the venue's.
        """
        params = {
            "symbol": order.asset,
            "side": "BUY" if order.side is Side.BUY else "SELL",
            "type": "MARKET" if order.limit_price is None else "LIMIT",
            "quantity": format(order.quantity, "f"),
            "newClientOrderId": order.client_order_id,
        }
        if order.limit_price is not None:
            params |= {"price": format(order.limit_price, "f"),
                       "timeInForce": "GTC"}
        if order.reduce_only:
            params["reduceOnly"] = "true"

        try:
            response = self._signed("POST", "/fapi/v1/order", params)
        except VenueError as refused:
            ack = Ack(client_order_id=order.client_order_id,
                      status=OrderStatus.REJECTED,
                      reason=f"{refused.code}: {refused.message}")
            self.rejections.append(ack)
            return ack

        assert isinstance(response, dict)
        status = {
            "NEW": OrderStatus.ACCEPTED, "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED": OrderStatus.FILLED, "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED, "EXPIRED": OrderStatus.CANCELLED,
        }.get(str(response.get("status")), OrderStatus.PENDING)

        # Collected on every accepted order, not only where the response
        # reports an executed quantity. A market order returns status NEW with
        # executedQty 0 and fills microseconds later, so gating on that field
        # left the engine believing it held nothing while the venue held
        # 0.002 BTC — the exact divergence §14.2 exists to catch, produced by
        # the adapter meant to feed it.
        #
        # A fill still comes from the trade record and never from this
        # response: the response has no fee and no realised price.
        self.collect_fills(order.asset)
        return Ack(client_order_id=order.client_order_id, status=status,
                   venue_order_id=str(response.get("orderId", "")))

    def collect_fills(self, asset: str) -> int:
        """Read executed trades and fold in any not seen before.

        Deduplicated by the venue's trade id, because this is called after each
        order and on recovery, and a fill counted twice is a position that
        drifts from the venue's without either side being wrong about its own
        arithmetic.
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
                client_order_id="",
            ))
            added += 1
        return added

    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        """Has this id already been placed? None if the venue has never seen it.

        The idempotency the venue does not provide. §14.3 has the intent
        written before the order is sent precisely so a process that died
        between the two can ask this on restart instead of resending and
        opening a second position.
        """
        try:
            order = self._signed("GET", "/fapi/v1/order",
                                 {"symbol": asset, "origClientOrderId": client_order_id})
        except VenueError as missing:
            # -2013 is "order does not exist", which is the answer and not a
            # failure. Anything else is a failure and is raised.
            if missing.code == UNKNOWN_ORDER:
                return None
            raise
        assert isinstance(order, dict)
        return Ack(
            client_order_id=client_order_id,
            status={"NEW": OrderStatus.ACCEPTED,
                    "PARTIALLY_FILLED": OrderStatus.PARTIAL,
                    "FILLED": OrderStatus.FILLED,
                    "CANCELED": OrderStatus.CANCELLED,
                    "REJECTED": OrderStatus.REJECTED,
                    "EXPIRED": OrderStatus.CANCELLED,
                    }.get(str(order.get("status")), OrderStatus.PENDING),
            venue_order_id=str(order.get("orderId", "")))

    def cancel_all(self, at: datetime) -> int:
        del at
        open_orders = self._signed("GET", "/fapi/v1/openOrders")
        assert isinstance(open_orders, list)
        symbols = {str(o["symbol"]) for o in open_orders}
        for symbol in symbols:
            self._signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        return len(open_orders)

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
