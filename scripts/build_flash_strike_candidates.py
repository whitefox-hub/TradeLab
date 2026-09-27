#!/usr/bin/env python
"""Every Flash Strike candidate, filled or not, with its features and outcomes.

research/PREREG-008-flash-strike.md, rules 1-3 and the exits. For each asset,
the 5-minute series is assembled across every downloaded month; shocks are
taken only in months the asset belongs to the universe, the earlier month
serving as warm-up. Each filled candidate carries the thirteen features and
its outcome under all twelve exit configurations.

Alongside, the **random-entry baseline** the registration requires: in each
asset-month, as many random bars as there were shocks, a random side, and the
same limit-at-VWAP entry and the same exits, with no anomaly and no
persistence check. It says what the anomaly adds.

Per-trade outcomes are written here; nothing is aggregated. The model, the
book and the verdict belong to run_prereg_008.py.

    python scripts/build_flash_strike_candidates.py
"""

from __future__ import annotations

import argparse
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.strategy import flash_strike as fs

STEP = 300_000
SEED = 20260927


def load(root: Path, asset: str) -> pl.DataFrame | None:
    """One asset's bars on a complete 5-minute grid, with premium and metrics.

    A missing bar becomes a zero-volume bar at the previous close, so bar
    arithmetic (30 minutes = 6 bars) stays true. Metrics are joined backward
    within ten minutes, so a stale snapshot is absent rather than carried.
    """
    def read(kind: str) -> pl.DataFrame | None:
        files = sorted((root / kind / asset).glob("*.parquet"))
        frames = [f for f in (pl.read_parquet(p) for p in files) if not f.is_empty()]
        return (pl.concat(frames).unique("available_ms").sort("available_ms")
                if frames else None)

    klines = read("klines")
    if klines is None:
        return None
    grid = pl.DataFrame({"available_ms": np.arange(
        klines["available_ms"].min(), klines["available_ms"].max() + 1, STEP)})
    bars = grid.join(klines, on="available_ms", how="left").with_columns(
        pl.col("close").forward_fill())
    bars = bars.with_columns(
        [pl.col(c).fill_null(pl.col("close")) for c in ("open", "high", "low")]
        + [pl.col(c).fill_null(0) for c in ("volume", "quote_volume", "trades",
                                            "taker_buy_volume", "taker_buy_quote")])
    premium = read("premiumIndexKlines")
    if premium is not None:
        bars = bars.join(premium.select("available_ms", pl.col("close").alias("premium")),
                         on="available_ms", how="left")
    else:
        bars = bars.with_columns(pl.lit(None, dtype=pl.Float64).alias("premium"))
    metrics = read("metrics")
    if metrics is not None:
        bars = bars.sort("available_ms").join_asof(
            metrics.select(pl.col("available_ms").alias("snap_ms"), "open_interest",
                           pl.col("toptrader_position_ratio").alias("toptrader")),
            left_on="available_ms", right_on="snap_ms", strategy="backward",
            tolerance=2 * STEP)
    else:
        bars = bars.with_columns(pl.lit(None, dtype=pl.Float64).alias("open_interest"),
                                 pl.lit(None, dtype=pl.Float64).alias("toptrader"))
    months = bars.select((pl.col("available_ms") - 1).cast(pl.Datetime("ms", "UTC"))
                         .dt.strftime("%Y-%m").alias("month"))["month"]
    ticks = {m: fs.infer_tick(g["close"].to_numpy())
             for (m,), g in bars.with_columns(months.alias("m")).group_by("m")}
    return bars.with_columns(months.alias("month"),
                             months.replace_strict(ticks, return_dtype=pl.Float64).alias("tick"))


def series(bars: pl.DataFrame) -> fs.Series:
    def col(name: str) -> np.ndarray:
        return bars[name].cast(pl.Float64).to_numpy()
    return fs.Series(
        close_ms=bars["available_ms"].to_numpy(), open=col("open"), high=col("high"),
        low=col("low"), close=col("close"), volume=col("volume"),
        quote_volume=col("quote_volume"), trades=col("trades"),
        taker_buy_quote=col("taker_buy_quote"), premium=col("premium"),
        open_interest=col("open_interest"), toptrader_ratio=col("toptrader"),
        tick=col("tick"))


def funding_per_bar(panel: Path, asset: str, close_ms: np.ndarray) -> tuple[np.ndarray, dict]:
    """Settlement-level funding, placed on the bar whose close is the settlement.

    The panel holds daily sums and settlement counts. Settlements are taken as
    evenly spaced from 00:00 UTC, which is Binance's schedule for 1, 4 and 8
    hour contracts alike. Also returns each day's funding z against the
    asset's previous 30 days, for feature 7.
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
    # Yesterday's funding against the 30 days before yesterday: known at any
    # fill today, and not part of its own reference.
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

    for kind, triples in (("event", zip(events, sides, atrs, strict=True)),
                          ("random", randoms)):
        for t, side, atr in triples:
            t, side = int(t), int(side)
            if kind == "event":
                entry = fs.fill(s, t, side, float(atr))
                shocked.append(_shock_row(s, asset, month[t], t, side, entry))
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
                   "cap_notional": float(0.01 * volume_1h[entry.fill])
                   if np.isfinite(volume_1h[entry.fill]) else 0.0,
                   **feats}
            for c, o in enumerate(outcomes):
                assert o is not None
                row[f"net_{c}"] = o.net
                row[f"reason_{c}"] = o.reason
                row[f"exit_ms_{c}"] = int(s.close_ms[o.exit_bar])
            rows.append(row)
    # Every shock, filled or not: the fill rate is reported, and a table of
    # fills alone cannot say what share of shocks it is.
    counts = pl.DataFrame(shocked, schema=SHOCK_SCHEMA, orient="row") if shocked \
        else pl.DataFrame(schema=SHOCK_SCHEMA)
    return (pl.DataFrame(rows) if rows else pl.DataFrame(), counts)


SHOCK_SCHEMA = {"asset": pl.Utf8, "month": pl.Utf8, "shock_ms": pl.Int64, "side": pl.Int8,
                "filled": pl.Boolean, "after_15m": pl.Float64}


def _shock_row(s: fs.Series, asset: str, month: str, t: int, side: int,
               entry: fs.Fill | None) -> dict:
    """Every shock, filled or not, for the fill rate and the adverse selection.

    `after_15m` is the side-adjusted move from the limit price to the close 15
    minutes after the order's decision bar: its fill, or its expiry. Filled
    limits are expected to do worse than unfilled ones. That gap is what a
    resting order pays for being the one the market chose to hit, and it is
    reported. It is not charged, because the backtest's outcomes already run on
    the prices after each real fill.
    """
    vwap = s.quote_volume[t] / s.volume[t] if s.volume[t] > 0 else np.nan
    decision = entry.fill if entry else min(t + fs.ORDER_LIFE_BARS, s.close.size - 1)
    later = s.close[min(decision + 3, s.close.size - 1)]
    return {"asset": asset, "month": month, "shock_ms": int(s.close_ms[t]), "side": side,
            "filled": entry is not None, "after_15m": float(side * (later - vwap) / vwap)}


def _random_fill(s: fs.Series, t: int, side: int, atr: float) -> fs.Fill | None:
    """The same resting limit at the bar's VWAP and the same trade-through,
    without an anomaly to anchor it or persistence to confirm it."""
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
    # Spawn, not fork: a forked child inherits polars' thread pool mid-state and
    # waits on it forever. The first run of this sat for ten minutes at 2.6 s
    # of CPU before anyone noticed it was not working but waiting.
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
