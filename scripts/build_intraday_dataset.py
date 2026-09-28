#!/usr/bin/env python
"""Build the intraday data set PREREG-008 registered. Reads no return.

    python scripts/build_intraday_dataset.py            # all three phases, resumable

1. Daily quote volume, survivorship-neutral universe (FINDING-011), cut at delisting.
2. Monthly PIT universe: top 100 by the previous 30 days' volume.
3. Per member month plus the prior month (warm-up): 5m klines, premium-index klines,
   metrics; one parquet per asset/kind/month.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl

from tradelab.collectors.binance.intraday import (
    cut,
    daily_quote_volume,
    month_of_klines,
    month_of_metrics,
    monthly_universe,
)

START = datetime(2024, 9, 25, tzinfo=UTC)
END = datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC)
FIRST_MONTH = date(2024, 11, 1)     # the first with 30 ranked days inside the window
KINDS = ("klines", "premiumIndexKlines", "metrics")


def months(first: date, last: date) -> list[date]:
    out, month = [], first
    while month <= last:
        out.append(month)
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return out


def previous(month: date) -> date:
    return date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)


def set_window(start: date | None, end: date | None) -> None:
    """The window, and the first universe month: the first with 30 ranked days in it."""
    global START, END, FIRST_MONTH
    if start:
        START = datetime(start.year, start.month, start.day, tzinfo=UTC)
        ranked = start + timedelta(days=30)
        FIRST_MONTH = (ranked if ranked.day == 1 else
                       date(ranked.year + ranked.month // 12, ranked.month % 12 + 1, 1))
    if end:
        END = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, default=Path("data/parquet/binance_pit"))
    parser.add_argument("--out", type=Path, default=Path("data/parquet/intraday"))
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="window start, UTC (default: the 2024-09 window)")
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    set_window(args.start, args.end)
    args.out.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=60.0)

    contracts = pl.read_parquet(args.panel / "_universe.parquet")
    traded = {p.stem for p in args.panel.glob("*.parquet") if not p.stem.startswith("_")}
    contracts = contracts.filter(pl.col("asset").is_in(sorted(traded)))
    delisted = dict(zip(contracts["asset"], contracts["delisted_at"], strict=True))

    # -- 1. daily volume ----------------------------------------------------
    volume_path = args.out / "_daily_volume.parquet"
    if volume_path.exists():
        volumes = pl.read_parquet(volume_path)
    else:
        frames = []
        for index, row in enumerate(contracts.iter_rows(named=True), start=1):
            frame = daily_quote_volume(client, row["symbol"], START, END, row["delisted_at"])
            frames.append(frame.with_columns(pl.lit(row["asset"]).alias("asset")))
            if index % 100 == 0:
                print(f"  volume {index}/{contracts.height}", flush=True)
        volumes = pl.concat(frames).select("asset", "day", "quote_volume")
        volumes.write_parquet(volume_path)
    print(f"1. daily volume: {volumes['asset'].n_unique()} assets, {volumes.height:,} days")

    # -- 2. the universe ----------------------------------------------------
    member_months = months(FIRST_MONTH, date(END.year, END.month, 1))
    universe = monthly_universe(volumes, member_months)
    universe.write_parquet(args.out / "_universe_monthly.parquet")
    print(f"2. universe: {len(member_months)} months, "
          f"{universe['asset'].n_unique()} distinct assets")

    # -- 3. download --------------------------------------------------------
    wanted = sorted({(a, m) for a, month in zip(universe["asset"], universe["month"],
                                                strict=True)
                     for m in (previous(month), month)})
    tasks = [(kind, asset, month) for asset, month in wanted for kind in KINDS
             if not (args.out / kind / asset / f"{month:%Y-%m}.parquet").exists()]
    print(f"3. download: {len(wanted)} asset-months, {len(tasks)} files still to fetch",
          flush=True)

    def fetch(kind: str, asset: str, month: date) -> tuple[str, int]:
        symbol, until = f"{asset}USDT", END.date()
        frame = (month_of_metrics(client, symbol, month, until) if kind == "metrics"
                 else month_of_klines(client, kind, symbol, month, until))
        frame = cut(frame, START, END, delisted.get(asset))
        target = args.out / kind / asset / f"{month:%Y-%m}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written even when empty (an answer, not a gap); via rename so resume never
        # mistakes a half-written file for a finished one.
        partial = target.with_suffix(".partial")
        frame.write_parquet(partial)
        partial.replace(target)
        return kind, frame.height

    done = rows = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, *task) for task in tasks]
        for future in as_completed(futures):
            _, height = future.result()
            done += 1
            rows += height
            if done % 250 == 0 or done == len(tasks):
                print(f"  {done}/{len(tasks)} files, {rows:,} rows", flush=True)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
