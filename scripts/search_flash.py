#!/usr/bin/env python
"""The Flash Strike search, research/PREREG-011: every rule in the grid on the shock bank,
discovered on one span, the survivors checked on the next.

A rule is a shock level, a filter, the sides allowed, and one bank variant (delay, follow or
fade, stop, target, horizon). Each is scored as a signal: every qualifying shock is a trade
at fixed risk, so the verdict is the signal's, not the book's. The daily return is the mean
R of that day's trades times the 0.25% risk; the Sharpe is on calendar days, days without a
trade counting as zero.

    python scripts/search_flash.py [--bank data/parquet/intraday/_bank]
"""

from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

import numpy as np
import polars as pl
from build_flash_bank import LEVELS, VARIANTS, column

RISK = 0.0025
DISCOVER = ("2024-11-01", "2025-11-01")
VALIDATE = ("2025-11-01", "2026-09-27")
FILTERS = {
    "none": pl.lit(True),
    "taker_agrees": ((pl.col("sign") > 0) & (pl.col("taker_share") > 0.5))
    | ((pl.col("sign") < 0) & (pl.col("taker_share") < 0.5)),
    "volume_z_2": pl.col("volume_z") > 2,
    "btc_against": (pl.col("btc_ret") * pl.col("sign")) < 0,
}
SIDES = ("both", "long", "short")
TOP = 20
MIN_TRADES_PER_DAY = 1.0


def load(bank: Path) -> pl.DataFrame:
    frames = [f for f in (pl.read_parquet(p) for p in sorted(bank.glob("*.parquet")))
              if f.height]
    return (pl.concat(frames, how="diagonal_relaxed")
            .with_columns(day=pl.from_epoch("shock_ms", time_unit="ms").dt.date())
            .sort(["shock_ms", "asset"]))


def evaluate(rows: pl.DataFrame, days: pl.Series) -> dict:
    n = rows.height
    daily = (pl.DataFrame({"day": days}).join(
        rows.group_by("day").agg((pl.col("r").mean() * RISK).alias("ret")), on="day",
        how="left").fill_null(0.0))["ret"].to_numpy()
    sd = daily.std(ddof=1)
    return {"trades": n, "per_day": n / days.len(),
            "mean_net_bps": float(rows["net"].mean() * 1e4) if n else 0.0,
            "mean_r": float(rows["r"].mean()) if n else 0.0,
            "win": float((rows["net"] > 0).mean()) if n else 0.0,
            "sharpe": float(daily.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0,
            "return": float(daily.sum())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, default=Path("data/parquet/intraday/_bank"))
    parser.add_argument("--out", type=Path, default=Path("research/search_011"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    bank = load(args.bank)
    spans = {name: pl.date_range(pl.lit(lo).str.to_date(), pl.lit(hi).str.to_date(), "1d",
                                 closed="left", eager=True).alias("day")
             for name, (lo, hi) in (("discover", DISCOVER), ("validate", VALIDATE))}
    results = []
    trial = 0
    for level, (fname, fexpr), side in product(LEVELS, FILTERS.items(), SIDES):
        subset = bank.filter(pl.col(f"at_{level:g}") & fexpr)
        for k, d, sl, tp, h in VARIANTS:
            col = column(k, d, sl, tp, h)
            flip = 1 if d == "follow" else -1
            rows = subset.select(
                "day", (pl.col("sign") * flip).alias("side"), pl.col(col).alias("net"),
                (pl.col(col) / (sl * pl.col("atr_pct"))).alias("r"))
            if side != "both":
                rows = rows.filter(pl.col("side") == (1 if side == "long" else -1))
            trial += 1
            disc = rows.filter(pl.col("day").is_in(spans["discover"].implode()))
            results.append({"trial": trial, "level": level, "filter": fname, "side": side,
                            "delay": k, "dir": d, "sl": sl, "tp": tp, "hold": h,
                            **{f"d_{key}": v for key, v in
                               evaluate(disc, spans["discover"]).items()}})
    table = pl.DataFrame(results)
    table.write_parquet(args.out / "discover.parquet")

    eligible = table.filter(pl.col("d_per_day") >= MIN_TRADES_PER_DAY).sort(
        "d_sharpe", descending=True)
    top = eligible.head(TOP)
    checked = []
    for row in top.iter_rows(named=True):
        subset = bank.filter(pl.col(f"at_{row['level']:g}") & FILTERS[row["filter"]])
        col = column(row["delay"], row["dir"], row["sl"], row["tp"], row["hold"])
        flip = 1 if row["dir"] == "follow" else -1
        rows = subset.select("day", (pl.col("sign") * flip).alias("side"),
                             pl.col(col).alias("net"),
                             (pl.col(col) / (row["sl"] * pl.col("atr_pct"))).alias("r"))
        if row["side"] != "both":
            rows = rows.filter(pl.col("side") == (1 if row["side"] == "long" else -1))
        val = rows.filter(pl.col("day").is_in(spans["validate"].implode()))
        checked.append({**row, **{f"v_{key}": v for key, v in
                                  evaluate(val, spans["validate"]).items()}})
    validated = pl.DataFrame(checked)
    validated.write_parquet(args.out / "validate.parquet")

    n = table.height
    summary = {"trials": n, "eligible": eligible.height,
               "discover_sharpe_quantiles": {
                   q: float(table["d_sharpe"].quantile(q)) for q in (0.5, 0.9, 0.99, 1.0)},
               "null_max_sharpe_approx": float(np.sqrt(2 * np.log(n)))}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    pl.Config.set_tbl_cols(20)
    pl.Config.set_tbl_rows(40)
    print(json.dumps(summary, indent=2))
    print(validated.select("level", "filter", "side", "delay", "dir", "sl", "tp", "hold",
                           "d_per_day", "d_mean_net_bps", "d_sharpe",
                           "v_per_day", "v_mean_net_bps", "v_win", "v_sharpe", "v_return"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
