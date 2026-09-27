#!/usr/bin/env python
"""Queue calibration for PREREG-008: does the bar model's fill survive the trades?

The bar model fills a resting limit when a later 5-minute bar trades through
it by one tick. Price-time priority settles the queue question for such a
fill: a trade printed a tick beyond the limit means the whole level at the
limit was consumed first, our order included. What the bar cannot see is
*when*. A through-print before the order could have been resting is not a fill,
and neither is one after the order's thirty minutes.

So, for a stratified sample of 2,000 bar-model fills, this reads the day's
`aggTrades` and asks whether the first trade a tick or more through the limit
came after the shock bar closed plus the placement latency, and before the
order expired. The share that does is the haircut `run_prereg_008.py` applies
to every bar-model fill, as a random thinning.

The REST endpoint serves only the last two days of aggregate trades (-4166,
measured), so the daily archive is read. Each file is fetched once, processed
in memory and discarded.

    python scripts/calibrate_flash_strike_fills.py
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import polars as pl

from tradelab.collectors.binance.intraday import fetch_text

ARCHIVE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
SAMPLE = 2_000
LATENCY_MS = 1_000                  # placement after the shock bar's close
ORDER_LIFE_MS = 30 * 60_000
SEED = 20260927


def trades_on(client: httpx.Client, symbol: str, day: date) -> np.ndarray | None:
    """(time ms, price) for one day, or None if the archive has no such file."""
    text = fetch_text(client, f"{ARCHIVE}/{symbol}/{symbol}-aggTrades-{day:%Y-%m-%d}.zip")
    if text is None:
        return None
    rows = [(int(r[5]), float(r[1])) for r in csv.reader(io.StringIO(text))
            if r and r[0].isdigit()]
    return np.array(rows, dtype=np.float64) if rows else np.empty((0, 2))


def tick_of(prices: np.ndarray) -> float:
    distinct = np.unique(prices)
    gaps = np.diff(distinct)
    gaps = gaps[gaps > 0]
    return float(gaps.min()) if gaps.size else float("nan")


def judge(trades: np.ndarray, placed: int, expires: int, limit: float, side: int,
          bar_fill_close: int) -> dict:
    window = trades[(trades[:, 0] >= placed) & (trades[:, 0] <= expires)]
    if window.size == 0:
        return {"confirmed": False, "delay_ms": None, "same_bar": False}
    tick = tick_of(trades[:, 1])
    through = (window[:, 1] <= limit - tick) if side > 0 else (window[:, 1] >= limit + tick)
    if not through.any():
        return {"confirmed": False, "delay_ms": None, "same_bar": False}
    first = window[np.argmax(through), 0]
    return {"confirmed": True, "delay_ms": float(first - placed),
            "same_bar": bool(bar_fill_close - 300_000 <= first < bar_fill_close)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet/intraday"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    events = pl.read_parquet(args.root / "_candidates.parquet").filter(
        pl.col("kind") == "event")
    universe = pl.read_parquet(args.root / "_universe_monthly.parquet").with_columns(
        pl.col("month").dt.strftime("%Y-%m"),
        ((pl.col("rank") - 1) // 34).alias("tercile"))
    events = events.join(universe.select("asset", "month", "tercile"),
                         on=["asset", "month"], how="left")
    rng = np.random.default_rng(SEED)
    parts = []
    for tercile, group in events.group_by("tercile", maintain_order=True):
        take = min(group.height, SAMPLE // 3 + (1 if tercile == (0,) else 0))
        parts.append(group[rng.choice(group.height, size=take, replace=False).tolist()])
    sample = pl.concat(parts)
    print(f"sample: {sample.height} bar-model fills, by tercile "
          f"{sample.group_by('tercile').agg(pl.len()).sort('tercile').to_dicts()}")

    def day_of(ms: int) -> date:
        return datetime.fromtimestamp(ms / 1000, UTC).date()

    jobs: dict[tuple[str, date], list[dict]] = {}
    for row in sample.iter_rows(named=True):
        placed = row["shock_ms"] + LATENCY_MS
        expires = row["shock_ms"] + ORDER_LIFE_MS
        for day in {day_of(placed), day_of(expires)}:
            jobs.setdefault((row["asset"], day), [])
        jobs[(row["asset"], day_of(placed))].append(row)

    client = httpx.Client(timeout=120.0)

    def run(key: tuple[str, date], rows: list[dict]) -> list[dict]:
        asset, day = key
        symbol = f"{asset}USDT"
        trades = trades_on(client, symbol, day)
        if trades is None:
            return [{"asset": asset, "tercile": r["tercile"], "available": False}
                    for r in rows]
        out = []
        for r in rows:
            expires = r["shock_ms"] + ORDER_LIFE_MS
            both = trades
            if day_of(expires) != day:
                nxt = trades_on(client, symbol, day + timedelta(days=1))
                if nxt is not None:
                    both = np.vstack([trades, nxt])
            verdict = judge(both, r["shock_ms"] + LATENCY_MS, expires, r["entry"],
                            r["side"], r["fill_ms"])
            out.append({"asset": asset, "tercile": r["tercile"], "available": True, **verdict})
        return out

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, k, v) for k, v in jobs.items() if v]
        for i, future in enumerate(as_completed(futures), start=1):
            results += future.result()
            if i % 100 == 0:
                print(f"  {i}/{len(futures)} asset-days", flush=True)

    frame = pl.DataFrame(results)
    usable = frame.filter(pl.col("available"))
    confirmed = float(usable["confirmed"].mean())
    by_tercile = (usable.group_by("tercile").agg(pl.col("confirmed").mean().alias("share"),
                                                 pl.len().alias("n")).sort("tercile"))
    report = {
        "confirmed_share": confirmed,
        "sampled": frame.height,
        "available": usable.height,
        "latency_ms": LATENCY_MS,
        "by_tercile": by_tercile.to_dicts(),
        "same_bar_share": float(usable.filter(pl.col("confirmed"))["same_bar"].mean()),
        "median_delay_s": float(np.median([d for d in usable["delay_ms"].to_list()
                                           if d is not None]) / 1000),
        "built": date.today().isoformat(),
    }
    (args.root / "_queue_calibration.json").write_text(json.dumps(report, indent=2,
                                                                   default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
