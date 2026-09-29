#!/usr/bin/env python
"""Run RESULT-015's rule forward in the simulator, beside holding BTC. Fake capital.

    set -a && . secrets/api.env && set +a
    .venv/bin/python scripts/run_btc_trend.py            # runs until stopped
    .venv/bin/python scripts/run_btc_trend.py --once     # one tick, for a check

Two books on Binance spot prices, each $10,000 of fake capital:

* **BTC above SMA 150**: long while the last closed day is above its 150-day average,
  in USDT otherwise; decided once per UTC day.
* **Hold BTC**: bought on its first tick, never sold, the reference.

Both are sized at the active `max_position_pct_equity` (25% by default), so they compare
like for like. Spot costs as in PREREG-015: 0.10% fee, 1.5 bp slippage. A manual exit
closes the position; the SMA book stays flat until its signal next changes.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np

from tradelab.config import get_settings
from tradelab.execution.adapters import SimulatedAdapter
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.intent import IntentLog
from tradelab.execution.types import AccountState, ExecutionMode, Fill, Instrument, Order, Side
from tradelab.orchestration.forward import Snapshot, Store
from tradelab.risk.limits import RiskEngine
from tradelab.strategy import btc_trend

REST = "https://api.binance.com"
SYMBOL = "BTCUSDT"
CAPITAL = Decimal(10_000)
STATE_DIR = Path("data/forward/btc-trend")
IMPL = Path("tradelab/strategy/btc_trend.py")
TICK_S = 300
POLL_S = 5
FEE = Decimal("0.001")
HALF_SPREAD_BPS = Decimal("1.5")
#: Below the cap, so the slipped fill price cannot carry the notional over it.
CAP_MARGIN = Decimal("0.995")


def instrument(client: httpx.Client) -> Instrument:
    info = client.get(f"{REST}/api/v3/exchangeInfo", params={"symbol": SYMBOL}).json()
    f = {x["filterType"]: x for x in info["symbols"][0]["filters"]}
    return Instrument(SYMBOL, Decimal(f["PRICE_FILTER"]["tickSize"]),
                      Decimal(f["LOT_SIZE"]["stepSize"]),
                      Decimal(f["NOTIONAL"]["minNotional"]))


def closed_days(client: httpx.Client, now: datetime) -> tuple[str, np.ndarray]:
    """The last closed UTC day and the closes of the 200 days up to it."""
    rows = client.get(f"{REST}/api/v3/klines", params={
        "symbol": SYMBOL, "interval": "1d", "limit": 201}).json()
    stop = int(now.timestamp() * 1000)
    closed = [r for r in rows if int(r[6]) < stop]
    last = datetime.fromtimestamp(int(closed[-1][0]) / 1000, UTC).strftime("%Y-%m-%d")
    return last, np.array([float(r[4]) for r in closed])


def ticker(client: httpx.Client) -> Decimal:
    return Decimal(client.get(f"{REST}/api/v3/ticker/price",
                              params={"symbol": SYMBOL}).json()["price"])


@dataclass
class Book:
    name: str
    label: str
    registration: str
    state: dict = field(default_factory=dict)

    def start(self, store: Store, inst: Instrument, limits_id, limits) -> None:
        self.dir = STATE_DIR / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.limits = limits
        strategy = store.ensure_strategy(self.name, IMPL)
        self.run_id = store.ensure_run(strategy, self.label, {
            "registration": self.registration, "purpose": "forward validation",
            "capital_is_fake": True, "prices": "binance spot"}, CAPITAL, limits_id)
        path = self.dir / "state.json"
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.state = raw.get("state", {})
        self.adapter = SimulatedAdapter(instruments={SYMBOL: inst}, prices={},
                                        half_spread_bps=HALF_SPREAD_BPS, taker_fee=FEE)
        self.adapter._fills.extend(
            Fill(asset=f["asset"], side=Side(f["side"]), quantity=Decimal(f["quantity"]),
                 price=Decimal(f["price"]), fee=Decimal(f["fee"]),
                 at=datetime.fromisoformat(f["at"]), client_order_id=f["client_order_id"],
                 venue_trade_id=f.get("venue_trade_id", "")) for f in raw.get("fills", []))
        self.adapter._held.update({k: Decimal(v) for k, v in raw.get("held", {}).items()})
        account = AccountState(equity=CAPITAL,
                               peak_equity=Decimal(raw.get("peak", CAPITAL)),
                               day_start_equity=CAPITAL)
        self.engine = ExecutionEngine(run_id=str(self.run_id)[:8],
                                      mode=ExecutionMode.SIMULATOR, adapter=self.adapter,
                                      risk=RiskEngine(limits, account),
                                      instruments={SYMBOL: inst},
                                      intents=IntentLog.load(self.dir / "intents.jsonl"))
        divergences = self.engine.recover(datetime.now(UTC))
        print(f"{self.name}: run {self.run_id} resumed, divergences {divergences or 'none'}",
              flush=True)
        if divergences:
            store.halt(self.run_id, f"reconciliation at restart: {divergences}")
            raise SystemExit(1)

    @property
    def held(self) -> Decimal:
        p = self.engine.account.positions.get(SYMBOL)
        return p.quantity if p else Decimal(0)

    def equity(self, price: Decimal) -> Decimal:
        p = self.engine.account.positions.get(SYMBOL)
        return CAPITAL + ((p.realised_pnl + p.unrealised(price)) if p else Decimal(0))

    def trade_to(self, target: Decimal, price: Decimal, now: datetime) -> None:
        """Buy or sell to `target` BTC; a sale to zero is reduce-only."""
        self.adapter.prices[SYMBOL] = price
        delta = target - self.held
        if delta == 0 or abs(delta) * price < self.engine.instruments[SYMBOL].min_viable(price):
            return
        order = Order(SYMBOL, Side.BUY if delta > 0 else Side.SELL, abs(delta),
                      reduce_only=delta < 0 and target == 0)
        decision = self.engine.submit(order, price, {SYMBOL: price}, now, mid_price=price)
        self.engine.apply_fills(now)
        if not decision.allowed:
            print(f"  {self.name}: refused, {decision.breach}: {decision.detail}", flush=True)

    def full(self, price: Decimal) -> Decimal:
        cap = self.limits.max_position_pct_equity * self.equity(price) * CAP_MARGIN
        return cap / price

    def persist(self, now: datetime, price: Decimal) -> Decimal:
        p = self.engine.account.positions.get(SYMBOL)
        qty = self.held
        row = Snapshot(SYMBOL, qty, p.avg_price if p else Decimal(0),
                       p.unrealised(price) if p else Decimal(0),
                       p.realised_pnl if p else Decimal(0), Decimal(0),
                       "algorithm" if qty else None, None, None)
        equity = self.equity(price)
        self.store.write(self.run_id, now, [row], equity, abs(qty) * price)

        def encode(value):
            return value.isoformat() if isinstance(value, datetime) else str(value)
        payload = {"state": self.state, "fills": [asdict(f) for f in self.adapter.fills],
                   "held": {k: str(v) for k, v in self.adapter._held.items()},
                   "peak": str(self.engine.account.peak_equity)}
        partial = self.dir / "state.partial"
        partial.write_text(json.dumps(payload, default=encode, indent=1))
        partial.replace(self.dir / "state.json")
        return equity

    def close_requested(self, client: httpx.Client) -> bool:
        requests = self.store.pending_closes(self.run_id)
        if not requests:
            return False
        now, price = datetime.now(UTC), ticker(client)
        for request, asset in requests:
            before = self.held
            if asset != SYMBOL or before == 0:
                self.store.answer_close(request, self.run_id, asset, {"result": "nothing open"})
                continue
            self.trade_to(Decimal(0), price, now)
            if self.held != 0:
                self.store.answer_close(request, self.run_id, asset, {"result": "refused"})
                continue
            self.state["manual_flat"] = True
            fill = self.adapter.fills[-1]
            self.store.answer_close(request, self.run_id, asset, {
                "result": "closed", "qty": str(before), "price": str(fill.price),
                "fee": str(fill.fee)})
            print(f"{now:%Y-%m-%d %H:%M:%S} {self.name}: manual exit {before} at {fill.price}",
                  flush=True)
        self.persist(now, price)
        return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = Store(get_settings().database_url)
    limits_id, limits = store.active_limits()
    client = httpx.Client(timeout=30.0)
    inst = instrument(client)
    sma = Book("btc-sma150", "BTC above SMA 150 (RESULT-015)", "PREREG-015")
    hold = Book("hold-btc", "Hold BTC (reference)", "PREREG-015 benchmark")
    for book in (sma, hold):
        book.start(store, inst, limits_id, limits)

    while True:
        for book in (sma, hold):
            book.close_requested(client)
        now = datetime.now(UTC)
        day, closes = closed_days(client, now)
        price = ticker(client)

        signal = btc_trend.long(closes)
        if signal is not None and sma.state.get("day") != day:
            if signal != sma.state.get("signal"):
                sma.state["manual_flat"] = False       # a new signal supersedes a manual exit
            sma.state.update(day=day, signal=signal)
            # Switch only: the study held its position unchanged between signals.
            if signal and sma.held == 0 and not sma.state.get("manual_flat"):
                sma.trade_to(sma.full(price), price, now)
            elif not signal and sma.held != 0:
                sma.trade_to(Decimal(0), price, now)
            print(f"{now:%Y-%m-%d %H:%M} {sma.name}: {day} close {closes[-1]:,.0f} vs SMA "
                  f"{closes[-btc_trend.SMA_DAYS:].mean():,.0f} -> "
                  f"{'long' if signal else 'flat'}", flush=True)
        if not hold.state.get("bought"):
            hold.trade_to(hold.full(price), price, now)
            hold.state["bought"] = hold.held > 0

        for book in (sma, hold):
            book.engine.risk.mark_equity(book.equity(price))
        sma.engine.risk.enforce(now)       # the reference is never stopped: it is the yardstick
        for book in (sma, hold):
            equity = book.persist(now, price)
            print(f"{now:%Y-%m-%d %H:%M} {book.name}: {book.held} BTC, equity {equity:,.2f}",
                  flush=True)
        if args.once:
            return 0
        wake = time.time() + TICK_S - time.time() % TICK_S + 15
        while (left := wake - time.time()) > 0:
            time.sleep(min(POLL_S, left))
            for book in (sma, hold):
                try:
                    book.close_requested(client)
                except httpx.HTTPError as failed:
                    print(f"  manual exit check failed, retrying: {failed}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
