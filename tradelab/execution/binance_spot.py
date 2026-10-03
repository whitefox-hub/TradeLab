"""Binance spot, over the wire. Plan.md §12.1, §14.2, §14.3, §14.4.

TESTNET runs against Binance's Demo Mode (demo-api.binance.com): live-like prices and the
production filters, simulated fills. LIVE runs against api.binance.com. Each has its own
key variables, so an unset one never falls back to the other.

Spot differs from futures where it matters to the books:

* the position is the base asset's balance, so the account must hold no base asset but
  the book's (a dedicated sub-account);
* a commission is charged in whatever asset Binance picks: on a buy usually the base asset
  received. A fill therefore carries the quantity that actually moved and the commission
  in quote terms, so the engine's position equals the venue's balance to the last unit;
* the account's `canWithdraw` describes the account, not the key, so the key's own
  permissions are read from `apiRestrictions`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from tradelab.execution.adapters import Ack, VenueAdapter
from tradelab.execution.binance import (
    TIMESTAMP_DRIFT,
    UNKNOWN_ORDER,
    Credentials,
    Params,
    UnsafeCredentials,
    VenueError,
    _status,
)
from tradelab.execution.types import (
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Side,
)

LIVE_BASE = "https://api.binance.com"
DEMO_BASE = "https://demo-api.binance.com"
DUPLICATE_ORDER = -2010
RECV_WINDOW_MS = 5_000
FILL_WAIT_S = 5.0


@dataclass
class BinanceSpotAdapter(VenueAdapter):
    """Signed REST against Binance spot. Market and limit orders; rounds before sending."""

    credentials: Credentials
    instruments: dict[str, Instrument] = field(default_factory=dict)
    recv_window_ms: int = RECV_WINDOW_MS
    client: httpx.Client | None = None
    rejections: list[Ack] = field(default_factory=list)
    fill_wait_s: float = FILL_WAIT_S
    _assets: dict[str, tuple[str, str]] = field(default_factory=dict)
    _fills: list[Fill] = field(default_factory=list)
    _seen_trades: set[int] = field(default_factory=set)
    _our_orders: dict[str, str] = field(default_factory=dict)
    _clock_offset_ms: int = 0

    @classmethod
    def from_environment(
        cls, mode: ExecutionMode = ExecutionMode.TESTNET, *, symbols: list[str] | None = None
    ) -> BinanceSpotAdapter:
        """Build from the environment, then check the key before trading."""
        prefix = "BINANCE_TESTNET" if mode is ExecutionMode.TESTNET else "BINANCE_SPOT"
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
        return DEMO_BASE if self.mode is ExecutionMode.TESTNET else LIVE_BASE

    @property
    def fills(self) -> list[Fill]:
        return self._fills

    def _http(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(
                base_url=self.base_url, timeout=20.0,
                headers={"X-MBX-APIKEY": self.credentials.api_key},
            )
        return self.client

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._clock_offset_ms

    @staticmethod
    def _read(response: httpx.Response) -> object:
        payload = response.json()
        if isinstance(payload, dict) and int(payload.get("code", 0)) < 0:
            raise VenueError(int(payload["code"]), str(payload.get("msg", "")))
        response.raise_for_status()
        return payload

    def _public(self, path: str, params: Params | None = None) -> object:
        return self._read(self._http().get(path, params=params or {}))

    def _signed(self, method: str, path: str, params: Params | None = None) -> object:
        """One signed request, resynced and resent once if the clock drifted (-1021 is
        refused before the venue reads the request, so the resend cannot double an order)."""
        try:
            return self._send_signed(method, path, params)
        except VenueError as refused:
            if refused.code != TIMESTAMP_DRIFT:
                raise
            self.sync_clock()
            return self._send_signed(method, path, params)

    def _send_signed(self, method: str, path: str, params: Params | None = None) -> object:
        query = {**(params or {}), "timestamp": self._now_ms(),
                 "recvWindow": self.recv_window_ms}
        return self._read(
            self._http().request(method, f"{path}?{self.credentials.sign(query)}"))

    def sync_clock(self) -> int:
        server = self._public("/api/v3/time")
        assert isinstance(server, dict)
        self._clock_offset_ms = int(server["serverTime"]) - int(time.time() * 1000)
        return self._clock_offset_ms

    def load_instruments(self, symbols: list[str] | None = None) -> dict[str, Instrument]:
        """Tick, lot and minimum notional, read from the venue. A market order is held to
        the coarser of LOT_SIZE and MARKET_LOT_SIZE."""
        params = {"symbols": "[" + ",".join(f'"{s}"' for s in symbols) + "]"} if symbols else {}
        info = self._public("/api/v3/exchangeInfo", params)
        assert isinstance(info, dict)
        for entry in info.get("symbols", []):
            if entry.get("status") != "TRADING":
                continue
            symbol = str(entry["symbol"])
            filters = {f["filterType"]: f for f in entry.get("filters", [])}
            step = Decimal(str(filters["LOT_SIZE"]["stepSize"]))
            market = Decimal(str(filters.get("MARKET_LOT_SIZE", {}).get("stepSize", "0")))
            notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
            self.instruments[symbol] = Instrument(
                asset=symbol,
                tick_size=Decimal(str(filters["PRICE_FILTER"]["tickSize"])),
                lot_size=max(step, market),
                min_notional=Decimal(str(notional.get("minNotional", "0"))),
            )
            self._assets[symbol] = (str(entry["baseAsset"]), str(entry["quoteAsset"]))
        if symbols:
            missing = set(symbols) - set(self.instruments)
            if missing:
                raise VenueError(None, f"not trading at this venue: {sorted(missing)}")
        return self.instruments

    def check_credentials(self) -> None:
        """The key must be able to trade. With real money it must not be able to withdraw,
        and must be held to trusted addresses (§14.4)."""
        account = self._signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        assert isinstance(account, dict)
        if not account.get("canTrade"):
            raise UnsafeCredentials("the account cannot trade; nothing can be sent")
        if not self.mode.touches_real_money:
            return
        key = self._signed("GET", "/sapi/v1/account/apiRestrictions")
        assert isinstance(key, dict)
        if key.get("enableWithdrawals"):
            raise UnsafeCredentials(
                "this key can withdraw. Trading needs no withdrawal permission, and a key "
                "that has one makes the worst available mistake irreversible. Create one "
                "without it.")
        if not key.get("enableSpotAndMarginTrading"):
            raise UnsafeCredentials("this key cannot trade spot; enable Spot & Margin "
                                    "Trading on it")
        if not key.get("ipRestrict"):
            raise UnsafeCredentials("this key is not restricted to trusted IPs; restrict "
                                    "it before it trades real money")

    def submit(self, order: Order, at: datetime) -> Ack:
        """Send one order and wait, briefly, for its trades. The venue does not refuse a
        client id that has left the book, so recovery asks `already_executed` instead of
        resending (§14.3)."""
        instrument = self.instruments.get(order.asset)
        if instrument is None:
            return self._reject(order, "no instrument definition; refusing to guess precision")
        quantity = instrument.quantise(order.quantity)
        if quantity <= 0:
            return self._reject(order, f"quantises to zero at lot size {instrument.lot_size}")
        params: dict[str, str | int] = {
            "symbol": order.asset,
            "side": "BUY" if order.side is Side.BUY else "SELL",
            "type": "MARKET" if order.order_type is OrderType.MARKET else "LIMIT",
            "quantity": format(quantity, "f"),
            "newClientOrderId": order.client_order_id,
            "newOrderRespType": "RESULT",
        }
        if order.order_type is OrderType.LIMIT:
            if order.limit_price is None:
                return self._reject(order, "limit order without a price")
            params |= {"price": format(instrument.round_price(order.limit_price), "f"),
                       "timeInForce": "GTC"}
        try:
            response = self._signed("POST", "/api/v3/order", params)
        except VenueError as refused:
            if refused.code == DUPLICATE_ORDER:
                existing = self.already_executed(order.asset, order.client_order_id)
                if existing is not None:
                    return existing
            return self._reject(order, f"{refused.code}: {refused.message}")
        assert isinstance(response, dict)
        venue_order_id = str(response.get("orderId", ""))
        self._our_orders[venue_order_id] = order.client_order_id
        executed = Decimal(str(response.get("executedQty", "0")))
        deadline = time.monotonic() + self.fill_wait_s
        while True:
            self.recover_fills(order.asset, order.client_order_id, venue_order_id)
            if self._traded(venue_order_id) >= executed or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        return Ack(order.client_order_id, _status(response.get("status")), venue_order_id)

    def _traded(self, venue_order_id: str) -> Decimal:
        client = self._our_orders.get(venue_order_id)
        return sum((f.gross for f in self._fills if f.client_order_id == client),
                   Decimal(0)) if client else Decimal(0)

    def _reject(self, order: Order, reason: str) -> Ack:
        ack = Ack(order.client_order_id, OrderStatus.REJECTED, reason=reason)
        self.rejections.append(ack)
        return ack

    def recover_fills(self, asset: str, client_order_id: str, venue_order_id: str) -> int:
        """The trades of one order, by venue order id. On restart, run before `prime`."""
        if not venue_order_id:
            return 0
        self._our_orders[venue_order_id] = client_order_id
        trades = self._signed("GET", "/api/v3/myTrades",
                              {"symbol": asset, "orderId": venue_order_id})
        return self._fold_trades(trades)

    def _fold_trades(self, trades: object) -> int:
        assert isinstance(trades, list)
        added = 0
        for trade in trades:
            trade_id = int(trade["id"])
            if trade_id in self._seen_trades:
                continue
            self._seen_trades.add(trade_id)
            self._fills.append(self._fill(trade))
            added += 1
        return added

    def _fill(self, trade: dict) -> SpotFill:
        """The quantity that moved and the commission in quote terms (module docstring)."""
        symbol = str(trade["symbol"])
        base, quote = self._assets.get(symbol, ("", ""))
        side = Side.BUY if trade["isBuyer"] else Side.SELL
        qty, price = Decimal(str(trade["qty"])), Decimal(str(trade["price"]))
        commission, paid_in = Decimal(str(trade["commission"])), str(trade["commissionAsset"])
        moved = qty
        if paid_in == base:
            fee = commission * price
            moved = qty - commission if side is Side.BUY else qty + commission
        elif paid_in == quote:
            fee = commission
        else:
            fee = commission * self._price_in(paid_in, quote)
        return SpotFill(
            asset=symbol, side=side, quantity=moved, price=price, fee=fee,
            at=datetime.fromtimestamp(int(trade["time"]) / 1000, UTC),
            client_order_id=self._our_orders.get(str(trade.get("orderId", "")), ""),
            venue_trade_id=str(trade["id"]), gross=qty)

    def _price_in(self, asset: str, quote: str) -> Decimal:
        """A third asset's commission (BNB) valued in quote at the venue's last price."""
        try:
            return self.last_price(f"{asset}{quote}")
        except (VenueError, httpx.HTTPError):
            return Decimal(0)

    def prime(self, symbols: list[str] | None = None) -> int:
        """Mark trades already at the venue as seen, without folding them (not ours)."""
        primed = 0
        for symbol in symbols or sorted(self.instruments):
            trades = self._signed("GET", "/api/v3/myTrades", {"symbol": symbol, "limit": 1000})
            assert isinstance(trades, list)
            for trade in trades:
                if int(trade["id"]) not in self._seen_trades:
                    self._seen_trades.add(int(trade["id"]))
                    primed += 1
        return primed

    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        """Has this id been placed? None if the venue has never seen it (§14.3)."""
        if not client_order_id:
            raise ValueError("cannot resolve an empty client order id")
        try:
            found = self._signed("GET", "/api/v3/order",
                                 {"symbol": asset, "origClientOrderId": client_order_id})
        except VenueError as missing:
            if missing.code == UNKNOWN_ORDER:
                return None
            raise
        assert isinstance(found, dict)
        venue_order_id = str(found.get("orderId", ""))
        self._our_orders[venue_order_id] = client_order_id
        return Ack(client_order_id, _status(found.get("status")), venue_order_id)

    def cancel_all(self, at: datetime) -> int:
        del at
        resting = self._signed("GET", "/api/v3/openOrders")
        assert isinstance(resting, list)
        cancelled = 0
        for symbol in sorted({str(o["symbol"]) for o in resting}):
            try:
                self._signed("DELETE", "/api/v3/openOrders", {"symbol": symbol})
                cancelled += 1
            except VenueError:
                continue
        return cancelled

    def balances(self) -> dict[str, Decimal]:
        """Free plus locked, by asset, non-zero only."""
        account = self._signed("GET", "/api/v3/account", {"omitZeroBalances": "true"})
        assert isinstance(account, dict)
        return {str(b["asset"]): Decimal(str(b["free"])) + Decimal(str(b["locked"]))
                for b in account.get("balances", [])
                if Decimal(str(b["free"])) + Decimal(str(b["locked"])) != 0}

    def positions(self) -> dict[str, Decimal]:
        """The base asset held for each instrument: what §14.2 reconciles against."""
        held = self.balances()
        return {symbol: held[base] for symbol, (base, _) in self._assets.items()
                if symbol in self.instruments and held.get(base)}

    def last_price(self, symbol: str) -> Decimal:
        quote = self._public("/api/v3/ticker/price", {"symbol": symbol})
        assert isinstance(quote, dict)
        return Decimal(str(quote["price"]))


@dataclass(frozen=True, slots=True)
class SpotFill(Fill):
    """A spot fill also keeps the executed quantity before a base-asset commission."""

    gross: Decimal = Decimal(0)
