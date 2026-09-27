#!/usr/bin/env python
"""Run the §16.7 failure drills against Binance testnet. Plan.md §12.1, §16.7.

The third rung of §12.1 exists for the failures a local simulator cannot
produce, and §16.7 says a drill that has never been run is a plan, not a
control. This runs them against a matching engine with the real
`ExecutionEngine` in the loop, and prints what happened rather than whether it
matched an expectation.

    BINANCE_TESTNET_KEY=... BINANCE_TESTNET_SECRET=... python scripts/testnet_drill.py

It opens and closes real testnet positions. It flattens at the start and again
at the end, and reports whether the account is flat when it finishes — because
a drill that leaves a position open has created the condition it was testing
for.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from decimal import Decimal

from tradelab.execution.binance import BinanceFuturesAdapter, VenueError
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Order,
    OrderType,
    Side,
)
from tradelab.risk.limits import RiskEngine, RiskLimits

#: The basket, and deliberately not BTC: every one of these has a $5 floor at
#: the venue against BTC's $50, which is the difference between $100 buying
#: twenty increments and buying two (§14.5).
BASKET = ["ADAUSDT", "XRPUSDT", "XLMUSDT", "SUIUSDT", "ALGOUSDT"]
DRILL = "ADAUSDT"

#: The entry-spacing limit is relaxed here and nowhere else. A drill submits in
#: seconds what a strategy would submit in an hour, and leaving it at 60 would
#: mean the drills never reach the venue at all — which is the failure mode
#: where a suite passes by not running.
LIMITS = RiskLimits(min_seconds_between_entries=0, max_orders_per_minute=60)


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
    """The drills themselves, so the caller can flatten whatever they leave.

    Separated for that reason alone: the first version printed its "account
    flat" line at the end of the same function, so the run that died in drill 4
    skipped it and left 118 ADA open — a drill creating the condition it exists
    to test for.
    """
    # -- 1. reconciliation against what the venue really holds ---------------
    heading(1, "Reconciliation against the venue's own position view")
    engine = build(adapter, "drill-1", equity)
    print(f"  venue holds {adapter.positions() or 'nothing'}, we hold nothing")
    print(f"  divergences: {engine.reconcile(now) or 'none'} — halted: {engine.account.halted}")
    # Reconcile, then prime, then trade. Without the priming step the first
    # order folds the venue's whole recent history as though we had placed it.
    print(f"  trades already at the venue, marked seen and not folded: "
          f"{adapter.prime([DRILL])}")

    # -- 2. an order through the whole path ----------------------------------
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

    # -- 3. the crash ---------------------------------------------------------
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

    # -- 4. the id already sent ----------------------------------------------
    heading(4, "Resolving an id the venue may already have executed")
    from tradelab.execution.intent import IntentState

    sent = next(i.client_order_id for i in reversed(engine.intents.records)
                if i.state is IntentState.ACKNOWLEDGED)
    for label, cid in (("an id we did send", sent), ("an id we never sent", "no-such-id")):
        found = adapter.already_executed(DRILL, cid)
        print(f"  {label:22} -> {found.status if found else 'never seen'}")
    print("  this is what a restart asks instead of resending and doubling.")

    # -- 5. partial fills -----------------------------------------------------
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
        # Said out loud rather than passed over. Whether an order sweeps more
        # than one level is the book's business on the day, so this drill
        # exercises the path opportunistically and cannot claim it when it did
        # not. The unit test is what holds the behaviour; a run of this on 26
        # September filled 118 ADA in three trades and folded all three.
        print("  NOT EXERCISED today: one level covered the order. The book, not")
        print("  the adapter, decides this — see test_a_fill_carries_the_venue_"
              "trade_id.")

    # Everything below starts flat, because every drill from here builds a
    # fresh engine and a fresh engine reconciling against a held position is
    # drill 3 again, not the drill it was meant to be.
    flatten(adapter, datetime.now(UTC))
    below = adapter.instruments[DRILL].round_price(price * Decimal("0.97"))

    def resting(tag: str) -> Order:
        """A buy 3% under the market: on the book, and nowhere near filling."""
        return Order(DRILL, Side.BUY, quantity, OrderType.LIMIT,
                     limit_price=below).with_id(f"drill-{tag}-{int(now.timestamp())}", 0)

    # -- 6. the dead man's switch, left to fire -------------------------------
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

    # -- 7. idempotency where the venue does enforce it ----------------------
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

    # -- 8. rejection for margin, until the error limit halts ----------------
    heading(8, "Insufficient margin, three times, and the engine stops")
    # Limits wide enough that the venue is the one to refuse. Everything the
    # risk engine checks is exercised elsewhere; this is about the path a
    # venue refusal takes once it is past the veto.
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

    # -- 9. clock drift -------------------------------------------------------
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

    # -- 10. corrupt data -----------------------------------------------------
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

    # -- 11. the kill switch, mid-flight -------------------------------------
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

    # -- not drilled here -----------------------------------------------------
    print(f"\n{'─' * 72}\n  NOT DRILLED, and why\n{'─' * 72}")
    print("  websocket drop   this adapter is REST only; there is no socket to drop")
    print("  venue rate limit reaching -1003 costs the key a ban of minutes to days;")
    print(f"                   last weight reading {adapter.weight_used} of 2400/min")
    print("  registry restore §16.10, a database drill, not a venue one")

    return 0


if __name__ == "__main__":
    sys.exit(main())
