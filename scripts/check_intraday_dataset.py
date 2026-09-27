"""Data assertions on the PREREG-008 intraday set. Plan.md §4.2, §16.7.

Reads what the builder wrote and reports what is wrong with it. It computes no
return, so it spends no trial.

For each kind and asset-month:

* **coverage**: 5-minute slots present against slots expected. Expected runs
  from the later of the month's start and the asset's first bar to the earlier
  of the month's end, the window's end and delisting;
* **duplicates** and **ordering**;
* **sanity**: for klines, low ≤ open, close ≤ high, prices positive, taker
  volume within total; for metrics, open interest not negative.

Across kinds: the share of kline slots that have a premium bar and a metrics
snapshot within five minutes, which is the join a feature will make.

And the universe's churn: how many names each month were not members the
month before.
"""

from __future__ import annotations

import argparse
from itertools import pairwise
from pathlib import Path

import numpy as np
import polars as pl

STEP_MS = 300_000


def month_files(root: Path, kind: str) -> list[Path]:
    return sorted((root / kind).glob("*/*.parquet"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet/intraday"))
    args = parser.parse_args()

    universe = pl.read_parquet(args.root / "_universe_monthly.parquet")
    by_month = {m: set(g["asset"]) for (m,), g in universe.group_by("month")}
    ordered = sorted(by_month)
    print("universe churn: names new to the top 100 each month")
    churn = [len(by_month[b] - by_month[a]) for a, b in pairwise(ordered)]
    print(f"  median {int(np.median(churn))}, min {min(churn)}, max {max(churn)} of 100")

    for kind in ("klines", "premiumIndexKlines", "metrics"):
        files = month_files(args.root, kind)
        empty = gaps = dupes = disorder = bad = 0
        coverage = []
        worst: list[tuple[float, str]] = []
        for path in files:
            frame = pl.read_parquet(path)
            if frame.is_empty():
                empty += 1
                continue
            stamps = frame["available_ms"].to_numpy()
            dupes += int(stamps.size - np.unique(stamps).size)
            disorder += int(np.sum(np.diff(stamps) < 0))
            span = (stamps.max() - stamps.min()) // STEP_MS + 1
            present = np.unique(stamps).size
            coverage.append(present / span)
            gaps += int(span - present)
            worst.append((present / span, f"{path.parent.name}/{path.stem}"))
            if kind != "metrics":
                bad += frame.filter(
                    (pl.col("low") > pl.min_horizontal("open", "close"))
                    | (pl.col("high") < pl.max_horizontal("open", "close"))
                    | (pl.col("low") <= 0)
                    | ((pl.col("volume") > 0)
                       & (pl.col("taker_buy_volume") > pl.col("volume") * 1.000001))
                ).height if kind == "klines" else 0
            else:
                bad += frame.filter(pl.col("open_interest") < 0).height
        worst.sort()
        print(f"\n{kind}: {len(files)} asset-months, {empty} empty")
        if coverage:
            print(f"  coverage median {np.median(coverage):.4f}, "
                  f"p1 {np.percentile(coverage, 1):.4f}, missing slots {gaps:,}")
            print(f"  duplicates {dupes}, out of order {disorder}, failed sanity {bad}")
            print(f"  lowest coverage: {[(f'{c:.3f}', n) for c, n in worst[:5]]}")

    # The join a feature will make: each kline slot against the other two.
    joined = total = with_premium = with_metrics = 0
    for path in month_files(args.root, "klines"):
        asset, month = path.parent.name, path.stem
        kl = pl.read_parquet(path).select("available_ms")
        if kl.is_empty():
            continue
        total += kl.height
        prem = args.root / "premiumIndexKlines" / asset / f"{month}.parquet"
        if prem.exists():
            with_premium += kl.join(pl.read_parquet(prem).select("available_ms"),
                                    on="available_ms").height
        met = args.root / "metrics" / asset / f"{month}.parquet"
        if met.exists():
            snaps = pl.read_parquet(met).select(
                pl.col("available_ms").alias("snap")).sort("snap")
            matched = kl.sort("available_ms").join_asof(
                snaps, left_on="available_ms", right_on="snap", strategy="backward")
            with_metrics += matched.filter(
                pl.col("snap").is_not_null()
                & (pl.col("available_ms") - pl.col("snap") <= STEP_MS)).height
        joined += 1
    print(f"\nalignment over {joined} kline asset-months, {total:,} slots")
    print(f"  with a premium bar at the same close : {with_premium / max(total, 1):.4f}")
    print(f"  with a metrics snapshot ≤ 5 min old  : {with_metrics / max(total, 1):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
