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
from datetime import UTC, datetime
from decimal import Decimal

from tradelab.execution.binance import BinanceFuturesAdapter
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

    # -- 6. the dead man's switch --------------------------------------------
    heading(6, "A kill switch that does not depend on our process")
    armed = adapter.arm_dead_mans_switch(DRILL, 30_000)
    print(f"  armed at the venue for {armed} ms")
    adapter.disarm_dead_mans_switch(DRILL)
    print("  disarmed (countdown zero cancels the timer, not the orders)")

    # -- 7. the kill switch ---------------------------------------------------
    heading(7, "Kill switch, from the venue's view and not ours")
    orders = engine.kill(now)
    print(f"  flattening orders built from the venue: {len(orders)}")
    for order in orders:
        print(f"    {order.side} {order.quantity} {order.asset} reduce_only={order.reduce_only}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
