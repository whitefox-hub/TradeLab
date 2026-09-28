#!/usr/bin/env python
"""Run the PREREG-009 trend book forward, in the simulator. Fake capital, no venue.

    set -a && . secrets/api.env && set +a
    .venv/bin/python scripts/run_trend_book.py            # runs until stopped
    .venv/bin/python scripts/run_trend_book.py --once     # one tick, for a check

Restart (§14.3): intent log recovered, fills and book state reloaded from `state.json`;
a reconciliation divergence halts the run rather than being adopted. Size is clipped to
`max_position_pct_equity` before sending, not vetoed after (a veto at the cap is a cliff).
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
import numpy as np

from tradelab.config import get_settings
from tradelab.execution.adapters import SimulatedAdapter
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.intent import IntentLog
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    Side,
)
from tradelab.orchestration.forward import Snapshot, Store
from tradelab.risk.limits import RiskEngine
from tradelab.strategy import trend_book as tb

REST = "https://fapi.binance.com"
SYMBOLS = tuple(tb.WEIGHTS)
CAPITAL = Decimal(10_000)
LABEL = "trend-book PREREG-009"
STATE_DIR = Path("data/forward/trend-book")
PUBLISH_MARGIN_S = 15
HISTORY_DAYS = 16
#: Registered costs: taker 0.05% plus half spread 0.005% plus slippage 0.01%,
#: which the simulated adapter charges as a 1.5 bp price concession plus its fee.
HALF_SPREAD_BPS = Decimal("1.5")


def instruments(client: httpx.Client) -> dict[str, Instrument]:
    info = client.get(f"{REST}/fapi/v1/exchangeInfo").json()
    out = {}
    for s in info["symbols"]:
        if s["symbol"] in SYMBOLS:
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"]] = Instrument(s["symbol"], Decimal(f["PRICE_FILTER"]["tickSize"]),
                                          Decimal(f["LOT_SIZE"]["stepSize"]),
                                          Decimal(f["MIN_NOTIONAL"]["notional"]))
    return out


def bars(client: httpx.Client, symbol: str, days: int, now: datetime) -> np.ndarray:
    """Closed 5-minute bars: (open ms, open, high, low, close), oldest first."""
    rows: list[list] = []
    start = int((now - timedelta(days=days)).timestamp() * 1000)
    stop = int(now.timestamp() * 1000)
    while start < stop:
        page = client.get(f"{REST}/fapi/v1/klines", params={
            "symbol": symbol, "interval": "5m", "startTime": start, "limit": 1500}).json()
        if not page:
            break
        rows += page
        start = int(page[-1][0]) + 1
    closed = [r for r in rows if int(r[6]) < stop]
    return np.array([[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])]
                     for r in closed])


def basket_series(series: dict[str, np.ndarray]) -> np.ndarray:
    """The index on the 5-minute bars both assets have, anchored at the first."""
    common = np.intersect1d(series["BTCUSDT"][:, 0], series["ETHUSDT"][:, 0])
    aligned = {s: a[np.isin(a[:, 0], common)] for s, a in series.items()}
    anchor = {s: a[0, 4] for s, a in aligned.items()}
    cols = [tb.basket({s: a[:, k] for s, a in aligned.items()}, anchor) for k in (1, 2, 3, 4)]
    return np.column_stack([common, *cols])


def daily(index: np.ndarray, today: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Daily high, low and close of the index, closed days only."""
    days = np.array([datetime.fromtimestamp(t / 1000, UTC).strftime("%Y-%m-%d")
                     for t in index[:, 0]])
    highs, lows, closes = [], [], []
    for day in sorted(set(days)):
        if day >= today:
            continue
        rows = index[days == day]
        highs.append(rows[:, 2].max())
        lows.append(rows[:, 3].min())
        closes.append(rows[-1, 4])
    return np.array(highs), np.array(lows), np.array(closes)


def funding(client: httpx.Client, symbol: str) -> tuple[int, float]:
    last = client.get(f"{REST}/fapi/v1/fundingRate", params={"symbol": symbol,
                                                              "limit": 1}).json()[-1]
    return int(last["fundingTime"]), float(last["fundingRate"])


# -- state ---------------------------------------------------------------------


def save(path: Path, book: tb.State, adapter: SimulatedAdapter, extra: dict) -> None:
    def encode(value):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    payload = {"book": asdict(book), "fills": [asdict(f) for f in adapter.fills],
               "held": {k: str(v) for k, v in adapter._held.items()}, **extra}
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(payload, default=encode, indent=1))
    partial.replace(path)


def load(path: Path) -> tuple[tb.State, list[Fill], dict[str, Decimal], dict]:
    if not path.exists():
        return tb.State(), [], {}, {}
    raw = json.loads(path.read_text())
    book = raw.pop("book")
    for key in ("setting_at",):
        if book.get(key):
            book[key] = datetime.fromisoformat(book[key])
    fills = [Fill(asset=f["asset"], side=Side(f["side"]), quantity=Decimal(f["quantity"]),
                  price=Decimal(f["price"]), fee=Decimal(f["fee"]),
                  at=datetime.fromisoformat(f["at"]), client_order_id=f["client_order_id"],
                  venue_trade_id=f.get("venue_trade_id", "")) for f in raw.pop("fills")]
    held = {k: Decimal(v) for k, v in raw.pop("held").items()}
    return tb.State(**book), fills, held, raw


# -- one tick ------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / "state.json"

    store = Store(get_settings().database_url)
    strategy = store.ensure_strategy("trend-book", Path("tradelab/strategy/trend_book.py"))
    limits_id, limits = store.active_limits()
    run_id = store.ensure_run(strategy, LABEL, {"registration": "PREREG-009",
                                                "purpose": "forward validation",
                                                "capital_is_fake": True}, CAPITAL, limits_id)
    client = httpx.Client(timeout=30.0)
    instrument = instruments(client)

    book, fills, held, extra = load(state_path)
    adapter = SimulatedAdapter(instruments=instrument, prices={},
                               half_spread_bps=HALF_SPREAD_BPS)
    adapter._fills.extend(fills)
    adapter._held.update(held)
    funding_paid = {k: Decimal(v) for k, v in extra.get("funding_paid", {}).items()}
    funding_seen = {k: int(v) for k, v in extra.get("funding_seen", {}).items()}

    account = AccountState(equity=CAPITAL, peak_equity=Decimal(extra.get("peak", CAPITAL)),
                           day_start_equity=CAPITAL)
    engine = ExecutionEngine(run_id=str(run_id)[:8], mode=ExecutionMode.SIMULATOR,
                             adapter=adapter, risk=RiskEngine(limits, account),
                             instruments=instrument,
                             intents=IntentLog.load(STATE_DIR / "intents.jsonl"))
    divergences = engine.recover(datetime.now(UTC))
    print(f"run {run_id} resumed; divergences {divergences or 'none'}; "
          f"book {book.guide} {book.exposure:+.2f}", flush=True)
    if divergences:
        store.halt(run_id, f"reconciliation at restart: {divergences}")
        return 1

    while True:
        now = datetime.now(UTC)
        series = {s: bars(client, s, HISTORY_DAYS, now) for s in SYMBOLS}
        index = basket_series(series)
        last = index[-1]
        bar = tb.Bar(datetime.fromtimestamp(last[0] / 1000, UTC) + timedelta(minutes=5),
                     float(last[1]), float(last[2]), float(last[3]), float(last[4]))
        highs, lows, closes = daily(index, bar.at.strftime("%Y-%m-%d"))
        atr, signal = tb.daily_atr(highs, lows, closes), tb.momentum(closes)
        price = {s: Decimal(str(series[s][-1, 4])) for s in SYMBOLS}

        setting = store.setting()
        decision = tb.step(book, setting, bar, atr, signal)
        fill_price = dict(price)
        if decision.reason in ("stop", "take profit"):
            level = tb.exit_level(book, bar, decision.reason)
            fill_price = {s: p * Decimal(str(level / bar.close)) for s, p in price.items()}
        adapter.prices.update(fill_price)

        equity = _equity(engine, price, funding_paid)
        engine.risk.mark_equity(equity)
        # max_daily_loss needs the day's opening equity: reset on each UTC day's first tick.
        today = now.strftime("%Y-%m-%d")
        if extra.get("day") != today:
            engine.account.day_start_equity = equity
            extra["day"] = today
        if decision.changed:
            _rebalance(engine, decision.state.exposure, equity, fill_price, limits, now)
        adapter.prices.update(price)
        engine.trim_held(price, now)
        engine.apply_fills(now)
        engine.risk.enforce(now)

        for s in SYMBOLS:                               # funding across settlements
            stamp, rate = funding(client, s)
            if stamp > funding_seen.get(s, 0):
                quantity = adapter._held.get(s, Decimal(0))
                funding_paid[s] = funding_paid.get(s, Decimal(0)) + \
                    quantity * price[s] * Decimal(str(rate))
                funding_seen[s] = stamp

        book = decision.state
        equity = _equity(engine, price, funding_paid)
        rows = [_snapshot(engine, s, price[s], funding_paid.get(s, Decimal(0)), book)
                for s in SYMBOLS]
        gross = sum(abs(r.qty) * price[r.asset] for r in rows)
        store.write(run_id, now, rows, equity, Decimal(gross))
        save(state_path, book, adapter, {
            "funding_paid": funding_paid, "funding_seen": funding_seen,
            "peak": engine.account.peak_equity, "day": extra.get("day")})
        print(f"{now:%Y-%m-%d %H:%M} trend {setting.trend} ({setting.source}) "
              f"{decision.reason}: {book.guide} {book.exposure:+.2f}, "
              f"equity {equity:,.2f}", flush=True)
        if args.once:
            return 0
        _sleep_to_next_bar()


def _equity(engine: ExecutionEngine, price: dict[str, Decimal],
            funding_paid: dict[str, Decimal]) -> Decimal:
    marked = sum((p.realised_pnl + p.unrealised(price[a])
                  for a, p in engine.account.positions.items() if a in price), Decimal(0))
    return CAPITAL + marked - sum(funding_paid.values(), Decimal(0))


def _rebalance(engine: ExecutionEngine, exposure: float, equity: Decimal,
               price: dict[str, Decimal], limits, now: datetime) -> None:
    cap = limits.max_position_pct_equity * equity
    for symbol, weight in tb.WEIGHTS.items():
        wanted = Decimal(str(exposure * weight)) * equity
        if abs(wanted) > cap:
            print(f"  {symbol}: {wanted:,.2f} clipped to the {cap:,.2f} position cap")
            wanted = cap if wanted > 0 else -cap
        held = engine.account.positions.get(symbol)
        current = (held.quantity if held else Decimal(0)) * price[symbol]
        delta = (wanted - current) / price[symbol]
        if abs(delta) * price[symbol] < engine.instruments[symbol].min_viable(price[symbol]):
            continue
        reducing = held is not None and held.quantity != 0 and \
            (delta > 0) != (held.quantity > 0) and abs(delta) <= abs(held.quantity)
        order = Order(symbol, Side.BUY if delta > 0 else Side.SELL, abs(delta),
                      reduce_only=reducing)
        decision = engine.submit(order, price[symbol], {s: price[s] for s in price}, now,
                                 mid_price=price[symbol])
        if not decision.allowed:
            print(f"  {symbol}: refused, {decision.breach}: {decision.detail}")


def _snapshot(engine: ExecutionEngine, symbol: str, price: Decimal, funding_paid: Decimal,
              book: tb.State) -> Snapshot:
    p = engine.account.positions.get(symbol)
    qty = p.quantity if p else Decimal(0)
    return Snapshot(symbol, qty, p.avg_price if p else Decimal(0),
                    p.unrealised(price) if p else Decimal(0),
                    p.realised_pnl if p else Decimal(0), funding_paid,
                    book.guide if qty else None, book.trend if qty else None,
                    book.source if qty else None)


def _sleep_to_next_bar() -> None:
    now = time.time()
    time.sleep(300 - now % 300 + PUBLISH_MARGIN_S)


if __name__ == "__main__":
    raise SystemExit(main())
