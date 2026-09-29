#!/usr/bin/env python
"""PREREG-013: a target above the costs, a capped loss, a waiting limit of days.

    python scripts/search_hold.py bank [--era seen|unseen]   # per-asset outcomes, resumable
    python scripts/search_hold.py search                     # discover + validate, seen era
    python scripts/search_hold.py unseen                     # the committed rules, 10-day windows
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import polars as pl
from build_flash_strike_candidates import funding_per_bar, load

from tradelab.strategy import flash_data
from tradelab.strategy import flash_strike as fs

TARGETS = (0.001, 0.003, 0.005, 0.010)
CAPS = (0.02, 0.03, 0.05, 0.10)
WAITS = (1, 3, 7)
UNIVERSES = ("all", "majors")
MAJORS = ("BTC", "ETH")
BARS_PER_DAY = 288
HMAX = max(WAITS) * BARS_PER_DAY
RISK = 0.0025
ERAS = {"seen": (Path("data/parquet/intraday"), Path("data/parquet/binance_pit")),
        "unseen": (Path("data/parquet/intraday_2022"), Path("data/parquet/binance_pit_2022"))}
DISCOVER = (date(2024, 11, 1), date(2025, 10, 31))
VALIDATE = (date(2025, 11, 1), date(2026, 9, 24))
UNSEEN_START = date(2022, 12, 1)
WINDOW_DAYS = 10
OUT = Path("research/search_013")
TOP = 20


def column(x: float, cap: float, wait: int) -> str:
    return f"n_{x:g}_{cap:g}_{wait}"


def bank_for(root: Path, panel: Path, asset: str, members: set[str]) -> pl.DataFrame | None:
    bars = load(root, asset)
    if bars is None:
        return None
    s = flash_data.series(bars)
    month = bars["month"].to_list()
    funding, _ = funding_per_bar(panel, asset, s.close_ms)
    cum = np.concatenate([[0.0], np.cumsum(funding)])
    events, sides, atrs = fs.shocks(s)
    keep = [i for i, t in enumerate(events) if month[t] in members
            and fs.fill_at_shock(s, int(t), int(sides[i]), float(atrs[i])) is not None]
    events, sides = events[keep], sides[keep].astype(np.float64)
    n = s.close.size
    ok = events + HMAX < n
    events, sides = events[ok], sides[ok]
    if events.size == 0:
        return None
    price = s.close[events]
    win = events[:, None] + np.arange(1, HMAX + 1)[None, :]
    side = sides[:, None]
    worst = np.where(side > 0, s.low[win], s.high[win])
    best = np.where(side > 0, s.high[win], s.low[win])
    opens, closes = s.open[win], s.close[win]
    rows = np.arange(events.size)
    out: dict[str, np.ndarray] = {"shock_ms": s.close_ms[events].astype(np.int64),
                                  "side": sides.astype(np.int8)}
    for cap in CAPS:
        stop = price[:, None] * (1 - side * cap)
        stopped = np.where(side > 0, worst <= stop, worst >= stop)
        first_stop = np.where(stopped.any(1), stopped.argmax(1), HMAX)
        gapped = np.where(side > 0, opens <= stop, opens >= stop)
        stop_px = np.where(gapped, worst, stop)[rows, np.minimum(first_stop, HMAX - 1)]
        for x in TARGETS:
            target = price * (1 + sides * (x + fs.TAKER + fs.MAKER))
            hit = np.where(side > 0, best >= target[:, None], best <= target[:, None])
            first_tp = np.where(hit.any(1), hit.argmax(1), HMAX)
            for wait in WAITS:
                hb = wait * BARS_PER_DAY
                by_stop = (first_stop < hb) & (first_stop <= first_tp)
                by_tp = ~by_stop & (first_tp < hb)
                step = np.where(by_stop, first_stop, np.where(by_tp, first_tp, hb - 1))
                exit_px = np.where(by_stop, stop_px,
                                   np.where(by_tp, target, closes[rows, hb - 1]))
                cost = fs.TAKER + np.where(by_tp, fs.MAKER, fs.TAKER)
                bar = events + 1 + step
                fund = cum[bar + 1] - cum[events + 1]
                net = sides * (exit_px - price) / price - cost - sides * fund
                out[column(x, cap, wait)] = net.astype(np.float32)
                out[f"h_{x:g}_{cap:g}_{wait}"] = ((step + 1) / BARS_PER_DAY).astype(np.float32)
    return pl.DataFrame(out).with_columns(pl.lit(asset).alias("asset"))


def bank(era: str, workers: int) -> int:
    root, panel = ERAS[era]
    out = root / "_bank_013"
    out.mkdir(parents=True, exist_ok=True)
    universe = pl.read_parquet(root / "_universe_monthly.parquet")
    members: dict[str, set[str]] = {}
    for asset, month in universe.select("asset", "month").iter_rows():
        members.setdefault(asset, set()).add(f"{month:%Y-%m}")
    todo = {a: m for a, m in sorted(members.items()) if not (out / f"{a}.parquet").exists()}
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        futures = {pool.submit(bank_for, root, panel, a, m): a for a, m in todo.items()}
        for done, future in enumerate(as_completed(futures), 1):
            frame = future.result()
            (frame if frame is not None else pl.DataFrame({"asset": []})).write_parquet(
                out / f"{futures[future]}.parquet")
            if done % 50 == 0:
                print(f"  {done}/{len(futures)} assets", flush=True)
    print(f"bank complete: {len(members)} assets in {out}")
    return 0


def read_bank(era: str) -> pl.DataFrame:
    root, _ = ERAS[era]
    paths = sorted((root / "_bank_013").glob("*.parquet"))
    frames = [f for f in (pl.read_parquet(p) for p in paths) if f.height]
    return (pl.concat(frames).with_columns(
        day=pl.from_epoch("shock_ms", time_unit="ms").dt.date()).sort(["shock_ms", "asset"]))


def evaluate(rows: pl.DataFrame, days: pl.Series) -> dict:
    n = rows.height
    daily = (pl.DataFrame({"day": days}).join(
        rows.group_by("day").agg((pl.col("r").mean() * RISK).alias("ret")), on="day",
        how="left").fill_null(0.0))["ret"].to_numpy()
    sd = daily.std(ddof=1)
    return {"trades": n, "per_day": n / days.len(),
            "mean_net_bps": float(rows["net"].mean() * 1e4) if n else 0.0,
            "win": float((rows["net"] > 0).mean()) if n else 0.0,
            "mean_days_held": float(rows["held"].mean()) if n else 0.0,
            "sharpe": float(daily.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0,
            "return": float(np.prod(1 + daily) - 1)}


def trades(frame: pl.DataFrame, x: float, cap: float, wait: int, universe: str,
           lo: date, hi: date) -> pl.DataFrame:
    col = column(x, cap, wait)
    held = pl.col(f"h_{x:g}_{cap:g}_{wait}")
    rows = frame.filter((pl.col("day") >= lo) & (pl.col("day") <= hi))
    if universe == "majors":
        rows = rows.filter(pl.col("asset").is_in(MAJORS))
    # Drop a trade whose waiting limit would pass the span's end.
    end_ms = int(np.datetime64(hi, "ms").astype(np.int64)) + 86_400_000
    rows = rows.filter(pl.col("shock_ms") + wait * 86_400_000 <= end_ms)
    return rows.select("day", pl.col(col).alias("net"), held.alias("held"),
                       (pl.col(col) / cap).alias("r"))


def span(lo: date, hi: date) -> pl.Series:
    return pl.date_range(lo, hi, "1d", eager=True).alias("day")


def search() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = read_bank("seen")
    table = []
    for trial, (x, cap, wait, universe) in enumerate(product(TARGETS, CAPS, WAITS, UNIVERSES), 1):
        rows = trades(frame, x, cap, wait, universe, *DISCOVER)
        table.append({"trial": trial, "x": x, "cap": cap, "wait": wait, "universe": universe,
                      **{f"d_{k}": v for k, v in evaluate(rows, span(*DISCOVER)).items()}})
    disc = pl.DataFrame(table)
    disc.write_parquet(OUT / "discover.parquet")
    checked = []
    for row in disc.sort("d_sharpe", descending=True).head(TOP).iter_rows(named=True):
        rows = trades(frame, row["x"], row["cap"], row["wait"], row["universe"], *VALIDATE)
        checked.append({**row, **{f"v_{k}": v for k, v in
                                  evaluate(rows, span(*VALIDATE)).items()}})
    val = pl.DataFrame(checked)
    val.write_parquet(OUT / "validate.parquet")
    passed = val.filter((pl.col("v_sharpe") > 1) & (pl.col("v_mean_net_bps") > 0)
                        & (pl.col("v_per_day") >= 0.2)).head(3)
    summary = {"trials": disc.height, "positive_discover": int((disc["d_sharpe"] > 0).sum()),
               "passed_validation": passed.height,
               "forward": passed.select("x", "cap", "wait", "universe").to_dicts()}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pl.Config.set_tbl_width_chars(250)
    pl.Config.set_tbl_cols(30)
    pl.Config.set_tbl_rows(30)
    pl.Config.set_float_precision(3)
    print(json.dumps({k: v for k, v in summary.items() if k != "forward"}, indent=2))
    print(val.select("x", "cap", "wait", "universe", "d_per_day", "d_win", "d_mean_net_bps",
                     "d_sharpe", "v_per_day", "v_win", "v_mean_net_bps", "v_mean_days_held",
                     "v_sharpe", "v_return"))
    return 0


def unseen() -> int:
    chosen = json.loads((OUT / "summary.json").read_text())["forward"]
    if not chosen:
        raise SystemExit("no rule passed validation; the unseen era stays sealed")
    frame = read_bank("unseen")
    last = frame["day"].max()
    report = []
    for spec in chosen:
        rows = trades(frame, spec["x"], spec["cap"], spec["wait"], spec["universe"],
                      UNSEEN_START, last)
        days = span(UNSEEN_START, last)
        daily = (pl.DataFrame({"day": days}).join(
            rows.group_by("day").agg((pl.col("r").mean() * RISK).alias("ret")), on="day",
            how="left").fill_null(0.0))["ret"].to_numpy()
        windows = []
        for w0 in range(0, daily.size, WINDOW_DAYS):
            part = daily[w0:w0 + WINDOW_DAYS]
            cum = float(np.prod(1 + daily[:w0 + part.size]) - 1)
            windows.append({"start": str(days[w0]), "window_return": float(np.prod(1 + part) - 1),
                            "cumulative": cum})
            if cum <= 0:
                break
        reached = w0 + part.size
        sd = daily[:reached].std(ddof=1)
        report.append({"rule": spec, "windows": windows, "reached_days": reached,
                       "sharpe": float(daily[:reached].mean() / sd * np.sqrt(365)) if sd else 0.0,
                       "threshold": float(1.65 / np.sqrt(reached / 365))})
    (OUT / "unseen.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("bank", "search", "unseen"))
    parser.add_argument("--era", choices=tuple(ERAS), default="seen")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.stage == "bank":
        return bank(args.era, args.workers)
    return search() if args.stage == "search" else unseen()


if __name__ == "__main__":
    raise SystemExit(main())
