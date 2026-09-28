#!/usr/bin/env python
"""Every Flash Strike candidate, filled or not, with features and outcomes per exit config.

research/PREREG-008-flash-strike.md, rules 1-3 and the exits. Shocks only in universe
member months (prior month is warm-up). Also the registered random-entry baseline: same
count per asset-month, random side, same limit-at-VWAP entry and exits. Per-trade only;
aggregation and verdict belong to run_prereg_008.py.

    python scripts/build_flash_strike_candidates.py [--only BTC ETH]
"""

from __future__ import annotations

import argparse
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.strategy import flash_data
from tradelab.strategy import flash_strike as fs

SEED = 20260927


def load(root: Path, asset: str) -> pl.DataFrame | None:
    """One asset's bars via `flash_data.assemble`, shared with the forward simulation so
    the model trades on inputs built exactly as in training."""
    def read(kind: str) -> pl.DataFrame | None:
        files = sorted((root / kind / asset).glob("*.parquet"))
        frames = [f for f in (pl.read_parquet(p) for p in files) if not f.is_empty()]
        return (pl.concat(frames).unique("available_ms").sort("available_ms")
                if frames else None)

    klines = read("klines")
    if klines is None:
        return None
    return flash_data.assemble(klines, read("premiumIndexKlines"), read("metrics"))


def series(bars: pl.DataFrame) -> fs.Series:
    return flash_data.series(bars)


def funding_per_bar(panel: Path, asset: str, close_ms: np.ndarray) -> tuple[np.ndarray, dict]:
    """Per-bar funding at settlement closes, plus each day's funding z (feature 7).

    Daily sums are spread over `n` settlements evenly spaced from 00:00 UTC (Binance's
    schedule for 1/4/8h contracts).
    """
    out = np.zeros(close_ms.size)
    path = panel / f"{asset}.parquet"
    if not path.exists():
        return out, {}
    daily = pl.read_parquet(path).drop_nulls("rate").sort("day")
    index = {int(ms): i for i, ms in enumerate(close_ms)}
    for day, rate, n in daily.select("day", "rate", "n").iter_rows():
        if not n:
            continue
        start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
        for k in range(int(n)):
            i = index.get(start + k * (86_400_000 // int(n)))
            if i is not None:
                out[i] += rate / n
    rates = daily["rate"].to_numpy()
    z = {}
    # Yesterday's funding vs the 30 days before it: known at any fill today.
    for i, day in enumerate(daily["day"].to_list()):
        if i < 2:
            continue
        window = rates[max(i - 31, 0):i - 1]
        if window.size >= 10 and window.std() > 0:
            z[day] = float((rates[i - 1] - window.mean()) / window.std())
    return out, z


def candidates_for(root: Path, panel: Path, asset: str, members: set[str],
                   btc: dict[int, float]) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    bars = load(root, asset)
    if bars is None:
        return None
    s = series(bars)
    month = bars["month"].to_list()
    funding, funding_z = funding_per_bar(panel, asset, s.close_ms)
    rng = np.random.default_rng([SEED, sum(map(ord, asset))])
    volume_1h = fs.trailing_mean(s.quote_volume, 12)
    ctx = fs.prepare(s)

    rows: list[dict] = []
    shocked: list[dict] = []
    events, sides, atrs = fs.shocks(s)
    in_universe = np.array([month[t] in members for t in events], dtype=bool)
    events, sides, atrs = events[in_universe], sides[in_universe], atrs[in_universe]

    atr_all = ctx.atr
    per_month: dict[str, int] = {}
    for t in events:
        per_month[month[t]] = per_month.get(month[t], 0) + 1
    randoms = []
    for m, count in per_month.items():
        pool = np.flatnonzero((np.array(month) == m) & np.isfinite(atr_all))
        if pool.size:
            picks = rng.choice(pool, size=min(count, pool.size), replace=False)
            randoms += [(int(t), int(rng.choice([-1, 1])), float(atr_all[t])) for t in picks]

    shock_triples = list(zip(events, sides, atrs, strict=True))
    for kind, triples in (("event", shock_triples), ("random", randoms),
                          ("at_shock", shock_triples)):   # PREREG-010's entry, same shocks
        for t, side, atr in triples:
            t, side = int(t), int(side)
            if kind == "event":
                entry = fs.fill(s, t, side, float(atr))
                shocked.append(_shock_row(s, asset, month[t], t, side, entry))
            elif kind == "at_shock":
                entry = fs.fill_at_shock(s, t, side, float(atr))
            else:
                entry = _random_fill(s, t, side, float(atr))
            if entry is None:
                continue
            outcomes = [fs.exit_trade(s, entry, sl, tp, h, funding)
                        for sl, tp, h in fs.CONFIGS]
            if any(o is None for o in outcomes):
                continue
            day = datetime.fromtimestamp(s.close_ms[entry.fill] / 1000, UTC).date()
            feats = fs.features(s, ctx, entry, funding_z.get(day, np.nan),
                                btc.get(int(s.close_ms[t]), np.nan))
            row = {"asset": asset, "kind": kind, "month": month[t],
                   "shock_ms": int(s.close_ms[t]), "fill_ms": int(s.close_ms[entry.fill]),
                   "side": side, "entry": entry.price, "atr": entry.atr,
                   "entry_market": entry.market,
                   "cap_notional": float(0.01 * volume_1h[entry.fill])
                   if np.isfinite(volume_1h[entry.fill]) else 0.0,
                   **feats}
            for c, o in enumerate(outcomes):
                assert o is not None
                row[f"net_{c}"] = o.net
                row[f"reason_{c}"] = o.reason
                row[f"exit_ms_{c}"] = int(s.close_ms[o.exit_bar])
            rows.append(row)
    # All shocks, filled or not, so the fill rate can be reported.
    counts = pl.DataFrame(shocked, schema=SHOCK_SCHEMA, orient="row") if shocked \
        else pl.DataFrame(schema=SHOCK_SCHEMA)
    return (pl.DataFrame(rows) if rows else pl.DataFrame(), counts)


SHOCK_SCHEMA = {"asset": pl.Utf8, "month": pl.Utf8, "shock_ms": pl.Int64, "side": pl.Int8,
                "filled": pl.Boolean, "after_15m": pl.Float64}


def _shock_row(s: fs.Series, asset: str, month: str, t: int, side: int,
               entry: fs.Fill | None) -> dict:
    """One shock row for the fill rate and adverse selection.

    `after_15m`: side-adjusted move from the limit to the close 15 min after the decision
    bar (fill or expiry). Reported, not charged: outcomes already use post-fill prices.
    """
    vwap = s.quote_volume[t] / s.volume[t] if s.volume[t] > 0 else np.nan
    decision = entry.fill if entry else min(t + fs.ORDER_LIFE_BARS, s.close.size - 1)
    later = s.close[min(decision + 3, s.close.size - 1)]
    return {"asset": asset, "month": month, "shock_ms": int(s.close_ms[t]), "side": side,
            "filled": entry is not None, "after_15m": float(side * (later - vwap) / vwap)}


def _random_fill(s: fs.Series, t: int, side: int, atr: float) -> fs.Fill | None:
    """Baseline fill: limit at the bar's VWAP, same trade-through rule, no anomaly check."""
    if s.volume[t] <= 0 or not np.isfinite(atr):
        return None
    vwap = s.quote_volume[t] / s.volume[t]
    for f in range(t + 1, min(t + fs.ORDER_LIFE_BARS, s.close.size - 1) + 1):
        through = (s.low[f] <= vwap - s.tick[f]) if side > 0 else (s.high[f] >= vwap + s.tick[f])
        if through:
            return fs.Fill(t, f, side, float(vwap), atr)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet/intraday"))
    parser.add_argument("--panel", type=Path, default=Path("data/parquet/binance_pit"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only", nargs="*", default=None,
                        help="a few assets, for a smoke test; writes _candidates_smoke")
    args = parser.parse_args()

    universe = pl.read_parquet(args.root / "_universe_monthly.parquet")
    members: dict[str, set[str]] = {}
    for asset, month in universe.select("asset", "month").iter_rows():
        members.setdefault(asset, set()).add(f"{month:%Y-%m}")

    btc_bars = load(args.root, "BTC")
    if btc_bars is None and not args.only:
        raise SystemExit("BTC is in every month's universe and must be downloaded first")
    btc: dict[int, float] = {}
    if btc_bars is not None:
        btc_close = btc_bars["close"].to_numpy()
        btc = dict(zip(btc_bars["available_ms"].to_list(),
                       np.concatenate([[np.nan], btc_close[1:] / btc_close[:-1] - 1]).tolist(),
                       strict=True))
    if args.only:
        members = {a: m for a, m in members.items() if a in set(args.only)}

    frames: list[pl.DataFrame] = []
    shock_frames: list[pl.DataFrame] = []
    done = 0
    # Spawn, not fork: a forked child inherits polars' thread pool mid-state and deadlocks.
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=context) as pool:
        futures = {pool.submit(candidates_for, args.root, args.panel, a, m, btc): a
                   for a, m in sorted(members.items())}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                if not result[0].is_empty():
                    frames.append(result[0])
                shock_frames.append(result[1])
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(futures)} assets", flush=True)
    suffix = "_smoke" if args.only else ""
    pl.concat(shock_frames).write_parquet(args.root / f"_shocks{suffix}.parquet")
    out = pl.concat(frames, how="diagonal_relaxed").sort("fill_ms")
    out.write_parquet(args.root / f"_candidates{suffix}.parquet")
    counts = out.group_by("kind").agg(pl.len()).sort("kind").to_dicts()
    print(f"candidates written: {counts}, "
          f"{out.filter(pl.col('kind') == 'event')['asset'].n_unique()} assets, "
          f"built {date.today()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
