#!/usr/bin/env python
"""The BTC ladder book: in the simulator on Binance spot prices, or on a Binance venue.

    set -a && . secrets/api.env && set +a
    .venv/bin/python scripts/run_btc_ladder.py               # the simulator, until stopped
    .venv/bin/python scripts/run_btc_ladder.py --venue demo  # Binance Demo Mode
    .venv/bin/python scripts/run_btc_ladder.py --once        # one check, for a test

`--venue demo` sends real orders to Binance's Demo Mode (BINANCE_TESTNET_* keys), with its
own run, state directory and intent log; `--venue live` is refused until the risk limits are
decided for a real account. On a venue, the account must hold no BTC but the book's.

The rule is `tradelab.strategy.btc_ladder`; its numbers are the `btc_ladder` setting on the
Parameters screen, read every minute. A change ends the run and starts a new one, so a run
is only ever under one configuration: in the simulator from the budget, on a venue carrying
the parts held (they are real BTC there) and the cash, moved by any budget change. The
budget (the setting's `budget`) grows with every realised profit; it sits inside a $10,000
fake account so the parts stay within the active risk limits. The price is checked every 10
seconds; the screen is written every 5 minutes and on every trade. Close sells every part at
market, even at a loss, and pauses buying 24 h. The operator can also buy one part or sell
one part by hand; those are marked manual on the part, the sale, the order's intent and the
audit trail.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from tradelab.config import get_settings
from tradelab.execution.adapters import SimulatedAdapter, VenueAdapter
from tradelab.execution.binance_spot import DEMO_BASE, BinanceSpotAdapter
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.intent import IntentLog
from tradelab.execution.types import AccountState, ExecutionMode, Fill, Instrument, Order, Side
from tradelab.orchestration.forward import Snapshot, Store
from tradelab.risk.limits import RiskEngine
from tradelab.strategy import btc_ladder as lad

REST = "https://api.binance.com"
SYMBOL = "BTCUSDT"
CAPITAL = Decimal(10_000)
LABEL = "BTC ladder"
STATE_DIR = Path("data/forward/btc-ladder")
MODE = ExecutionMode.SIMULATOR
VENUES = {
    "sim": (ExecutionMode.SIMULATOR, "BTC ladder", Path("data/forward/btc-ladder"), REST),
    "demo": (ExecutionMode.TESTNET, "BTC ladder (demo)", Path("data/forward/btc-ladder-demo"),
             DEMO_BASE),
    "live": (ExecutionMode.LIVE, "BTC ladder (live)", Path("data/forward/btc-ladder-live"), REST),
}


def hold_the_book(state_dir: Path):
    """An exclusive lock on the book's state directory, held for the life of the process.

    Two processes on one book each keep their own parts in memory and would each act on
    them: two buys for one decision, two sales of one part. The kernel drops the lock when
    the process dies however it dies, so a crash never leaves the book locked."""
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = (state_dir / ".lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another process already runs the book in {state_dir}") from None
    return handle


def database_url(settings) -> str:
    """Real money in the live database, everything else in the laboratory's; never crossed."""
    return settings.live_database_url if MODE is ExecutionMode.LIVE else settings.database_url
IMPL = Path("tradelab/strategy/btc_ladder.py")
POLL_S = 10
SNAPSHOT_S = 300
CONFIG_S = 60
SETTING = "btc_ladder"
FEE = Decimal("0.001")
HALF_SPREAD_BPS = Decimal("1.5")
SLIP = float(HALF_SPREAD_BPS) / 10_000


def instrument(client: httpx.Client) -> Instrument:
    info = client.get(f"{REST}/api/v3/exchangeInfo", params={"symbol": SYMBOL}).json()
    f = {x["filterType"]: x for x in info["symbols"][0]["filters"]}
    return Instrument(SYMBOL, Decimal(f["PRICE_FILTER"]["tickSize"]),
                      Decimal(f["LOT_SIZE"]["stepSize"]), Decimal(f["NOTIONAL"]["minNotional"]))


def ticker(client: httpx.Client) -> Decimal:
    """The price the book decides on: the venue's own, so decisions and fills agree."""
    return Decimal(client.get(f"{REST}/api/v3/ticker/price",
                              params={"symbol": SYMBOL}).json()["price"])


def configure(venue: str) -> None:
    """Point the module at one venue: its mode, run label, state directory and prices."""
    global MODE, LABEL, STATE_DIR, REST
    MODE, LABEL, STATE_DIR, REST = VENUES[venue]


class Book:
    def __init__(self, store: Store, inst: Instrument, cfg: lad.Config,
                 adapter: VenueAdapter | None = None, carry: bool = False) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.cfg = cfg
        limits_id, self.limits = store.active_limits()
        strategy = store.ensure_strategy("btc-ladder", IMPL)
        self.run_id = store.ensure_run(strategy, LABEL, {
            "rule": f"{cfg.parts} parts, a new one every {cfg.step:.2%} lower or after each "
                    f"{cfg.reentry:.2%} correction at least {cfg.step:.2%} from every part "
                    f"held; each sells on a {cfg.trail:.2%} pullback once at its "
                    "time-scaled minimum, at its cap at the latest; never at a loss",
            "config": asdict(cfg),
            "budget": cfg.budget, "capital_is_fake": True, "mode": str(MODE),
            "prices": REST},
            CAPITAL, limits_id, str(MODE))
        path = STATE_DIR / "state.json"
        raw = json.loads(path.read_text()) if path.exists() else {}
        s = raw.get("state", {})
        self.cash = float(s.get("cash", cfg.budget))
        self.parts = [lad.Part(p["price"], p["quantity"], p["spent"],
                               datetime.fromisoformat(p["opened"]), p.get("peak"))
                      for p in s.get("parts", [])]
        self.armed = bool(s.get("armed", True))
        self.high = s.get("high")
        self.paused_until = s.get("paused_until")
        self.day = s.get("day")
        self.trades = int(s.get("trades", 0))
        self.manual = set(s.get("manual", []))
        self.sales = list(s.get("sales", []))
        if adapter is None:
            adapter = SimulatedAdapter(instruments={SYMBOL: inst}, prices={},
                                       half_spread_bps=HALF_SPREAD_BPS, taker_fee=FEE)
            adapter._fills.extend(
                Fill(asset=f["asset"], side=Side(f["side"]), quantity=Decimal(f["quantity"]),
                     price=Decimal(f["price"]), fee=Decimal(f["fee"]),
                     at=datetime.fromisoformat(f["at"]), client_order_id=f["client_order_id"],
                     venue_trade_id=f.get("venue_trade_id", "")) for f in raw.get("fills", []))
            adapter._held.update({k: Decimal(v) for k, v in raw.get("held", {}).items()})
        self.adapter = adapter
        account = AccountState(equity=CAPITAL, peak_equity=Decimal(raw.get("peak", CAPITAL)),
                               day_start_equity=Decimal(raw.get("day_start", CAPITAL)))
        self.engine = ExecutionEngine(run_id=str(self.run_id)[:8], mode=MODE,
                                      adapter=self.adapter, risk=RiskEngine(self.limits, account),
                                      instruments={SYMBOL: inst},
                                      intents=IntentLog.load(STATE_DIR / "intents.jsonl"))
        divergences = self.engine.recover(datetime.now(UTC))
        held = sum(p.quantity for p in self.parts)
        if s.get("config") and s["config"] != asdict(cfg):
            if not carry:
                raise ConfigChanged
            self.cash = max(0.0, self.cash + cfg.budget - s["config"]["budget"])
            print(f"configuration changed from {s['config']}: {len(self.parts)} parts carried, "
                  f"cash {self.cash:,.2f}", flush=True)
        print(f"run {self.run_id} resumed, divergences {divergences or 'none'}, {cfg}, "
              f"{len(self.parts)} parts ({held:.8f} BTC), cash {self.cash:,.2f}", flush=True)
        if divergences:
            store.halt(self.run_id, f"reconciliation at restart: {divergences}")
            raise SystemExit(1)

    def equity(self, price: Decimal) -> Decimal:
        p = self.engine.account.positions.get(SYMBOL)
        return CAPITAL + ((p.realised_pnl + p.unrealised(price)) if p else Decimal(0))

    def order(self, side: Side, quantity: float, price: Decimal, now: datetime,
              note: str = "") -> Fill | None:
        """One market order. Its trades, however many, come back as one fill: the quantity
        that moved, its average price and its fees in USDT."""
        if isinstance(self.adapter, SimulatedAdapter):
            self.adapter.prices[SYMBOL] = price
        before = len(self.adapter.fills)
        decision = self.engine.submit(Order(SYMBOL, side, Decimal(str(quantity)),
                                            reduce_only=side is Side.SELL),
                                      price, {SYMBOL: price}, now, mid_price=price, note=note)
        self.engine.apply_fills(now)
        new = self.adapter.fills[before:]
        if not decision.allowed or not new:
            print(f"  {side.value} refused: {decision.breach}: {decision.detail}", flush=True)
            return None
        moved = sum((f.quantity for f in new), Decimal(0))
        return Fill(SYMBOL, side, moved, sum((f.quantity * f.price for f in new), Decimal(0))
                    / moved, sum((f.fee for f in new), Decimal(0)), new[-1].at,
                    new[-1].client_order_id)

    def held(self) -> float:
        p = self.engine.account.positions.get(SYMBOL)
        return float(p.quantity) if p else 0.0

    def sell(self, part: lad.Part, price: Decimal, now: datetime, why: str) -> bool:
        """Sell one part. `why`: cap, pullback or manual.

        On a venue a buy's commission is taken in BTC, so what arrives is a little over a
        whole number of lots. A part is only its whole lots, so every decision is on what a
        sale can deliver and none is at a loss by rounding; the remainders build up outside
        the parts, and a sale also takes the whole lots they add up to."""
        dust = max(0.0, self.held() - sum(p.quantity for p in self.parts))
        fill = self.order(Side.SELL, part.quantity + dust, price, now, why)
        if fill is None:
            return False
        got = float(fill.quantity * fill.price - fill.fee)
        self.cash += got
        self.parts.remove(part)
        self.trades += 1
        days = (now - part.opened).total_seconds() / 86_400
        entry = "manual" if part.opened.isoformat() in self.manual else "rule"
        self.manual.discard(part.opened.isoformat())
        self.sales.append({"bought_at": part.price, "sold_at": float(fill.price),
                           "opened": part.opened.isoformat(), "closed": now.isoformat(),
                           "days": round(days, 2), "spent": round(part.spent, 2),
                           "got": round(got, 2), "gain": got / part.spent - 1,
                           "entry": entry, "exit": why})
        print(f"{now:%Y-%m-%d %H:%M:%S} SELL part bought at {part.price:,.2f} after {days:.1f} d "
              f"at {fill.price}: {got - part.spent:+.2f} ({got / part.spent - 1:+.2%}) "
              f"[{entry} entry, {why}]", flush=True)
        if not self.parts:
            self.armed, self.high = False, float(fill.price)
        return True

    def track(self, px: float) -> None:
        """The high since the last buy or full exit, and whether it has corrected REENTRY."""
        self.high = max(self.high or px, px)
        if not self.armed and px <= self.high * (1 - self.cfg.reentry):
            self.armed = True

    def step(self, price: Decimal, now: datetime) -> bool:
        """One check of every part and of the entry. True if it traded."""
        traded = False
        px = float(price)
        for part in list(self.parts):
            if lad.exit_now(part, px, now, float(FEE), SLIP, self.cfg):
                gain = lad.net(part, px, float(FEE), SLIP)
                assert gain >= 0, "the ladder would sell at a loss"
                traded |= self.sell(part, price, now, "cap" if gain >= lad.cap(
                    (now - part.opened).total_seconds() / 86_400) else "pullback")
        self.track(px)
        paused = self.paused_until and now.isoformat() < self.paused_until
        if not paused and lad.buy_now(self.parts, px, self.armed, self.cfg):
            traded |= self.buy(price, now, manual=False) is None
        return traded

    def buy(self, price: Decimal, now: datetime, manual: bool) -> str | None:
        """Buy one part at market. None if bought, else why not."""
        if len(self.parts) >= self.cfg.parts:
            return "all parts are held"
        px = float(price)
        size = lad.part_size(self.cash, self.parts, self.cfg)
        if size < 10:
            return f"a part would be ${size:,.2f}, under the $10 the venue accepts"
        quantity = size / (px * (1 + SLIP)) / (1 + float(FEE))
        fill = self.order(Side.BUY, quantity, price, now, "manual" if manual else "rule")
        if fill is None:
            return "refused by the risk limits; see the book's log"
        spent = float(fill.quantity * fill.price + fill.fee)
        self.cash -= spent
        sellable = self.engine.instruments[SYMBOL].quantise(fill.quantity)
        self.parts.append(lad.Part(float(fill.price), float(sellable), spent, now))
        if manual:
            self.manual.add(now.isoformat())
        self.armed, self.high = False, float(fill.price)
        lowest = min(p.price for p in self.parts)
        print(f"{now:%Y-%m-%d %H:%M:%S} BUY{' (manual)' if manual else ''} part "
              f"{len(self.parts)}/{self.cfg.parts} at {fill.price} (${spent:,.2f}); next part "
              f"at or below {lowest * (1 - self.cfg.step):,.2f}, or after a "
              f"{self.cfg.reentry:.1%} correction from the high", flush=True)
        return None

    def manual_requested(self, client: httpx.Client) -> bool:
        """The operator's manual buys and sells of one part, answered in the audit trail. A
        manual sale may realise a loss; the screen showed the gain before it was confirmed."""
        requests = self.store.pending_manual(self.run_id)
        if not requests:
            return False
        now, price = datetime.now(UTC), ticker(client)
        for request, action, payload in requests:
            if action == "ladder.buy_request":
                refused = self.buy(price, now, manual=True)
                outcome = ({"result": "refused", "why": refused} if refused else
                           {"result": "bought", "price": str(self.parts[-1].price),
                            "part": now.isoformat()})
            else:
                part = next((p for p in self.parts
                             if p.opened.isoformat() == payload.get("part")), None)
                if part is None:
                    outcome = {"result": "refused", "why": "that part is no longer held"}
                else:
                    sold = self.sell(part, price, now, "manual")
                    outcome = ({"result": "sold", "price": str(self.sales[-1]["sold_at"]),
                                "gain": f"{self.sales[-1]['gain']:.4f}"}
                               if sold else {"result": "refused",
                                             "why": "refused by the risk limits"})
            self.store.answer_manual(request, self.run_id, outcome)
            print(f"{now:%Y-%m-%d %H:%M:%S} manual {action}: {outcome}", flush=True)
        self.persist(now, price)
        self.publish(price, now)
        return True

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

    def status(self, price: Decimal, now: datetime) -> dict:
        """What the screen shows, computed here so the API only reads it (§20.1)."""
        px, fee = float(price), float(FEE)
        parts = []
        for x in sorted(self.parts, key=lambda q: -q.price):
            days = (now - x.opened).total_seconds() / 86_400
            floor, top = lad.minimum(days), lad.cap(days)
            at = lambda g, x=x: x.spent * (1 + g) / (x.quantity * (1 - SLIP) * (1 - fee))  # noqa: E731
            parts.append({
                "bought_at": x.price, "opened": x.opened.isoformat(), "days": round(days, 2),
                "spent": round(x.spent, 2),
                "value": round(x.quantity * px * (1 - SLIP) * (1 - fee), 2),
                "gain": lad.net(x, px, fee, SLIP), "minimum": floor, "minimum_price": at(floor),
                "cap": top, "cap_price": at(top), "peak": x.peak,
                "manual": x.opened.isoformat() in self.manual,
                "sells_at": max(x.peak * (1 - self.cfg.trail), at(floor)) if x.peak else None})
        full = len(self.parts) >= self.cfg.parts
        cheapest = min((x.price for x in self.parts), default=None)
        paused = bool(self.paused_until and now.isoformat() < self.paused_until)
        return {
            "as_of": now.isoformat(), "run_id": str(self.run_id), "price": px,
            "config": asdict(self.cfg), "cash": round(self.cash, 2),
            "next_part_size": round(lad.part_size(self.cash, self.parts, self.cfg), 2),
            "invested": round(sum(x.spent for x in self.parts), 2),
            "parts_held": len(self.parts), "realised_sales": self.trades,
            "high": self.high, "armed": self.armed, "paused_until": self.paused_until,
            "correction_price": None if self.high is None else self.high * (1 - self.cfg.reentry),
            "next_down_price": None if full or cheapest is None
            else cheapest * (1 - self.cfg.step),
            "full": full, "paused": paused, "parts": parts,
            "sales": self.sales[::-1][:20]}

    def publish(self, price: Decimal, now: datetime) -> None:
        partial = STATE_DIR / "status.partial"
        partial.write_text(json.dumps(self.status(price, now), indent=1))
        partial.replace(STATE_DIR / "status.json")

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
        state = {"config": asdict(self.cfg), "cash": self.cash, "armed": self.armed,
                 "high": self.high, "paused_until": self.paused_until, "day": self.day,
                 "trades": self.trades, "manual": sorted(self.manual), "sales": self.sales,
                 "parts": [{**asdict(x), "opened": x.opened.isoformat()} for x in self.parts]}
        held = self.adapter.positions() if isinstance(self.adapter, SimulatedAdapter) else {}
        payload = {"state": state, "fills": [asdict(f) for f in self.adapter.fills],
                   "held": {k: str(v) for k, v in held.items()},
                   "peak": str(self.engine.account.peak_equity),
                   "day_start": str(self.engine.account.day_start_equity)}
        partial = STATE_DIR / "state.partial"
        partial.write_text(json.dumps(payload, default=encode, indent=1))
        partial.replace(STATE_DIR / "state.json")


class ConfigChanged(Exception):
    """The saved state was made under other numbers than the ones now set."""


def start(store: Store, inst: Instrument, adapter: VenueAdapter | None = None) -> Book:
    """The book under the numbers now set. If they differ from the saved state's, the run
    is ended and a new one starts: one run, one configuration. In the simulator it starts
    from the budget; on a venue it carries the parts, which are real BTC there."""
    cfg = lad.Config.from_setting(store.setting(SETTING))
    try:
        return Book(store, inst, cfg, adapter)
    except ConfigChanged:
        if adapter is not None:
            old = json.loads((STATE_DIR / "state.json").read_text())["state"]["config"]
            book_run = store.ensure_run(store.ensure_strategy("btc-ladder", IMPL), LABEL, {},
                                        CAPITAL, store.active_limits()[0], str(MODE))
            store.end(book_run, f"configuration changed from {old} to {asdict(cfg)}; "
                                "parts carried to the next run")
            return Book(store, inst, cfg, adapter, carry=True)
        old = json.loads((STATE_DIR / "state.json").read_text())["state"]["config"]
        book_run = store.ensure_run(store.ensure_strategy("btc-ladder", IMPL), LABEL, {}, CAPITAL,
                                    store.active_limits()[0])
        store.end(book_run, f"configuration changed from {old} to {asdict(cfg)}")
        for f in STATE_DIR.iterdir():
            f.unlink()
        print(f"configuration changed: run {book_run} ended, starting under {cfg}", flush=True)
        return Book(store, inst, cfg)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--venue", choices=VENUES, default="sim")
    args = parser.parse_args()
    if args.venue == "live":
        raise SystemExit("live is not enabled: the laboratory's risk limits were set for a "
                         "$10,000 fake account and would stop a real $2,000 one at its first "
                         "correction, and the live database has none yet. Decide the real "
                         "account's limits and create them there first.")
    configure(args.venue)
    lock = hold_the_book(STATE_DIR)  # noqa: F841 - held until the process exits
    client = httpx.Client(timeout=30.0)
    store = Store(database_url(get_settings()))
    adapter = None
    if MODE is not ExecutionMode.SIMULATOR:
        adapter = BinanceSpotAdapter.from_environment(MODE, symbols=[SYMBOL])
        inst = adapter.instruments[SYMBOL]
    else:
        inst = instrument(client)
    book = start(store, inst, adapter)
    last_snapshot = last_config = 0.0
    while True:
        try:
            if time.time() - last_config >= CONFIG_S:
                last_config = time.time()
                if lad.Config.from_setting(store.setting(SETTING)) != book.cfg:
                    book.persist(datetime.now(UTC), ticker(client))
                    book = start(store, inst, adapter)
            book.close_requested(client)
            book.manual_requested(client)
            now, price = datetime.now(UTC), ticker(client)
            today = now.strftime("%Y-%m-%d")
            if book.day != today:
                book.engine.account.day_start_equity = book.equity(price)
                book.day = today
            traded = book.step(price, now)
            book.publish(price, now)
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
