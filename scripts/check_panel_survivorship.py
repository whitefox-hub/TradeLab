"""Compare the survivorship-neutral Binance panel with the five-venue one. §4.4, RESULT-007.

Reports agreement on shared asset-days (fewer old settlements = rate-limited truncation),
cross-section removed per day, and delisted names' exit behaviour. No signal, no trial.

    python scripts/check_panel_survivorship.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl


def load(root: Path, venue_filter: bool) -> pl.DataFrame:
    frames = [pl.read_parquet(f) for f in sorted(root.glob("*.parquet"))
              if not f.name.startswith("_")]
    rows = pl.concat(frames, how="diagonal_relaxed")
    if venue_filter:
        rows = rows.filter(pl.col("venue") == "binance")
    return rows.drop_nulls("close").select("asset", "day", "close", "rate", "n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path, default=Path("data/parquet/funding_multivenue"))
    parser.add_argument("--new", type=Path, default=Path("data/parquet/binance_pit"))
    args = parser.parse_args()

    old, new = load(args.old, True), load(args.new, False)
    universe = pl.read_parquet(args.new / "_universe.parquet")
    last_common = min(old["day"].max(), new["day"].max())

    # -- 1. agreement ---------------------------------------------------------
    both = old.join(new, on=["asset", "day"], suffix="_new").filter(
        pl.col("day") < last_common)      # the old panel's last day was partial
    close_off = both.filter((pl.col("close") - pl.col("close_new")).abs()
                            > 1e-9 * pl.col("close_new").abs())
    fewer = both.filter(pl.col("n") < pl.col("n_new"))
    rate_off = both.filter((pl.col("rate") - pl.col("rate_new")).abs() > 1e-12)
    print("1. agreement, where both panels hold an asset-day")
    print(f"   asset-days compared     : {both.height:,} "
          f"({both['asset'].n_unique()} assets, last partial day excluded)")
    print(f"   closes that differ      : {close_off.height}")
    print(f"   funding that differs    : {rate_off.height}")
    print(f"   old has fewer settlements: {fewer.height} asset-days, "
          f"{fewer['asset'].n_unique()} assets")
    if fewer.height:
        worst = (fewer.group_by("asset").agg(pl.len().alias("days"))
                 .sort("days", descending=True).head(5))
        print(f"   most affected           : {worst.to_dicts()}")
    missing = (new.join(old, on=["asset", "day"], how="anti")
               .filter(pl.col("asset").is_in(old["asset"].unique().implode())))
    print(f"   days the old panel lacks for its own assets: {missing.height}")

    # -- 2. composition ---------------------------------------------------------
    per_day = (new.group_by("day").agg(pl.len().alias("new"))
               .join(old.group_by("day").agg(pl.len().alias("old")), on="day", how="left")
               .sort("day"))
    print("\n2. composition, assets with a price on the day")
    for row in per_day.filter(pl.col("day").dt.day() == 1)[::3].iter_rows(named=True):
        print(f"   {row['day']}  new {row['new']:4d}  old {row['old'] or 0:4d}  "
              f"removed {(row['new'] - (row['old'] or 0)) / row['new']:.0%}")
    traded = new["asset"].n_unique()
    dead = universe.filter(pl.col("status") != "TRADING")
    dead_in = new.filter(pl.col("asset").is_in(dead["asset"].implode()))
    print(f"   assets that traded in the window: {traded} new, "
          f"{old['asset'].n_unique()} old")
    print(f"   of which no longer trading      : {dead_in['asset'].n_unique()}")

    # -- 3. the dead ------------------------------------------------------------
    exits = []
    for asset, rows in dead_in.sort("day").group_by("asset"):
        closes = rows["close"].to_numpy()
        if closes.size < 31:
            continue
        exits.append({
            "asset": asset[0],
            "last_day": rows["day"][-1],
            "last_day_return": closes[-1] / closes[-2] - 1,
            "final_30d_return": closes[-1] / closes[-31] - 1,
            "final_7d_funding": float(rows["rate"].fill_null(0).to_numpy()[-7:].sum()),
        })
    frame = pl.DataFrame(exits)
    ended = frame.filter(pl.col("last_day") < last_common)
    print(f"\n3. the dead: {ended.height} delisted inside the window with 30+ days")
    for name, column in (("last day", "last_day_return"),
                         ("final 30 days", "final_30d_return"),
                         ("final 7d funding", "final_7d_funding")):
        values = ended[column].to_numpy()
        print(f"   {name:17s} median {np.median(values):+8.2%}   "
              f"p5 {np.percentile(values, 5):+8.2%}   p95 {np.percentile(values, 95):+9.2%}")
    up = ended.filter(pl.col("last_day_return") > 0.5)
    print(f"   last day above +50%: {up.height} "
          f"{sorted(up['asset'].to_list())}")
    print(f"   final 30 days below -50%: "
          f"{ended.filter(pl.col('final_30d_return') < -0.5).height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
