#!/usr/bin/env python
"""The shock bank for the Flash Strike search: every large 5-minute move, with what each
variant of the trade would have earned.

A move is banked when it reaches the asset's trailing 30-day 95th percentile in ATR, in a
universe-member month. For each, the net return on notional of a market entry at the close
of the shock bar or 1 or 3 bars later, following or fading the move, under every stop, target
and horizon in the grid. Costs and fills are `flash_strike`'s: taker in, target a maker limit
needing a trade-through, stop first in a bar touching both, gaps at the bar's worst price,
funding at settlements. Only data known at the entry is kept as a filter.

    python scripts/build_flash_bank.py [--root data/parquet/intraday] [--panel ...]
"""

from __future__ import annotations

import argparse
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from pathlib import Path

import numpy as np
import polars as pl
from build_flash_strike_candidates import funding_per_bar, load

from tradelab.strategy import flash_strike as fs

LEVELS = (0.95, 0.99, 0.995)
DELAYS = (0, 1, 3)
DIRS = (("follow", 1), ("fade", -1))
SLS = (0.5, 1.0, 2.0)
TPS = (0.5, 1.0, 2.0, 3.0)                  # multiples of the stop distance
HOLDS = (15, 30, 60, 120)                   # minutes
HMAX = max(HOLDS) // 5
VARIANTS = tuple(product(DELAYS, (d for d, _ in DIRS), SLS, TPS, HOLDS))


def column(k: int, d: str, sl: float, tp: float, h: int) -> str:
    return f"n_{k}_{d}_{sl:g}_{tp:g}_{h}"


def cooled(fired: np.ndarray) -> np.ndarray:
    keep = np.zeros(fired.size, dtype=bool)
    last = -fs.COOLDOWN_BARS - 1
    for t in np.flatnonzero(fired):
        if t - last > fs.COOLDOWN_BARS:
            keep[t] = True
            last = t
    return keep


def bank_for(root: Path, panel: Path, asset: str, members: set[str],
             btc_ret: dict[int, float]) -> pl.DataFrame | None:
    bars = load(root, asset)
    if bars is None:
        return None
    s = fs_series(bars)
    month = np.array(bars["month"].to_list())
    funding, _ = funding_per_bar(panel, asset, s.close_ms)
    cum_funding = np.concatenate([[0.0], np.cumsum(funding)])
    ctx = fs.prepare(s)
    atr = ctx.atr
    ratio = np.abs(s.close - s.open) / atr
    thresholds = {q: fs.trailing_quantile(ratio, fs.QUANTILE_BARS, q) for q in LEVELS}
    member = np.isin(month, list(members))
    fired = {q: np.isfinite(ratio) & np.isfinite(th) & (ratio >= th) & member
             for q, th in thresholds.items()}
    keep = {q: cooled(f) for q, f in fired.items()}
    events = np.flatnonzero(np.logical_or.reduce([keep[q] for q in LEVELS]))
    n = s.close.size
    events = events[events + max(DELAYS) + HMAX < n]
    if events.size == 0:
        return None
    sign = np.sign(s.close[events] - s.open[events])
    events, sign = events[sign != 0], sign[sign != 0]

    out: dict[str, np.ndarray] = {
        "shock_ms": s.close_ms[events].astype(np.int64),
        "sign": sign.astype(np.int8),
        "ratio": ratio[events].astype(np.float32),
        **{f"at_{q:g}": keep[q][events] for q in LEVELS},
        "atr_pct": (atr[events] / s.close[events]).astype(np.float32),
        "volume_z": ((s.quote_volume[events] - ctx.volume_mean[events])
                     / np.where(ctx.volume_std[events] > 0, ctx.volume_std[events], np.nan)
                     ).astype(np.float32),
        "taker_share": np.divide(s.taker_buy_quote[events], s.quote_volume[events],
                                 out=np.full(events.size, 0.5),
                                 where=s.quote_volume[events] > 0).astype(np.float32),
        "btc_ret": np.array([btc_ret.get(int(m), np.nan) for m in s.close_ms[events]],
                            dtype=np.float32),
        "ret_1h_before": (s.close[events] / s.close[np.maximum(events - 12, 0)] - 1
                          ).astype(np.float32),
        "vol_ratio": (ctx.vol_1h[events] / ctx.vol_24h[events]).astype(np.float32),
    }
    steps = np.arange(1, HMAX + 1)
    for k in DELAYS:
        e = events + k
        price = s.close[e]
        a = atr[e]
        win = e[:, None] + steps[None, :]
        hi, lo, op, cl = s.high[win], s.low[win], s.open[win], s.close[win]
        tick = s.tick[win]
        for d, flip in DIRS:
            side = (sign * flip)[:, None]
            worst = np.where(side > 0, lo, hi)
            best = np.where(side > 0, hi, lo)
            for sl in SLS:
                dist = (sl * a)[:, None]
                stop = price[:, None] - side * dist
                stopped = np.where(side > 0, worst <= stop, worst >= stop)
                gapped = np.where(side > 0, op <= stop, op >= stop)
                stop_px = np.where(gapped, worst, stop)
                first_stop = np.where(stopped.any(1), stopped.argmax(1), HMAX)
                stop_exit = stop_px[np.arange(e.size), np.minimum(first_stop, HMAX - 1)]
                for tp in TPS:
                    target = price[:, None] + side * tp * dist
                    hit = np.where(side > 0, best >= target + tick, best <= target - tick)
                    first_tp = np.where(hit.any(1), hit.argmax(1), HMAX)
                    for h in HOLDS:
                        hb = h // 5
                        by_stop = (first_stop < hb) & (first_stop <= first_tp)
                        by_tp = ~by_stop & (first_tp < hb)
                        exit_step = np.where(by_stop, first_stop,
                                             np.where(by_tp, first_tp, hb - 1))
                        exit_px = np.where(by_stop, stop_exit,
                                           np.where(by_tp, target[:, 0], cl[:, hb - 1]))
                        cost = fs.TAKER + np.where(by_tp, fs.MAKER, fs.TAKER)
                        exit_bar = e + 1 + exit_step
                        fund = cum_funding[exit_bar + 1] - cum_funding[e + 1]
                        s1 = side[:, 0]
                        net = s1 * (exit_px - price) / price - cost - s1 * fund
                        out[column(k, d, sl, tp, h)] = net.astype(np.float32)
    frame = pl.DataFrame(out).with_columns(pl.lit(asset).alias("asset"))
    return frame.with_columns(month=pl.from_epoch("shock_ms", time_unit="ms")
                              .dt.strftime("%Y-%m"))


def fs_series(bars: pl.DataFrame) -> fs.Series:
    from tradelab.strategy import flash_data
    return flash_data.series(bars)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet/intraday"))
    parser.add_argument("--panel", type=Path, default=Path("data/parquet/binance_pit"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    out = args.out or args.root / "_bank"
    out.mkdir(parents=True, exist_ok=True)

    universe = pl.read_parquet(args.root / "_universe_monthly.parquet")
    members: dict[str, set[str]] = {}
    for asset, month in universe.select("asset", "month").iter_rows():
        members.setdefault(asset, set()).add(f"{month:%Y-%m}")
    btc = load(args.root, "BTC")
    assert btc is not None
    c = btc["close"].to_numpy()
    btc_ret = dict(zip(btc["available_ms"].to_list(),
                       np.concatenate([[np.nan], c[1:] / c[:-1] - 1]).tolist(), strict=True))

    todo = {a: m for a, m in sorted(members.items()) if not (out / f"{a}.parquet").exists()}
    context = multiprocessing.get_context("spawn")
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=context) as pool:
        futures = {pool.submit(bank_for, args.root, args.panel, a, m, btc_ret): a
                   for a, m in todo.items()}
        for future in as_completed(futures):
            asset = futures[future]
            frame = future.result()
            (frame if frame is not None else pl.DataFrame({"asset": []})).write_parquet(
                out / f"{asset}.parquet")
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(futures)} assets", flush=True)
    print(f"bank complete: {len(members)} assets in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
