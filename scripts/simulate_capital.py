"""Walk execution steps 8-13 in SIMULATOR mode on a given capital. Plan.md §14.1, §14.5, §20.

Reference buy-and-hold, trend-conditioned: tests plumbing and limits (tick/lot rounding),
not an edge. Venue-side rejections need testnet (§12.1).

    python scripts/simulate_capital.py [--capital 1000] [--trend N] [--rebalance-minutes 60]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa

from tradelab.config import Settings
from tradelab.execution.adapters import SimulatedAdapter
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Instrument,
    Order,
    OrderType,
    Side,
)
from tradelab.risk.limits import RiskEngine, RiskLimits
from tradelab.scenario.run import _bars
from tradelab.scenario.tilt import NEUTRAL, exposure_multipliers
from tradelab.strategy.base import Context
from tradelab.strategy.reference import BuyAndHold

ASSET = "BTCUSDT"

#: Asks two thirds of the position cap so the maximal trend tilt lands on the cap, not past it.
STRATEGY = BuyAndHold()

#: Binance USD-M BTCUSDT. At small capital the lot size binds, not min notional (§14.1).
INSTRUMENT = Instrument(
    asset=ASSET, tick_size=Decimal("0.1"), lot_size=Decimal("0.001"),
    min_notional=Decimal(100),
)


def active_limits(engine: sa.Engine | None) -> tuple[RiskLimits, str]:
    """Latest stored risk limits, or engine defaults, with a label saying which."""
    if engine is None:
        return RiskLimits(), "engine defaults"
    import dataclasses
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT * FROM risk_limit_configs ORDER BY valid_from DESC LIMIT 1"
        )).mappings().first()
    if row is None:
        return RiskLimits(), "engine defaults, none stored"
    fields = {f.name: f for f in dataclasses.fields(RiskLimits)}
    values = {name: (int(row[name]) if f.type is int else Decimal(str(row[name])))
              for name, f in fields.items()}
    return RiskLimits(**values), f"stored configuration {row['name']!r}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=Decimal, default=Decimal(1000))
    parser.add_argument("--trend", type=int, default=NEUTRAL)
    parser.add_argument("--rebalance-minutes", type=int, default=60)
    args = parser.parse_args()

    close = _bars()
    start = datetime(2024, 3, 1, tzinfo=UTC)

    try:
        db = sa.create_engine(Settings().database_url)
        db.connect().close()
    except Exception:
        db = None
    limits, source = active_limits(db)

    long_multiplier, short_multiplier = exposure_multipliers(args.trend)
    print(f"capital        : {args.capital:,.2f} USDT")
    print(f"strategy       : {STRATEGY.name}")
    print(f"limits         : {source}")
    print(f"market trend   : {args.trend} "
          f"({'neutral' if args.trend == NEUTRAL else f'longs x{long_multiplier}'})")
    print()

    by_pct = args.capital * limits.max_position_pct_equity
    ceiling = min(by_pct, limits.max_position_notional)
    price0 = Decimal(str(close[0]))
    increment = INSTRUMENT.lot_size * price0
    print(f"max per position: {ceiling:,.2f} "
          f"({'pct_equity' if by_pct < limits.max_position_notional else 'notional'} binds)")
    print(f"asked for       : {args.capital * STRATEGY.exposure:,.2f} "
          f"({STRATEGY.exposure:.1%} of equity, by the strategy)")
    print(f"min viable      : {INSTRUMENT.min_viable(price0):,.2f} "
          f"(lot {INSTRUMENT.lot_size} x price {price0:,.0f})")
    print(f"size increment  : {increment:,.2f}  "
          f"-> {ceiling / increment:,.1f} increments of headroom")
    if ceiling < INSTRUMENT.min_viable(price0):
        print("  the smallest position the venue accepts is above the largest "
              "the limits allow. Nothing can be opened at this capital.")
        return 1
    if ceiling / increment < 20:
        print("  below twenty increments, position sizing stops being a control "
              "(§14.1): the realised position differs from the intended one by "
              "the quantisation, and realised risk differs by the same margin.")
    print()

    account = AccountState(
        equity=args.capital, peak_equity=args.capital,
        day_start_equity=args.capital)
    adapter = SimulatedAdapter(
        mode=ExecutionMode.SIMULATOR, instruments={ASSET: INSTRUMENT},
        prices={ASSET: price0})
    risk = RiskEngine(limits=limits, account=account)
    engine = ExecutionEngine(run_id="sim-capital", mode=ExecutionMode.SIMULATOR,
                             adapter=adapter, risk=risk,
                             instruments={ASSET: INSTRUMENT})

    divergences = engine.reconcile(start)
    print(f"reconciled      : {len(divergences)} divergences (step 8)")

    submitted = filled = 0
    vetoes: dict[str, int] = {}
    step = args.rebalance_minutes
    for i in range(0, close.size, step):
        at = start + timedelta(minutes=i)
        price = Decimal(str(close[i]))
        held = (account.positions[ASSET].quantity
                if ASSET in account.positions else Decimal(0))
        adapter.prices[ASSET] = price
        prices = {ASSET: price}

        # Step 9: `history` ends before bar i, so the strategy cannot see its trade price.
        target = STRATEGY.decide(Context(
            at=at, asset=ASSET, history=close[:i], last_close=price,
            held=held, equity=account.equity))

        # Step 10: trend scales longs and shorts oppositely.
        multiplier = long_multiplier if target.exposure >= 0 else short_multiplier
        target_notional = (abs(target.exposure) * account.equity
                           * Decimal(str(multiplier)))
        wanted = INSTRUMENT.quantise(target_notional / price)
        if target.exposure < 0:
            wanted = -wanted
        delta = wanted - held
        if abs(delta) * price < INSTRUMENT.min_viable(price):
            continue

        order = Order(
            asset=ASSET, side=Side.BUY if delta > 0 else Side.SELL,
            quantity=abs(delta), order_type=OrderType.MARKET,
            limit_price=None, reduce_only=delta < 0,
        )
        submitted += 1
        decision = engine.submit(order, price, prices, at, mid_price=price)
        if not decision.allowed:
            name = decision.breach.value if decision.breach else "unknown"
            vetoes[name] = vetoes.get(name, 0) + 1
            continue
        filled += engine.apply_fills(at)

        position = account.positions.get(ASSET)
        if position is not None:
            account.equity = args.capital + position.realised_pnl + \
                position.unrealised(price)

    final_price = Decimal(str(close[-1]))
    position = account.positions.get(ASSET)
    unrealised = position.unrealised(final_price) if position else Decimal(0)
    realised = position.realised_pnl if position else Decimal(0)
    equity = args.capital + realised + unrealised

    if submitted == 0:
        asked = args.capital * STRATEGY.exposure
        smallest = INSTRUMENT.quantise(asked / price0) * price0
        print("no order was ever asked for, and this is why: the position "
              f"the strategy asked for, {asked:,.2f}, quantises down to "
              f"{smallest:,.2f}, which is below the venue minimum of "
              f"{INSTRUMENT.min_viable(price0):,.2f}. The book stays flat with "
              "no veto and no error - the one failure mode that looks like a "
              "decision not to trade.")
        print()

    print(f"orders asked    : {submitted}")
    print(f"vetoed          : {sum(vetoes.values())} "
          + (", ".join(f"{k} x{v}" for k, v in vetoes.items()) if vetoes else ""))
    print(f"fills folded    : {filled} (step 13)")
    print(f"intents written : {len(engine.intents.records)} (step 12)")
    print()
    if position is not None and position.is_open:
        print(f"position        : {position.quantity} {ASSET} "
              f"@ {position.avg_price:,.2f}")
        print(f"  notional      : {position.notional(final_price):,.2f} "
              f"({position.notional(final_price) / equity:.1%} of equity)")
    print(f"realised        : {realised:+,.2f}")
    print(f"unrealised      : {unrealised:+,.2f}")
    print(f"equity          : {equity:,.2f} "
          f"({(equity / args.capital - 1) * 100:+.2f}%)")
    print()
    print(f"the asset moved : {(final_price / price0 - 1) * 100:+.2f}% "
          f"over {close.size:,} minutes")
    print("This is the reference position, not a strategy: nothing has cleared "
          "§15. The number above is the asset's move, scaled by what the "
          "limits allowed and reduced by costs. It is a test of the plumbing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
