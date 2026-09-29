#!/usr/bin/env python
"""PREREG-014: never sell at a loss. BTC and ETH, targets 0.5%, 1%, 2% above the costs.

    python scripts/run_prereg_014.py --era unseen      # the registered test
    python scripts/run_prereg_014.py --era seen        # mechanics check on the seen era

The account is marked every 00:00 UTC close: open positions at that price, net of the
cost to exit and the funding paid.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl
from build_flash_strike_candidates import funding_per_bar, load

from tradelab.strategy import flash_data
from tradelab.strategy import flash_strike as fs

ASSETS = ("BTC", "ETH")
TARGETS = (0.005, 0.01, 0.02)
STAKE = 500.0
CAPITAL = 10_000.0
WINDOW_DAYS = 10
DAY_MS = 86_400_000
ERAS = {
    "unseen": (Path("data/parquet/intraday_2022"), Path("data/parquet/binance_pit_2022"),
               date(2022, 1, 1), date(2024, 9, 24)),
    "seen": (Path("data/parquet/intraday"), Path("data/parquet/binance_pit"),
             date(2024, 11, 1), date(2026, 9, 24)),
}
OUT = Path("research/prereg_014")


def asset_path(root: Path, panel: Path, asset: str, x: float, lo: date,
               hi: date) -> tuple[np.ndarray, list[dict]]:
    """The asset's contribution to the account at each day's close, and its trades."""
    bars = load(root, asset)
    assert bars is not None, f"{asset} missing from {root}"
    s = flash_data.series(bars)
    month = bars["month"].to_list()
    universe = pl.read_parquet(root / "_universe_monthly.parquet").filter(
        pl.col("asset") == asset)
    members = {f"{m:%Y-%m}" for m in universe["month"]}
    funding, _ = funding_per_bar(panel, asset, s.close_ms)
    cum = np.concatenate([[0.0], np.cumsum(funding)])
    lo_ms = int(datetime(lo.year, lo.month, lo.day, tzinfo=UTC).timestamp() * 1000)
    hi_ms = int(datetime(hi.year, hi.month, hi.day, tzinfo=UTC).timestamp() * 1000) + DAY_MS

    events, sides, atrs = fs.shocks(s)
    trades: list[dict] = []
    busy_until = -1
    for t, side, atr in zip(events, sides, atrs, strict=True):
        t, side = int(t), int(side)
        if s.close_ms[t] < lo_ms or s.close_ms[t] >= hi_ms or t <= busy_until:
            continue
        if month[t] not in members or fs.fill_at_shock(s, t, side, float(atr)) is None:
            continue
        price = s.close[t]
        target = price * (1 + side * (x + fs.TAKER + fs.MAKER))
        best = s.high[t + 1:] if side > 0 else s.low[t + 1:]
        hit = best >= target if side > 0 else best <= target
        k = t + 1 + int(np.argmax(hit)) if hit.any() else None
        trades.append({"entry_bar": t, "exit_bar": k, "side": side, "entry": float(price),
                       "entry_ms": int(s.close_ms[t]),
                       "exit_ms": int(s.close_ms[k]) if k is not None else None})
        busy_until = k if k is not None else s.close.size

    day_ms = np.arange(lo_ms + DAY_MS, hi_ms + 1, DAY_MS)       # each day's closing stamp
    index = np.searchsorted(s.close_ms, day_ms, side="right") - 1
    value = np.zeros(day_ms.size)
    for tr in trades:
        t, k, side, p = tr["entry_bar"], tr["exit_bar"], tr["side"], tr["entry"]
        if k is not None:
            tr["net"] = x - side * (cum[k + 1] - cum[t + 1])
            tr["days"] = (s.close_ms[k] - s.close_ms[t]) / DAY_MS
            value[index >= k] += STAKE * tr["net"]
        open_days = (index > t) & ((index < k) if k is not None else True)
        for d in np.flatnonzero(open_days):
            b = index[d]
            value[d] += STAKE * (side * (s.close[b] - p) / p - 2 * fs.TAKER
                                - side * (cum[b + 1] - cum[t + 1]))
        tr["worst_mark"] = float(min((value_at(s, b, side, p) for b in index[open_days]),
                                     default=0.0))
    return value, trades


def value_at(s: fs.Series, b: int, side: int, p: float) -> float:
    return float(side * (s.close[b] - p) / p - 2 * fs.TAKER)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--era", choices=tuple(ERAS), default="unseen")
    args = parser.parse_args()
    root, panel, lo, hi = ERAS[args.era]
    OUT.mkdir(parents=True, exist_ok=True)
    days = pl.date_range(lo, hi, "1d", eager=True).to_list()
    report = []
    for x in TARGETS:
        account = np.full(len(days), CAPITAL)
        trades = []
        for asset in ASSETS:
            value, made = asset_path(root, panel, asset, x, lo, hi)
            account += value
            trades += [{"asset": asset, **t} for t in made]
        returns = np.diff(np.concatenate([[CAPITAL], account])) / \
            np.concatenate([[CAPITAL], account[:-1]])
        windows = []
        for w0 in range(0, len(days), WINDOW_DAYS):
            end = min(w0 + WINDOW_DAYS, len(days)) - 1
            windows.append({"start": str(days[w0]), "end": str(days[end]),
                            "account": float(account[end])})
        reached = end + 1
        r = returns[:reached]
        sd = r.std(ddof=1)
        end_ms = int(datetime(days[end].year, days[end].month, days[end].day,
                              tzinfo=UTC).timestamp() * 1000) + DAY_MS
        opened = [t for t in trades if t["entry_ms"] < end_ms]
        closed = [t for t in opened if t["exit_ms"] is not None and t["exit_ms"] <= end_ms]
        peak = np.maximum.accumulate(account[:reached])
        report.append({
            "target": x, "reached_days": reached, "stopped": reached < len(days),
            "account_end": float(account[end]),
            "sharpe": float(r.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0,
            "threshold": float(1.65 / np.sqrt(reached / 365)),
            "max_drawdown": float((account[:reached] / peak - 1).min()),
            "trades": len(opened),
            "closed": len(closed),
            "longest_wait_days": float(max((t["days"] for t in closed), default=0.0)),
            "worst_carried": float(min((t["worst_mark"] for t in opened), default=0.0)),
            "windows": windows})
        print(f"target +{x:.1%}: account {account[end]:,.0f} after {reached} days "
              f"({'stopped' if reached < len(days) else 'full span'}), "
              f"Sharpe {report[-1]['sharpe']:.2f} vs {report[-1]['threshold']:.2f}, "
              f"drawdown {report[-1]['max_drawdown']:.1%}, closed {len(closed)}, "
              f"longest wait {report[-1]['longest_wait_days']:.0f} d, "
              f"worst carried {report[-1]['worst_carried']:.1%}", flush=True)
    (OUT / f"{args.era}.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
