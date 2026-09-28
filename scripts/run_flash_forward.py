#!/usr/bin/env python
"""Flash Strike forward, in the simulator: PREREG-008 (pullback) and PREREG-010 (at the shock).

    set -a && . secrets/api.env && set +a
    .venv/bin/python scripts/run_flash_forward.py          # runs until stopped
    .venv/bin/python scripts/run_flash_forward.py --once   # warm up, one tick, stop

Three books on $10,000 of fake capital each: 008 registered, 008 operator (sized by the
trend, PREREG-008's amendment) and 010 registered. The same rules, features, model and
exits as the backtest: inputs are assembled by `flash_data.assemble`, candidates by the
same `flash_strike` functions, models by `run_prereg_008.decide`, retrained on the first
tick of each month on every candidate determined before it (history plus forward).

Every candidate, taken or not, is kept with its outcome under all twelve configurations
once its longest horizon has passed: that is next month's training data and PREREG-010's
record. Restart resumes each book from its intent log and `state.json`.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent))
from run_prereg_008 import MonthDecision, breakeven, decide, matrix

from tradelab.collectors.binance import live
from tradelab.collectors.binance.intraday import daily_quote_volume, monthly_universe
from tradelab.collectors.binance.pit_panel import rest_funding, universe
from tradelab.config import get_settings
from tradelab.execution.engine import ExecutionEngine
from tradelab.execution.intent import IntentLog
from tradelab.execution.paper import PaperAdapter
from tradelab.execution.types import (
    AccountState,
    ExecutionMode,
    Fill,
    Instrument,
    Order,
    OrderType,
    Side,
)
from tradelab.orchestration.forward import Snapshot, Store
from tradelab.risk.limits import RiskEngine
from tradelab.scenario.tilt import NEUTRAL, exposure_multipliers
from tradelab.strategy import flash_data
from tradelab.strategy import flash_strike as fs

ROOT = Path("data/parquet/intraday")
STATE = Path("data/forward/flash-strike")
CAPITAL = Decimal(10_000)
WINDOW_DAYS = 31
PUBLISH_MARGIN_S = 10
RISK_PER_TRADE, MAX_POSITION, MAX_GROSS, MAX_OPEN = 0.0025, 0.25, 2.0, 10
LONGEST_BARS = max(h for _, _, h in fs.CONFIGS) // 5
BOOKS = (("008-registered", "event", False), ("008-operator", "event", True),
         ("010-registered", "at_shock", False))
KIND_OF = {"event": "PREREG-008", "at_shock": "PREREG-010"}


# -- the universe and the models, per month ----------------------------------------


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def universe_for(client: httpx.Client, month: date) -> list[str]:
    """The registered universe: top 100 by the previous 30 days' volume (PREREG-008)."""
    stored = pl.read_parquet(ROOT / "_universe_monthly.parquet")
    have = stored.filter(pl.col("month") == month)
    if have.height:
        return have["asset"].to_list()
    cache = STATE / f"universe-{month:%Y-%m}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    start = datetime(month.year, month.month, 1, tzinfo=UTC) - timedelta(days=45)
    end = datetime(month.year, month.month, 1, tzinfo=UTC) - timedelta(seconds=1)
    frames = []
    for c in universe(client):
        v = daily_quote_volume(client, c.symbol, start, end, c.delisted_at)
        if not v.is_empty():
            frames.append(v.with_columns(pl.lit(c.asset).alias("asset")))
    assets = monthly_universe(pl.concat(frames), [month])["asset"].to_list()
    cache.write_text(json.dumps(assets))
    return assets


def model_for(kind: str, month: date) -> MonthDecision:
    """The frozen model for `month`, trained on every candidate determined before it."""
    path = STATE / "models" / f"{kind}-{month:%Y-%m}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    history = pl.read_parquet(ROOT / "_candidates.parquet").filter(pl.col("kind") == kind)
    forward = STATE / "candidates.parquet"
    if forward.exists():
        history = pl.concat([history, pl.read_parquet(forward).filter(pl.col("kind") == kind)],
                            how="diagonal_relaxed")
    decision = decide(history.sort(["fill_ms", "asset", "shock_ms"]), month)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(decision))
    print(f"  model {kind} {month:%Y-%m}: config {decision.config}", flush=True)
    return decision


# -- one asset's live state ----------------------------------------------------------


class Asset:
    def __init__(self, name: str) -> None:
        self.name, self.symbol = name, f"{name}USDT"
        self.klines = self.premium = self.metrics = pl.DataFrame()
        self.funding: list[tuple[int, float]] = []

    def warm(self, client: httpx.Client, now: datetime) -> None:
        self.klines, self.premium, self.metrics = live.warm(client, self.symbol, now,
                                                            WINDOW_DAYS)

    def warm_funding(self, client: httpx.Client, now: datetime) -> None:
        """Sequential across assets: funding shares 500 requests per 5 minutes."""
        self.funding = rest_funding(client, self.symbol, now - timedelta(days=WINDOW_DAYS),
                                    now, pace=0.7)

    def update(self, client: httpx.Client, now: datetime) -> None:
        keep = live.utc_ms(now - timedelta(days=WINDOW_DAYS))
        self.klines = live.merge(self.klines, live.recent_klines(client, self.symbol), keep)
        self.premium = live.merge(self.premium,
                                  live.recent_klines(client, self.symbol, "premium"), keep)
        self.metrics = live.merge(self.metrics, live.recent_metrics(client, self.symbol), keep)
        recent = client.get(f"{live.REST}/fapi/v1/fundingRate",
                            params={"symbol": self.symbol, "limit": 3}).json()
        if isinstance(recent, list):
            known = {t for t, _ in self.funding}
            self.funding += [(int(r["fundingTime"]), float(r["fundingRate"]))
                             for r in recent if int(r["fundingTime"]) not in known]

    def series(self) -> tuple[fs.Series, fs.Context, list[str]] | None:
        if self.klines.height < fs.QUANTILE_BARS // 2:
            return None
        bars = flash_data.assemble(self.klines, self.premium, self.metrics)
        s = flash_data.series(bars)
        return s, fs.prepare(s), bars["month"].to_list()

    def funding_per_bar(self, close_ms: np.ndarray) -> np.ndarray:
        out = np.zeros(close_ms.size)
        index = {int(ms): i for i, ms in enumerate(close_ms)}
        for stamp, rate in self.funding:
            i = index.get(stamp - stamp % 300_000)
            if i is not None:
                out[i] += rate
        return out

    def funding_z(self, day: date) -> float:
        daily: dict[date, float] = {}
        for stamp, rate in self.funding:
            d = datetime.fromtimestamp(stamp / 1000, UTC).date()
            daily[d] = daily.get(d, 0.0) + rate
        days = sorted(d for d in daily if d < day)
        if len(days) < 12:
            return float("nan")
        values = np.array([daily[d] for d in days])
        ref = values[-31:-1]
        return float((values[-1] - ref.mean()) / ref.std()) if ref.std() > 0 else 0.0


# -- books ---------------------------------------------------------------------------


class Book:
    def __init__(self, name: str, kind: str, tilted: bool, store: Store, limits_id,
                 limits, instruments: dict[str, Instrument]) -> None:
        self.name, self.kind, self.tilted = name, kind, tilted
        self.dir = STATE / name
        self.dir.mkdir(parents=True, exist_ok=True)
        strategy = store.ensure_strategy(f"flash-strike-{kind}", Path(fs.__file__))
        self.run_id = store.ensure_run(strategy, f"flash-strike {name}",
                                       {"registration": KIND_OF[kind], "book": name,
                                        "purpose": "forward validation",
                                        "capital_is_fake": True}, CAPITAL, limits_id)
        self.adapter = PaperAdapter(instruments=instruments, prices={},
                                    half_spread_bps=Decimal("1.5"))
        state = json.loads((self.dir / "state.json").read_text()) \
            if (self.dir / "state.json").exists() else {}
        self.adapter._fills.extend(_fills_from(state.get("fills", [])))
        self.adapter._held.update({k: Decimal(v) for k, v in state.get("held", {}).items()})
        self.trades: list[dict] = state.get("trades", [])
        self.funding_paid = {k: Decimal(v) for k, v in state.get("funding_paid", {}).items()}
        self.funding_seen = {k: int(v) for k, v in state.get("funding_seen", {}).items()}
        self.day, self.last_entry_ms = state.get("day"), int(state.get("last_entry_ms", 0))
        account = AccountState(equity=CAPITAL, peak_equity=Decimal(state.get("peak", CAPITAL)),
                               day_start_equity=CAPITAL)
        self.engine = ExecutionEngine(
            run_id=str(self.run_id)[:8], mode=ExecutionMode.SIMULATOR, adapter=self.adapter,
            risk=RiskEngine(limits, account), instruments=instruments,
            intents=IntentLog.load(self.dir / "intents.jsonl"))
        gaps = self.engine.recover(datetime.now(UTC))
        if gaps:
            store.halt(self.run_id, f"reconciliation at restart: {gaps}")
            raise SystemExit(f"{name}: divergence at restart {gaps}")

    def equity(self, price: dict[str, Decimal]) -> Decimal:
        marked = sum((p.realised_pnl + p.unrealised(price[a])
                      for a, p in self.engine.account.positions.items() if a in price),
                     Decimal(0))
        return CAPITAL + marked - sum(self.funding_paid.values(), Decimal(0))

    def gross(self, price: dict[str, Decimal]) -> float:
        return float(sum((abs(p.quantity) * price[a] for a, p in
                          self.engine.account.positions.items() if a in price), Decimal(0)))

    def save(self) -> None:
        payload = {"fills": [_fill_dict(f) for f in self.adapter.fills],
                   "held": {k: str(v) for k, v in self.adapter._held.items()},
                   "trades": self.trades,
                   "funding_paid": {k: str(v) for k, v in self.funding_paid.items()},
                   "funding_seen": self.funding_seen, "day": self.day,
                   "last_entry_ms": self.last_entry_ms,
                   "peak": str(self.engine.account.peak_equity)}
        partial = self.dir / "state.partial"
        partial.write_text(json.dumps(payload))
        partial.replace(self.dir / "state.json")


def _fill_dict(f: Fill) -> dict:
    d = asdict(f)
    return {k: (v.isoformat() if isinstance(v, datetime) else str(v)) for k, v in d.items()}


def _fills_from(rows: list[dict]) -> list[Fill]:
    return [Fill(asset=r["asset"], side=Side(r["side"]), quantity=Decimal(r["quantity"]),
                 price=Decimal(r["price"]), fee=Decimal(r["fee"]),
                 at=datetime.fromisoformat(r["at"]), client_order_id=r["client_order_id"],
                 venue_trade_id=r.get("venue_trade_id", "")) for r in rows]


# -- the run -------------------------------------------------------------------------


def instruments_for(client: httpx.Client, symbols: list[str]) -> dict[str, Instrument]:
    info = client.get(f"{live.REST}/fapi/v1/exchangeInfo").json()
    out = {}
    for s in info["symbols"]:
        if s["symbol"] in symbols:
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"][:-4]] = Instrument(
                s["symbol"][:-4], Decimal(f["PRICE_FILTER"]["tickSize"]),
                Decimal(f["LOT_SIZE"]["stepSize"]), Decimal(f["MIN_NOTIONAL"]["notional"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=60.0)
    store = Store(get_settings().database_url)
    limits_id, limits = store.active_limits()

    now = datetime.now(UTC)
    month = month_start(now.date())
    members = universe_for(client, month)
    if "BTC" not in members:
        members.append("BTC")
    instruments = instruments_for(client, [f"{a}USDT" for a in members])
    books = [Book(n, k, t, store, limits_id, limits, instruments) for n, k, t in BOOKS]
    models = {k: model_for(k, month) for k in ("event", "at_shock")}

    assets = {a: Asset(a) for a in members}
    print(f"warming {len(assets)} assets", flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda x: x.warm(client, now), assets.values()))
    for a in assets.values():
        a.warm_funding(client, now)
    pending: list[dict] = json.loads((STATE / "pending.json").read_text()) \
        if (STATE / "pending.json").exists() else []
    awaiting: list[dict] = json.loads((STATE / "awaiting.json").read_text()) \
        if (STATE / "awaiting.json").exists() else []
    print(f"ready: {len(books)} books, {len(pending)} pending, {len(awaiting)} awaiting outcome",
          flush=True)

    while True:
        now = datetime.now(UTC)
        if month_start(now.date()) != month:
            month = month_start(now.date())
            members = universe_for(client, month) + (["BTC"] if "BTC" not in members else [])
            instruments.update(instruments_for(client, [f"{a}USDT" for a in members]))
            for a in members:
                if a not in assets:
                    assets[a] = Asset(a)
                    assets[a].warm(client, now)
                    assets[a].warm_funding(client, now)
            models = {k: model_for(k, month) for k in ("event", "at_shock")}
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda x, at=now: x.update(client, at), assets.values()))
        tick(now, client, store, books, models, assets, members, pending, awaiting)
        (STATE / "pending.json").write_text(json.dumps(pending))
        (STATE / "awaiting.json").write_text(json.dumps(awaiting))
        if args.once:
            return 0
        time.sleep(300 - time.time() % 300 + PUBLISH_MARGIN_S)


def tick(now: datetime, client: httpx.Client, store: Store, books: list[Book], models: dict,
         assets: dict[str, Asset], members: list[str], pending: list[dict],
         awaiting: list[dict]) -> None:
    setting = store.setting()
    views = {a: v for a, x in assets.items() if (v := x.series()) is not None}
    btc = views.get("BTC")
    btc_ret = {}
    if btc:
        c = btc[0].close
        btc_ret = dict(zip(btc[0].close_ms.tolist(),
                           np.concatenate([[np.nan], c[1:] / c[:-1] - 1]).tolist(), strict=True))
    price = {a: Decimal(str(v[0].close[-1])) for a, v in views.items()}
    candidates: list[dict] = []

    for name, (s, ctx, _month_of) in views.items():
        last = s.close.size - 1
        events, sides, atrs = fs.shocks(s)
        if name in members and events.size and events[-1] == last:
            side, atr = int(sides[-1]), float(atrs[-1])
            pending.append({"asset": name, "shock_ms": int(s.close_ms[last]), "side": side,
                            "atr": atr})
            entry = fs.fill_at_shock(s, last, side, atr)
            if entry:
                candidates.append(_candidate("at_shock", name, s, ctx, entry, assets, btc_ret))
        for p in [p for p in pending if p["asset"] == name]:
            t = int(np.searchsorted(s.close_ms, p["shock_ms"]))
            if t >= s.close.size or s.close_ms[t] != p["shock_ms"]:
                pending.remove(p)
                continue
            entry = fs.fill(s, t, p["side"], p["atr"])
            if entry is not None:
                candidates.append(_candidate("event", name, s, ctx, entry, assets, btc_ret))
                pending.remove(p)
            elif last >= t + fs.ORDER_LIFE_BARS:
                pending.remove(p)

    for c in candidates:
        awaiting.append({k: v for k, v in c.items() if k not in ("entry",)} |
                        {"entry_fill": asdict(c["entry"])})

    for book in books:
        run_book(book, now, setting, models[book.kind], candidates, views, price, assets)
        rows = [_snapshot(book, a, price[a], setting) for a in
                {*book.engine.account.positions, *[t["asset"] for t in book.trades]}
                if a in price]
        store.write(book.run_id, now, rows, book.equity(price), Decimal(str(book.gross(price))))
        book.save()

    settle(views, awaiting, assets)
    print(f"{now:%Y-%m-%d %H:%M} candidates {len(candidates)} pending {len(pending)} "
          f"awaiting {len(awaiting)} | " + " | ".join(
              f"{b.name} open {len(b.trades)} eq {b.equity(price):,.2f}" for b in books),
          flush=True)


def _candidate(kind: str, name: str, s: fs.Series, ctx: fs.Context, entry: fs.Fill,
               assets: dict[str, Asset], btc_ret: dict) -> dict:
    day = datetime.fromtimestamp(s.close_ms[entry.fill] / 1000, UTC).date()
    feats = fs.features(s, ctx, entry, assets[name].funding_z(day),
                        btc_ret.get(int(s.close_ms[entry.shock]), float("nan")))
    volume_1h = fs.trailing_mean(s.quote_volume, 12)[entry.fill]
    return {"kind": kind, "asset": name, "shock_ms": int(s.close_ms[entry.shock]),
            "fill_ms": int(s.close_ms[entry.fill]), "side": entry.side, "entry": entry,
            "atr": entry.atr, "entry_market": entry.market,
            "cap_notional": float(0.01 * volume_1h) if np.isfinite(volume_1h) else 0.0,
            "month": datetime.fromtimestamp(s.close_ms[entry.fill] / 1000, UTC)
            .strftime("%Y-%m"), **feats}


def run_book(book: Book, now: datetime, setting, decision: MonthDecision,
             candidates: list[dict], views: dict, price: dict[str, Decimal],
             assets: dict[str, Asset]) -> None:
    engine, adapter = book.engine, book.adapter
    adapter.prices.update(price)
    equity = book.equity(price)
    engine.risk.mark_equity(equity)
    today = now.strftime("%Y-%m-%d")
    if book.day != today:
        engine.account.day_start_equity, book.day = equity, today

    for trade in list(book.trades):                      # exits
        view = views.get(trade["asset"])
        if view is None:
            continue
        s = view[0]
        entry = fs.Fill(**{**trade["fill"], "fill": int(np.searchsorted(s.close_ms,
                                                                        trade["fill_ms"]))})
        sl, tp, h = fs.CONFIGS[trade["config"]]
        out = fs.exit_trade(s, entry, sl, tp, h, assets[trade["asset"]].funding_per_bar(s.close_ms),
                            upto=s.close.size - 1)
        if out is None:
            continue
        qty = Decimal(trade["qty"])
        side = Side.SELL if trade["side"] > 0 else Side.BUY
        if out.reason == 1:                          # take profit: a resting limit, maker
            order = Order(trade["asset"], side, qty, OrderType.LIMIT,
                          limit_price=Decimal(str(out.price)), reduce_only=True)
        else:                                        # stop or time: market, at the model's price
            adapter.prices[trade["asset"]] = Decimal(str(out.price))
            order = Order(trade["asset"], side, qty, reduce_only=True)
        engine.submit(order, adapter.prices.get(trade["asset"], price[trade["asset"]]),
                      dict(price), now)
        adapter.prices[trade["asset"]] = price[trade["asset"]]
        book.trades.remove(trade)

    mine = [c for c in candidates if c["kind"] == book.kind]
    if mine:
        frame = pl.DataFrame([{**{k: v for k, v in c.items() if k != "entry"},
                               "entry": c["entry"].price} for c in mine])
        p = decision.fitted.predict(matrix(frame))
        threshold = breakeven(frame, decision.config)
        taken = sorted((i for i in range(len(mine)) if p[i] > threshold[i]),
                       key=lambda i: -p[i])[:1]           # one new entry per bar
        for i in taken:
            c = mine[i]
            if (len(book.trades) >= MAX_OPEN or engine.account.halted
                    or c["asset"] in {t["asset"] for t in book.trades}):
                continue
            if engine.account.day_start_equity and \
                    (equity - engine.account.day_start_equity) / engine.account.day_start_equity \
                    <= -Decimal(str(engine.risk.limits.max_daily_loss)):
                continue
            entry: fs.Fill = c["entry"]
            sl, _, _ = fs.CONFIGS[decision.config]
            stop = sl * entry.atr / entry.price
            notional = min(RISK_PER_TRADE / max(stop, 1e-6), MAX_POSITION,
                           c["cap_notional"] / float(equity)) * float(equity)
            if book.tilted and setting.trend != NEUTRAL:
                up, down = exposure_multipliers(setting.trend)
                notional *= up if entry.side > 0 else down
            if notional <= 0 or book.gross(price) + notional > MAX_GROSS * float(equity):
                continue
            qty = engine.instruments[c["asset"]].quantise(Decimal(str(notional / entry.price)))
            if qty <= 0:
                continue
            side = Side.BUY if entry.side > 0 else Side.SELL
            order = (Order(c["asset"], side, qty) if entry.market else
                     Order(c["asset"], side, qty, OrderType.LIMIT,
                           limit_price=Decimal(str(entry.price))))
            # The deviation guard (§14.1) is for corrupt feeds. A pullback fill sits at the
            # shock's VWAP, often >50 bp from the last close, and the backtest's book did not
            # apply it; the reference for a bar-model fill is its own price.
            reference = price[c["asset"]] if entry.market else Decimal(str(entry.price))
            decision_ = engine.submit(order, Decimal(str(entry.price)), dict(price), now,
                                      mid_price=reference)
            if decision_.allowed:
                book.trades.append({"asset": c["asset"], "fill": asdict(entry),
                                    "fill_ms": c["fill_ms"], "config": decision.config,
                                    "qty": str(qty), "side": entry.side,
                                    "trend": setting.trend, "source": setting.source})

    engine.trim_held(price, now)
    engine.apply_fills(now)
    for asset, position in engine.account.positions.items():   # funding on what is held
        if not position.is_open or asset not in assets:
            continue
        seen = book.funding_seen.get(asset, int(now.timestamp() * 1000))
        for stamp, rate in assets[asset].funding:
            if stamp > seen:
                book.funding_paid[asset] = book.funding_paid.get(asset, Decimal(0)) + \
                    position.quantity * price[asset] * Decimal(str(rate))
                seen = max(seen, stamp)
        book.funding_seen[asset] = seen
    engine.risk.enforce(now)


def settle(views: dict, awaiting: list[dict], assets: dict[str, Asset]) -> None:
    """Candidates whose longest horizon has passed get their outcome under all twelve
    configurations, and join the forward training set."""
    done = []
    for c in list(awaiting):
        view = views.get(c["asset"])
        if view is None:
            continue
        s = view[0]
        f = int(np.searchsorted(s.close_ms, c["fill_ms"]))
        if f >= s.close.size or s.close.size - 1 < f + LONGEST_BARS + 1:
            continue
        entry = fs.Fill(**{**c["entry_fill"], "fill": f,
                           "shock": int(np.searchsorted(s.close_ms, c["shock_ms"]))})
        row = {k: v for k, v in c.items() if k != "entry_fill"}
        for i, (sl, tp, h) in enumerate(fs.CONFIGS):
            o = fs.exit_trade(s, entry, sl, tp, h, assets[c["asset"]].funding_per_bar(s.close_ms))
            if o is None:
                break
            row[f"net_{i}"], row[f"reason_{i}"] = o.net, o.reason
            row[f"exit_ms_{i}"] = int(s.close_ms[o.exit_bar])
        else:
            done.append(row)
            awaiting.remove(c)
    if done:
        path = STATE / "candidates.parquet"
        frame = pl.DataFrame(done)
        if path.exists():
            frame = pl.concat([pl.read_parquet(path), frame], how="diagonal_relaxed")
        frame.write_parquet(path)


def _snapshot(book: Book, asset: str, price: Decimal, setting) -> Snapshot:
    p = book.engine.account.positions.get(asset)
    qty = p.quantity if p else Decimal(0)
    trade = next((t for t in book.trades if t["asset"] == asset), None)
    guide = None
    if book.tilted and qty:
        guide = "operator" if (trade or {}).get("trend", setting.trend) != NEUTRAL else "algorithm"
    return Snapshot(asset, qty, p.avg_price if p else Decimal(0),
                    p.unrealised(price) if p else Decimal(0),
                    p.realised_pnl if p else Decimal(0),
                    book.funding_paid.get(asset, Decimal(0)), guide,
                    (trade or {}).get("trend") if qty else None,
                    (trade or {}).get("source") if qty else None)


if __name__ == "__main__":
    raise SystemExit(main())
