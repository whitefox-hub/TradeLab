#!/usr/bin/env python
"""The BTC ladder book, in the simulator, on Binance spot prices. Fake capital.

    set -a && . secrets/api.env && set +a
    .venv/bin/python scripts/run_btc_ladder.py          # runs until stopped
    .venv/bin/python scripts/run_btc_ladder.py --once   # one check, for a test

The rule is `tradelab.strategy.btc_ladder`. The budget starts at $1,000 and grows with every
realised profit; it sits inside a $10,000 fake account so ten parts stay within the active
risk limits. The price is checked every 10 seconds; the screen is written every 5 minutes
and on every trade. Close sells every part at market, even at a loss, and pauses buying 24 h.
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
from tradelab.strategy import btc_ladder as lad

REST = "https://api.binance.com"
SYMBOL = "BTCUSDT"
CAPITAL = Decimal(10_000)
BUDGET = 1_000.0
LABEL = "BTC ladder"
STATE_DIR = Path("data/forward/btc-ladder")
IMPL = Path("tradelab/strategy/btc_ladder.py")
POLL_S = 10
SNAPSHOT_S = 300
FEE = Decimal("0.001")
HALF_SPREAD_BPS = Decimal("1.5")
SLIP = float(HALF_SPREAD_BPS) / 10_000


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
        strategy = store.ensure_strategy("btc-ladder", IMPL)
        self.run_id = store.ensure_run(strategy, LABEL, {
            "rule": "ten parts, a new one every 1.5% lower or after each 2% correction at "
                    "least 1.5% from every part held; each sells on a 2% pullback once at "
                    "its time-scaled minimum, at its cap at the latest; re-entry on a 2% "
                    "correction; never at a loss",
            "budget": BUDGET, "capital_is_fake": True, "prices": "binance spot"},
            CAPITAL, limits_id)
        path = STATE_DIR / "state.json"
        raw = json.loads(path.read_text()) if path.exists() else {}
        s = raw.get("state", {})
        self.cash = float(s.get("cash", BUDGET))
        self.parts = [lad.Part(p["price"], p["quantity"], p["spent"],
                               datetime.fromisoformat(p["opened"]), p.get("peak"))
                      for p in s.get("parts", [])]
        self.armed = bool(s.get("armed", True))
        self.high = s.get("high")
        self.paused_until = s.get("paused_until")
        self.day = s.get("day")
        self.trades = int(s.get("trades", 0))
        self.adapter = SimulatedAdapter(instruments={SYMBOL: inst}, prices={},
                                        half_spread_bps=HALF_SPREAD_BPS, taker_fee=FEE)
        self.adapter._fills.extend(
            Fill(asset=f["asset"], side=Side(f["side"]), quantity=Decimal(f["quantity"]),
                 price=Decimal(f["price"]), fee=Decimal(f["fee"]),
                 at=datetime.fromisoformat(f["at"]), client_order_id=f["client_order_id"],
                 venue_trade_id=f.get("venue_trade_id", "")) for f in raw.get("fills", []))
        self.adapter._held.update({k: Decimal(v) for k, v in raw.get("held", {}).items()})
        account = AccountState(equity=CAPITAL, peak_equity=Decimal(raw.get("peak", CAPITAL)),
                               day_start_equity=Decimal(raw.get("day_start", CAPITAL)))
        self.engine = ExecutionEngine(run_id=str(self.run_id)[:8], mode=ExecutionMode.SIMULATOR,
                                      adapter=self.adapter, risk=RiskEngine(self.limits, account),
                                      instruments={SYMBOL: inst},
                                      intents=IntentLog.load(STATE_DIR / "intents.jsonl"))
        divergences = self.engine.recover(datetime.now(UTC))
        held = sum(p.quantity for p in self.parts)
        print(f"run {self.run_id} resumed, divergences {divergences or 'none'}, "
              f"{len(self.parts)} parts ({held:.8f} BTC), cash {self.cash:,.2f}", flush=True)
        if divergences:
            store.halt(self.run_id, f"reconciliation at restart: {divergences}")
            raise SystemExit(1)

    def equity(self, price: Decimal) -> Decimal:
        p = self.engine.account.positions.get(SYMBOL)
        return CAPITAL + ((p.realised_pnl + p.unrealised(price)) if p else Decimal(0))

    def order(self, side: Side, quantity: float, price: Decimal, now: datetime) -> Fill | None:
        self.adapter.prices[SYMBOL] = price
        before = len(self.adapter.fills)
        decision = self.engine.submit(Order(SYMBOL, side, Decimal(str(quantity)),
                                            reduce_only=side is Side.SELL),
                                      price, {SYMBOL: price}, now, mid_price=price)
        self.engine.apply_fills(now)
        if not decision.allowed or len(self.adapter.fills) == before:
            print(f"  {side.value} refused: {decision.breach}: {decision.detail}", flush=True)
            return None
        return self.adapter.fills[-1]

    def sell(self, part: lad.Part, price: Decimal, now: datetime, why: str) -> bool:
        fill = self.order(Side.SELL, part.quantity, price, now)
        if fill is None:
            return False
        got = float(fill.quantity * fill.price - fill.fee)
        self.cash += got
        self.parts.remove(part)
        self.trades += 1
        days = (now - part.opened).total_seconds() / 86_400
        print(f"{now:%Y-%m-%d %H:%M:%S} SELL part bought at {part.price:,.2f} after {days:.1f} d "
              f"at {fill.price}: {got - part.spent:+.2f} ({got / part.spent - 1:+.2%}) [{why}]",
              flush=True)
        if not self.parts:                     # a full exit: wait for a correction to re-enter
            self.armed, self.high = False, float(fill.price)
        return True

    def track(self, px: float) -> None:
        """The high since the last buy or full exit, and whether it has corrected REENTRY."""
        self.high = max(self.high or px, px)
        if not self.armed and px <= self.high * (1 - lad.REENTRY):
            self.armed = True

    def step(self, price: Decimal, now: datetime) -> bool:
        """One check of every part and of the entry. True if it traded."""
        traded = False
        px = float(price)
        for part in list(self.parts):
            if lad.exit_now(part, px, now, float(FEE), SLIP):
                gain = lad.net(part, px, float(FEE), SLIP)
                assert gain >= 0, "the ladder would sell at a loss"
                traded |= self.sell(part, price, now, "cap" if gain >= lad.cap(
                    (now - part.opened).total_seconds() / 86_400) else "pullback")
        self.track(px)
        paused = self.paused_until and now.isoformat() < self.paused_until
        if not paused and lad.buy_now(self.parts, px, self.armed):
            size = lad.part_size(self.cash, self.parts)
            quantity = size / (px * (1 + SLIP)) / (1 + float(FEE))
            fill = self.order(Side.BUY, quantity, price, now) if size >= 10 else None
            if fill is not None:
                spent = float(fill.quantity * fill.price + fill.fee)
                self.cash -= spent
                self.parts.append(lad.Part(float(fill.price), float(fill.quantity), spent, now))
                # The next part up needs a fresh correction from here.
                self.armed, self.high = False, float(fill.price)
                lowest = min(p.price for p in self.parts)
                print(f"{now:%Y-%m-%d %H:%M:%S} BUY part {len(self.parts)}/{lad.PARTS} at "
                      f"{fill.price} (${spent:,.2f}); next part at or below "
                      f"{lowest * (1 - lad.STEP):,.2f}, or after a 2% correction above",
                      flush=True)
                traded = True
        return traded

    def close_requested(self, client: httpx.Client) -> bool:
        requests = self.store.pending_closes(self.run_id)
        if not requests:
            return False
        now, price = datetime.now(UTC), ticker(client)
        for request, asset in requests:
            if asset != SYMBOL or not self.parts:
                self.store.answer_close(request, self.run_id, asset, {"result": "nothing open"})
                continue
            spent, before = sum(p.spent for p in self.parts), self.cash
            sold = [self.sell(p, price, now, "manual") for p in list(self.parts)]
            self.paused_until = (now + timedelta(hours=24)).isoformat()
            self.store.answer_close(request, self.run_id, asset, {
                "result": "closed" if all(sold) else "partly closed", "parts": len(sold),
                "price": str(price), "profit": f"{self.cash - before - spent:.2f}"})
            print(f"{now:%Y-%m-%d %H:%M:%S} manual exit of {len(sold)} parts; buying paused 24 h",
                  flush=True)
        self.persist(now, price)
        return True

    def persist(self, now: datetime, price: Decimal) -> None:
        p = self.engine.account.positions.get(SYMBOL)
        qty = p.quantity if p else Decimal(0)
        row = Snapshot(SYMBOL, qty, p.avg_price if p else Decimal(0),
                       p.unrealised(price) if p else Decimal(0),
                       p.realised_pnl if p else Decimal(0), Decimal(0),
                       "algorithm" if qty else None, None, None)
        self.engine.risk.mark_equity(self.equity(price))
        self.store.write(self.run_id, now, [row], self.equity(price), abs(qty) * price)

        def encode(value):
            return value.isoformat() if isinstance(value, datetime) else str(value)
        state = {"cash": self.cash, "armed": self.armed, "high": self.high,
                 "paused_until": self.paused_until, "day": self.day, "trades": self.trades,
                 "parts": [{**asdict(x), "opened": x.opened.isoformat()} for x in self.parts]}
        payload = {"state": state, "fills": [asdict(f) for f in self.adapter.fills],
                   "held": {k: str(v) for k, v in self.adapter._held.items()},
                   "peak": str(self.engine.account.peak_equity),
                   "day_start": str(self.engine.account.day_start_equity)}
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
            today = now.strftime("%Y-%m-%d")
            if book.day != today:               # max_daily_loss is from the day's opening equity
                book.engine.account.day_start_equity = book.equity(price)
                book.day = today
            traded = book.step(price, now)
            if traded or time.time() - last_snapshot >= SNAPSHOT_S:
                book.engine.risk.enforce(now)
                book.persist(now, price)
                last_snapshot = time.time()
                print(f"{now:%Y-%m-%d %H:%M} price {price:,.2f}, {len(book.parts)} parts, "
                      f"cash {book.cash:,.2f}, equity {book.equity(price):,.2f}", flush=True)
        except httpx.HTTPError as failed:
            print(f"  price unavailable, retrying: {failed}", flush=True)
        if args.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
