#!/usr/bin/env python
"""The BTC +1% book, in the simulator, on Binance spot prices. Fake capital.

    set -a && . secrets/api.env && set +a
    .venv/bin/python scripts/run_btc_plus_one.py          # runs until stopped
    .venv/bin/python scripts/run_btc_plus_one.py --once   # one check, for a test

The rule (`tradelab.strategy.btc_plus_one`): buy BTC with the book's whole budget, sell as
soon as the sale nets +1% after the fee and slippage, buy again. Never sell at a loss.
The budget starts at $1,000 and grows with every realised profit. The account is $10,000 of
fake capital, so the position stays inside the active risk limits.

The price is checked every 10 seconds; the screen is written every 5 minutes and on every
trade. The Close button sells at market, even at a loss, and pauses buying for 24 hours.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from tradelab.config import get_settings
from tradelab.execution.adapters import SimulatedAdapter
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.intent import IntentLog
from tradelab.execution.types import AccountState, ExecutionMode, Fill, Instrument, Order, Side
from tradelab.orchestration.forward import Snapshot, Store
from tradelab.risk.limits import RiskEngine
from tradelab.strategy import btc_plus_one as rule

REST = "https://api.binance.com"
SYMBOL = "BTCUSDT"
CAPITAL = Decimal(10_000)
BUDGET = Decimal(1_000)
LABEL = "BTC +1%"
STATE_DIR = Path("data/forward/btc-plus-one")
IMPL = Path("tradelab/strategy/btc_plus_one.py")
POLL_S = 10
SNAPSHOT_S = 300
FEE = Decimal("0.001")
HALF_SPREAD_BPS = Decimal("1.5")
#: Below the position cap, so the slipped fill cannot carry the notional over it.
CAP_MARGIN = Decimal("0.995")


def instrument(client: httpx.Client) -> Instrument:
    info = client.get(f"{REST}/api/v3/exchangeInfo", params={"symbol": SYMBOL}).json()
    f = {x["filterType"]: x for x in info["symbols"][0]["filters"]}
    return Instrument(SYMBOL, Decimal(f["PRICE_FILTER"]["tickSize"]),
                      Decimal(f["LOT_SIZE"]["stepSize"]), Decimal(f["NOTIONAL"]["minNotional"]))


def ticker(client: httpx.Client) -> Decimal:
    return Decimal(client.get(f"{REST}/api/v3/ticker/price",
                              params={"symbol": SYMBOL}).json()["price"])


class Book:
    def __init__(self, store: Store, inst: Instrument) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.store = store
        limits_id, self.limits = store.active_limits()
        strategy = store.ensure_strategy("btc-plus-one", IMPL)
        self.run_id = store.ensure_run(strategy, LABEL, {
            "rule": "buy BTC, sell at +1% net, buy again; never sell at a loss",
            "budget": str(BUDGET), "capital_is_fake": True, "prices": "binance spot"},
            CAPITAL, limits_id)
        path = STATE_DIR / "state.json"
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.state = raw.get("state", {"budget": str(BUDGET), "spent": "0", "trades": 0})
        self.adapter = SimulatedAdapter(instruments={SYMBOL: inst}, prices={},
                                        half_spread_bps=HALF_SPREAD_BPS, taker_fee=FEE)
        self.adapter._fills.extend(
            Fill(asset=f["asset"], side=Side(f["side"]), quantity=Decimal(f["quantity"]),
                 price=Decimal(f["price"]), fee=Decimal(f["fee"]),
                 at=datetime.fromisoformat(f["at"]), client_order_id=f["client_order_id"],
                 venue_trade_id=f.get("venue_trade_id", "")) for f in raw.get("fills", []))
        self.adapter._held.update({k: Decimal(v) for k, v in raw.get("held", {}).items()})
        account = AccountState(equity=CAPITAL, peak_equity=Decimal(raw.get("peak", CAPITAL)),
                               day_start_equity=CAPITAL)
        self.engine = ExecutionEngine(run_id=str(self.run_id)[:8], mode=ExecutionMode.SIMULATOR,
                                      adapter=self.adapter, risk=RiskEngine(self.limits, account),
                                      instruments={SYMBOL: inst},
                                      intents=IntentLog.load(STATE_DIR / "intents.jsonl"))
        divergences = self.engine.recover(datetime.now(UTC))
        print(f"run {self.run_id} resumed, divergences {divergences or 'none'}, "
              f"budget {self.state['budget']}, held {self.held}", flush=True)
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

    def order(self, side: Side, quantity: Decimal, price: Decimal, now: datetime) -> Fill | None:
        self.adapter.prices[SYMBOL] = price
        before = len(self.adapter.fills)
        decision = self.engine.submit(Order(SYMBOL, side, quantity,
                                            reduce_only=side is Side.SELL),
                                      price, {SYMBOL: price}, now, mid_price=price)
        self.engine.apply_fills(now)
        if not decision.allowed or len(self.adapter.fills) == before:
            print(f"  {side.value} refused: {decision.breach}: {decision.detail}", flush=True)
            return None
        return self.adapter.fills[-1]

    def step(self, price: Decimal, now: datetime) -> bool:
        """One check of the rule. True if it traded."""
        fee, slip = float(FEE), float(HALF_SPREAD_BPS) / 10_000
        if self.held > 0:
            spent = float(self.state["spent"])
            if not rule.should_sell(spent, float(self.held), float(price), fee, slip):
                return False
            fill = self.order(Side.SELL, self.held, price, now)
            if fill is None:
                return False
            got = fill.quantity * fill.price - fill.fee
            profit = got - Decimal(self.state["spent"])
            assert profit >= 0, "the rule sold at a loss"
            self.state.update(budget=str(Decimal(self.state["budget"]) + profit), spent="0",
                              trades=self.state["trades"] + 1)
            print(f"{now:%Y-%m-%d %H:%M:%S} SELL {fill.quantity} at {fill.price}: "
                  f"+{profit:.2f}, budget {self.state['budget']}", flush=True)
            return True
        if self.state.get("paused_until") and now.isoformat() < self.state["paused_until"]:
            return False
        budget = min(Decimal(self.state["budget"]),
                     self.limits.max_position_pct_equity * self.equity(price) * CAP_MARGIN)
        quantity = budget / (price * (1 + HALF_SPREAD_BPS / 10_000)) / (1 + FEE)
        fill = self.order(Side.BUY, quantity, price, now)
        if fill is None:
            return False
        self.state["spent"] = str(fill.quantity * fill.price + fill.fee)
        target = rule.target_price(float(self.state["spent"]), float(fill.quantity), fee, slip)
        print(f"{now:%Y-%m-%d %H:%M:%S} BUY {fill.quantity} at {fill.price} "
              f"(${float(self.state['spent']):,.2f}); sells at {target:,.2f} or above", flush=True)
        return True

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
            fill = self.order(Side.SELL, before, price, now)
            if fill is None:
                self.store.answer_close(request, self.run_id, asset, {"result": "refused"})
                continue
            got = fill.quantity * fill.price - fill.fee
            profit = got - Decimal(self.state["spent"])
            self.state.update(budget=str(Decimal(self.state["budget"]) + profit), spent="0",
                              paused_until=(now + timedelta(hours=24)).isoformat())
            self.store.answer_close(request, self.run_id, asset, {
                "result": "closed", "qty": str(before), "price": str(fill.price),
                "fee": str(fill.fee), "profit": str(profit)})
            print(f"{now:%Y-%m-%d %H:%M:%S} manual exit {before} at {fill.price}: {profit:+.2f}; "
                  "buying paused 24 h", flush=True)
        self.persist(now, price)
        return True

    def persist(self, now: datetime, price: Decimal) -> None:
        p = self.engine.account.positions.get(SYMBOL)
        qty = self.held
        row = Snapshot(SYMBOL, qty, p.avg_price if p else Decimal(0),
                       p.unrealised(price) if p else Decimal(0),
                       p.realised_pnl if p else Decimal(0), Decimal(0),
                       "algorithm" if qty else None, None, None)
        self.engine.risk.mark_equity(self.equity(price))
        self.store.write(self.run_id, now, [row], self.equity(price), abs(qty) * price)

        def encode(value):
            return value.isoformat() if isinstance(value, datetime) else str(value)
        payload = {"state": self.state, "fills": [asdict(f) for f in self.adapter.fills],
                   "held": {k: str(v) for k, v in self.adapter._held.items()},
                   "peak": str(self.engine.account.peak_equity)}
        partial = STATE_DIR / "state.partial"
        partial.write_text(json.dumps(payload, default=encode, indent=1))
        partial.replace(STATE_DIR / "state.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    client = httpx.Client(timeout=30.0)
    book = Book(Store(get_settings().database_url), instrument(client))
    last_snapshot = 0.0
    while True:
        try:
            book.close_requested(client)
            now, price = datetime.now(UTC), ticker(client)
            traded = book.step(price, now)
            if traded or time.time() - last_snapshot >= SNAPSHOT_S:
                book.engine.risk.enforce(now)
                book.persist(now, price)
                last_snapshot = time.time()
                print(f"{now:%Y-%m-%d %H:%M} price {price:,.2f}, held {book.held} BTC, "
                      f"budget {book.state['budget']}, equity {book.equity(price):,.2f}",
                      flush=True)
        except httpx.HTTPError as failed:
            print(f"  price unavailable, retrying: {failed}", flush=True)
        if args.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
