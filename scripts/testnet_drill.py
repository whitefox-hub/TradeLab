#!/usr/bin/env python
"""Run the §16.7 failure drills against Binance testnet with the real `ExecutionEngine`.

Plan.md §12.1, §16.7. Opens real testnet positions; flattens at start and end.

    BINANCE_TESTNET_KEY=... BINANCE_TESTNET_SECRET=... python scripts/testnet_drill.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from tradelab.execution.adapters import Ack, VenueAdapter
from tradelab.execution.binance import BinanceFuturesAdapter, VenueError
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.intent import IntentLog, IntentState
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Order,
    OrderType,
    Side,
)
from tradelab.risk.limits import RiskEngine, RiskLimits

BASKET = ["ADAUSDT", "XRPUSDT", "XLMUSDT", "SUIUSDT", "ALGOUSDT"]
DRILL = "ADAUSDT"

LIMITS = RiskLimits(min_seconds_between_entries=0, max_orders_per_minute=60)


class _Dies(VenueAdapter):
    """The process dying around a send, which no venue can be asked to do."""

    def __init__(self, inner: BinanceFuturesAdapter, *, after_sending: bool) -> None:
        self.inner, self.after_sending, self.mode = inner, after_sending, inner.mode

    def submit(self, order: Order, at: datetime) -> Ack:
        if self.after_sending:
            self.inner.submit(order, at)
        raise ConnectionError("process killed")

    def cancel_all(self, at: datetime) -> int:
        return self.inner.cancel_all(at)

    def positions(self) -> dict[str, Decimal]:
        return self.inner.positions()

    def already_executed(self, asset: str, client_order_id: str) -> Ack | None:
        return self.inner.already_executed(asset, client_order_id)


def heading(number: int, title: str) -> None:
    print(f"\n{'─' * 72}\n  DRILL {number}. {title}\n{'─' * 72}")


def flatten(adapter: BinanceFuturesAdapter, at: datetime) -> None:
    for asset, quantity in adapter.positions().items():
        adapter.submit(
            Order(asset, Side.SELL if quantity > 0 else Side.BUY, abs(quantity),
                  OrderType.MARKET, reduce_only=True
                  ).with_id(f"flatten-{int(at.timestamp())}", 0), at)


def build(adapter: BinanceFuturesAdapter, run_id: str, equity: Decimal) -> ExecutionEngine:
    return ExecutionEngine(
        run_id=run_id, mode=ExecutionMode.TESTNET, adapter=adapter,
        risk=RiskEngine(LIMITS, AccountState(equity=equity, peak_equity=equity,
                                             day_start_equity=equity)),
        instruments=adapter.instruments,
    )


def main() -> int:
    now = datetime.now(UTC)
    adapter = BinanceFuturesAdapter.from_environment(ExecutionMode.TESTNET,
                                                     symbols=BASKET)
    equity = adapter.equity()
    price = adapter.last_price(DRILL)
    quantity = adapter.instruments[DRILL].quantise(Decimal(30) / price)

    print(f"venue   testnet, clock offset {adapter._clock_offset_ms} ms")
    print(f"equity  {equity} USDT")
    print(f"drill   {DRILL} at {price}, {quantity} units ≈ ${quantity * price:.2f}")

    flatten(adapter, now)
    if adapter.positions():
        print(f"\nREFUSING TO START: {adapter.positions()} still open")
        return 1

    try:
        return drills(adapter, equity, price, quantity, now)
    finally:
        flatten(adapter, datetime.now(UTC))
        left = adapter.positions()
        print(f"\n{'─' * 72}")
        print(f"  account flat: {not left}   {left if left else ''}")
        print(f"  request weight used: {adapter.weight_used}")
        print(f"  rejections seen: {len(adapter.rejections)}")


def drills(adapter: BinanceFuturesAdapter, equity: Decimal, price: Decimal,
           quantity: Decimal, now: datetime) -> int:
    """The drills; separate from `main` so its `finally` flattens even if one raises."""
    heading(1, "Reconciliation against the venue's own position view")
    engine = build(adapter, "drill-1", equity)
    print(f"  venue holds {adapter.positions() or 'nothing'}, we hold nothing")
    print(f"  divergences: {engine.reconcile(now) or 'none'} — halted: {engine.account.halted}")
    print(f"  trades already at the venue, marked seen and not folded: "
          f"{adapter.prime([DRILL])}")

    heading(2, "One order, engine to venue and back")
    decision = engine.submit(Order(DRILL, Side.BUY, quantity), price,
                             {DRILL: price}, now)
    print(f"  risk decision: allowed={decision.allowed} {decision.detail}")
    folded = engine.apply_fills(now)
    ours = engine.account.positions.get(DRILL)
    theirs = adapter.positions().get(DRILL, Decimal(0))
    print(f"  fills folded: {folded}")
    print(f"  our view {ours.quantity if ours else 0} @ {ours.avg_price if ours else 0}")
    print(f"  venue    {theirs}")
    print(f"  AGREE: {(ours.quantity if ours else Decimal(0)) == theirs}")

    heading(3, "Restart with a position open and no memory of it")
    print("  a second engine, as after a process death: it knows nothing")
    restarted = build(adapter, "drill-3", equity)
    divergences = restarted.reconcile(now)
    for d in divergences:
        print(f"  divergence  {d.asset}: ours {d.ours}, venue {d.theirs}, gap {d.gap}")
    print(f"  halted: {restarted.account.halted}")
    print(f"  reason: {restarted.account.halt_reason}")
    print("  correct: the gap is the only evidence the bug exists, so it is not")
    print("  silently adopted (§14.2).")

    heading(4, "Resolving an id the venue may already have executed")
    sent = next(i.client_order_id for i in reversed(engine.intents.records)
                if i.state is IntentState.ACKNOWLEDGED)
    for label, cid in (("an id we did send", sent), ("an id we never sent", "no-such-id")):
        found = adapter.already_executed(DRILL, cid)
        print(f"  {label:22} -> {found.status if found else 'never seen'}")
    print("  this is what a restart asks instead of resending and doubling.")

    heading(5, "One order, several trades")
    before = len(adapter.fills)
    big = adapter.instruments[DRILL].quantise(Decimal(400) / price)
    adapter.submit(Order(DRILL, Side.BUY, big).with_id("drill-5", 0), now)
    pieces = adapter.fills[before:]
    print(f"  asked for {big} units in one order")
    print(f"  the venue answered with {len(pieces)} trade(s):")
    for piece in pieces:
        print(f"    trade {piece.venue_trade_id}  {piece.quantity} @ {piece.price}")
    print(f"  distinct identities: {len({p.identity for p in pieces})}")
    print(f"  folded whole: {sum(p.quantity for p in pieces)} of {big}")
    if len(pieces) < 2:
        print("  NOT EXERCISED today: one level covered the order. The book, not")
        print("  the adapter, decides this — see test_a_fill_carries_the_venue_"
              "trade_id.")

    flatten(adapter, datetime.now(UTC))
    below = adapter.instruments[DRILL].round_price(price * Decimal("0.97"))

    def resting(tag: str) -> Order:
        """A buy 3% under the market: on the book, and nowhere near filling."""
        return Order(DRILL, Side.BUY, quantity, OrderType.LIMIT,
                     limit_price=below).with_id(f"drill-{tag}-{int(now.timestamp())}", 0)

    heading(6, "Dead man's switch: stop talking and let the venue cancel")
    order = resting("dms")
    adapter.submit(order, now)
    print(f"  resting at the venue: {order.client_order_id in adapter.open_orders(DRILL)}")
    armed = adapter.arm_dead_mans_switch(DRILL, 5_000)
    print(f"  armed for {armed} ms, and not re-armed")
    started = time.monotonic()
    while order.client_order_id in adapter.open_orders(DRILL):
        if time.monotonic() - started > 20:
            print("  NOT CANCELLED after 20 s — the switch did not fire")
            break
        time.sleep(1)
    else:
        print(f"  cancelled by the venue after {time.monotonic() - started:.1f} s, "
              "with no call from us")

    heading(7, "The same id twice, while the first still rests")
    order = resting("dup")
    first, second = adapter.submit(order, now), adapter.submit(order, now)
    listed = adapter.open_orders(DRILL).count(order.client_order_id)
    print(f"  first  -> {first.status} venue id {first.venue_order_id}")
    print(f"  second -> {second.status} venue id {second.venue_order_id} "
          f"{second.reason or ''}")
    print(f"  on the book under that id: {listed}   same order: "
          f"{first.venue_order_id == second.venue_order_id}")
    print("  a market order's id is freed at once (drill 4's case); a resting")
    print("  one is refused with -4116 and resolved to the order already there.")
    adapter.cancel_all(now)

    heading(8, "Insufficient margin, three times, and the engine stops")
    wide = RiskLimits(min_seconds_between_entries=0, max_orders_per_minute=60,
                      max_position_notional=Decimal(10_000_000),
                      max_position_pct_equity=Decimal(1_000),
                      max_gross_exposure=Decimal(1_000), max_net_exposure=Decimal(1_000),
                      max_leverage=Decimal(1_000))
    engine = ExecutionEngine(
        run_id="drill-8", mode=ExecutionMode.TESTNET, adapter=adapter,
        risk=RiskEngine(wide, AccountState(equity=equity, peak_equity=equity,
                                           day_start_equity=equity)),
        instruments=adapter.instruments)
    engine.reconcile(now)
    too_big = adapter.instruments[DRILL].quantise(equity * 25 / below)
    print(f"  asking for {too_big} units ≈ ${too_big * below:,.0f} on "
          f"${equity:,.0f} of equity")
    for attempt in range(1, 5):
        decision = engine.submit(
            Order(DRILL, Side.BUY, too_big, OrderType.LIMIT, limit_price=below),
            below, {DRILL: below}, now)
        print(f"  attempt {attempt}: {decision.breach or 'venue'} {decision.detail}  "
              f"errors={engine.account.consecutive_errors} "
              f"halted={engine.account.halted}")
        if engine.account.halted:
            break
    print(f"  reason: {engine.account.halt_reason}")
    print(f"  on the book: {adapter.open_orders(DRILL) or 'nothing'}")

    heading(9, "Clock drift past the receive window")
    good = adapter._clock_offset_ms
    adapter._clock_offset_ms = good - 20_000
    try:
        adapter._send_signed("GET", "/fapi/v2/account")
        print("  the venue accepted a request 20 s stale — recvWindow is not enforced")
    except VenueError as refused:
        print(f"  without the resync: {refused.code} {refused.message}")
    adapter._clock_offset_ms = good - 20_000
    held = adapter.positions()
    print(f"  with it: answered ({held or 'flat'}), offset "
          f"{good - 20_000} -> {adapter._clock_offset_ms} ms")

    heading(10, "Corrupt prices never reach the venue")
    engine = build(adapter, "drill-10", equity)
    engine.reconcile(now)
    before = len(adapter.rejections)
    for label, bad, mid in (("a 50% jump", price * Decimal("1.5"), price),
                            ("a zero price", Decimal(0), price),
                            ("a zero mid", price, Decimal(0))):
        decision = engine.submit(Order(DRILL, Side.BUY, quantity), bad,
                                 {DRILL: bad}, now, mid_price=mid)
        print(f"  {label:13} -> allowed={decision.allowed} "
              f"{decision.breach or ''} {decision.detail}")
    print(f"  venue rejections caused: {len(adapter.rejections) - before}; "
          f"on the book: {adapter.open_orders(DRILL) or 'nothing'}")

    heading(11, "Kill switch with a position open and an order resting")
    engine = build(adapter, "drill-11", equity)
    engine.reconcile(now)
    adapter.prime([DRILL])
    engine.submit(Order(DRILL, Side.BUY, quantity), price, {DRILL: price}, now,
                  mid_price=price)
    order = resting("kill")
    adapter.submit(order, now)
    print(f"  before: holding {adapter.positions().get(DRILL, 0)}, "
          f"resting {len(adapter.open_orders(DRILL))}")
    orders = engine.kill(now)
    print(f"  kill sent {len(orders)} reduce-only order(s)")
    print(f"  after:  holding {adapter.positions().get(DRILL, 0)}, "
          f"resting {len(adapter.open_orders(DRILL))}, halted {engine.account.halted}")
    decision = engine.submit(Order(DRILL, Side.BUY, quantity), price, {DRILL: price},
                             now, mid_price=price)
    print(f"  a new order afterwards: allowed={decision.allowed} "
          f"{decision.breach or ''} {decision.detail}")

    heading(12, "Die between send and answer, then restart from the log")
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "intents.jsonl"
        run_id = f"drill-12-{int(now.timestamp())}"
        before = build(adapter, run_id, equity)
        before.intents = IntentLog(path=path)
        before.reconcile(now)
        adapter.prime([DRILL])

        before.submit(Order(DRILL, Side.BUY, quantity), price, {DRILL: price}, now,
                      mid_price=price)
        print("  order 1 acknowledged; the process dies before folding its fill")
        before.adapter = _Dies(adapter, after_sending=True)
        before.submit(Order(DRILL, Side.BUY, quantity), price, {DRILL: price}, now,
                      mid_price=price)
        print("  order 2 reached the venue; the process died before the answer")
        before.adapter = _Dies(adapter, after_sending=False)
        before.submit(Order(DRILL, Side.BUY, quantity), price, {DRILL: price}, now,
                      mid_price=price)
        print("  order 3 died before it was sent")
        with path.open("a") as handle:
            handle.write('{"at": "')
        print("  and the log ends in half a line, as a kill -9 mid-write leaves it")
        used = {i.client_order_id for i in before.intents.records}
        print(f"  venue holds {adapter.positions().get(DRILL, 0)}; "
              f"unresolved in the log: {len(before.intents.unresolved())}")

        fresh = BinanceFuturesAdapter.from_environment(ExecutionMode.TESTNET,
                                                       symbols=BASKET)
        after = build(fresh, run_id, equity)
        after.intents = IntentLog.load(path)
        loaded = len(after.intents.records)
        divergences = after.recover(datetime.now(UTC))
        held = after.account.positions.get(DRILL)
        print(f"  restarted: read {loaded} records back, the torn line cut")
        for intent in after.intents.records:
            if "restart" in intent.reason:
                print(f"    {intent.client_order_id[:12]}…  {intent.state:12} {intent.reason}")
        print(f"  our view {held.quantity if held else 0}, venue "
              f"{fresh.positions().get(DRILL, 0)}, divergences "
              f"{divergences or 'none'}, halted {after.account.halted}")
        after.submit(Order(DRILL, Side.SELL, quantity, reduce_only=True), price,
                     {DRILL: price}, datetime.now(UTC), mid_price=price)
        print(f"  next id is new: {after.intents.records[-1].client_order_id not in used}")

    heading(13, "A position that outgrew its cap is trimmed back at the venue")
    flatten(adapter, now)
    adapter = BinanceFuturesAdapter.from_environment(ExecutionMode.TESTNET,
                                                     symbols=BASKET)
    engine = build(adapter, "drill-13", equity)
    engine.reconcile(now)
    adapter.prime([DRILL])
    engine.submit(Order(DRILL, Side.BUY, quantity), price, {DRILL: price}, now,
                  mid_price=price)
    engine.apply_fills(now)
    engine.risk.mark_equity(quantity * price * Decimal(2))
    print(f"  holding {quantity} ≈ ${quantity * price:.2f}, equity marked "
          f"${engine.account.equity:.2f}: {quantity * price / engine.account.equity:.0%} "
          f"of it, cap {LIMITS.max_position_pct_equity:.0%}")
    engine.halt("max_daily_loss: marked for the drill", now)
    orders = engine.trim_held({DRILL: price}, now)
    engine.apply_fills(now)
    ours = engine.account.positions[DRILL].quantity
    print(f"  halted first, as the loss limit would have: trimmed anyway, "
          f"{[f'{o.side} {o.quantity} reduce_only={o.reduce_only}' for o in orders]}")
    print(f"  after: ours {ours}, venue {adapter.positions().get(DRILL, 0)}, "
          f"{ours * price / engine.account.equity:.0%} of equity")

    print(f"\n{'─' * 72}\n  NOT DRILLED, and why\n{'─' * 72}")
    print("  websocket drop   this adapter is REST only; there is no socket to drop")
    print("  venue rate limit reaching -1003 costs the key a ban of minutes to days;")
    print(f"                   last weight reading {adapter.weight_used} of 2400/min")
    print("  registry restore §16.10, a database drill, not a venue one")

    return 0


if __name__ == "__main__":
    sys.exit(main())
