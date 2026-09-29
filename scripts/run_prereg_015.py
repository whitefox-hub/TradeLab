#!/usr/bin/env python
"""PREREG-015: BTC's trend, long or flat, against holding BTC.

    python scripts/run_prereg_015.py fetch      # spot BTCUSDT daily klines, 2017-08 -> 2026-09
    python scripts/run_prereg_015.py search     # discover + validate
    python scripts/run_prereg_015.py holdout    # the committed rules, once
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from itertools import product
from pathlib import Path

import httpx
import numpy as np
import polars as pl

from tradelab.collectors.binance.intraday import fetch_text

ARCHIVE = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d"
DATA = Path("data/parquet/spot_btc_1d.parquet")
OUT = Path("research/prereg_015")
COST = 0.00115
DISCOVER = (date(2022, 1, 1), date(2024, 9, 24))
VALIDATE = (date(2024, 11, 1), date(2026, 9, 24))
HOLDOUT = (date(2018, 4, 1), date(2021, 10, 31))


def fetch() -> int:
    rows = []
    month = date(2017, 8, 1)
    with httpx.Client(timeout=60.0) as client:
        while month <= date(2026, 9, 1):
            text = fetch_text(client, f"{ARCHIVE}/BTCUSDT-1d-{month:%Y-%m}.zip")
            for line in (text or "").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                opened = int(cells[0])
                opened = opened // 1000 if opened > 10**14 else opened   # µs from 2025
                rows.append((datetime.fromtimestamp(opened / 1000, UTC).date(),
                             float(cells[4])))
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    frame = pl.DataFrame(rows, schema={"day": pl.Date, "close": pl.Float64}, orient="row")
    frame = frame.unique("day").sort("day")
    DATA.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(DATA)
    print(f"{frame.height} days, {frame['day'].min()} -> {frame['day'].max()}")
    return 0


def rules() -> list[dict]:
    out = []
    specs = ([("sma", n, 0) for n in (50, 100, 150, 200)]
             + [("cross", f, s) for f, s in ((20, 50), (50, 100), (50, 200), (100, 200))]
             + [("momentum", n, 0) for n in (30, 60, 90, 180)])
    for (kind, a, b), check in product(specs, ("daily", "weekly")):
        out.append({"kind": kind, "a": a, "b": b, "check": check})
    return out


def signal(close: np.ndarray, rule: dict) -> np.ndarray:
    """1 long, 0 flat, NaN before warm-up; known at each close."""
    s = pl.Series(close)
    if rule["kind"] == "sma":
        out = (close > s.rolling_mean(rule["a"]).to_numpy()).astype(float)
        out[: rule["a"] - 1] = np.nan
    elif rule["kind"] == "cross":
        fast = s.rolling_mean(rule["a"]).to_numpy()
        slow = s.rolling_mean(rule["b"]).to_numpy()
        out = (fast > slow).astype(float)
        out[: rule["b"] - 1] = np.nan
    else:
        n = rule["a"]
        out = np.full(close.size, np.nan)
        out[n:] = (close[n:] > close[:-n]).astype(float)
    return out


def run(frame: pl.DataFrame, rule: dict | None, lo: date, hi: date) -> dict:
    """The account from $1 on lo's close through hi's close; rule None holds BTC."""
    days = frame["day"].to_list()
    close = frame["close"].to_numpy()
    i0, i1 = days.index(lo), days.index(hi)
    sig = signal(close, rule) if rule else np.ones(close.size)
    weekly = rule is not None and rule["check"] == "weekly"
    assert np.isfinite(sig[i0]), "warm-up not complete at the span's start"
    value, held, switches, invested = 1.0, 0.0, 0, 0
    curve = [1.0]
    for i in range(i0, i1):
        want = held if (weekly and days[i].weekday() != 0 and i > i0) else sig[i]
        if want != held:
            value *= 1 - COST
            switches += 1
            held = want
        value *= 1 + held * (close[i + 1] / close[i] - 1)
        invested += int(held > 0)
        curve.append(value)
    c = np.array(curve)
    r = np.diff(c) / c[:-1]
    return {"final": float(value), "max_drawdown": float((c / np.maximum.accumulate(c) - 1).min()),
            "switches": switches, "days_in": invested, "days": i1 - i0,
            "sharpe": float(r.mean() / r.std(ddof=1) * np.sqrt(365)) if r.std() > 0 else 0.0}


def search() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pl.read_parquet(DATA)
    hold = {name: run(frame, None, *span) for name, span in
            (("discover", DISCOVER), ("validate", VALIDATE))}
    table = []
    for i, rule in enumerate(rules(), 1):
        d, v = run(frame, rule, *DISCOVER), run(frame, rule, *VALIDATE)
        table.append({"trial": i, **rule,
                      "d_final": d["final"], "d_excess": d["final"] - hold["discover"]["final"],
                      "d_dd": d["max_drawdown"],
                      "v_final": v["final"], "v_excess": v["final"] - hold["validate"]["final"],
                      "v_dd": v["max_drawdown"]})
    frame_out = pl.DataFrame(table).sort("d_excess", descending=True)
    frame_out.write_parquet(OUT / "search.parquet")
    chosen = frame_out.filter((pl.col("d_excess") > 0) & (pl.col("v_excess") > 0)).head(3)
    summary = {"trials": len(table), "hold": hold,
               "beat_discover": int((frame_out["d_excess"] > 0).sum()),
               "beat_both": int(((frame_out["d_excess"] > 0) & (frame_out["v_excess"] > 0)).sum()),
               "holdout": chosen.select("kind", "a", "b", "check").to_dicts()}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pl.Config.set_tbl_width_chars(200)
    pl.Config.set_tbl_rows(30)
    pl.Config.set_float_precision(3)
    print(json.dumps({k: v for k, v in summary.items() if k != "holdout"}, indent=1))
    print(frame_out.drop("trial"))
    print("to the holdout:", summary["holdout"])
    return 0


def holdout() -> int:
    chosen = json.loads((OUT / "summary.json").read_text())["holdout"]
    if not chosen:
        raise SystemExit("no rule beat holding in both spans; the holdout stays sealed")
    frame = pl.read_parquet(DATA)
    hold = run(frame, None, *HOLDOUT)
    report = {"hold": hold, "rules": [{"rule": r, **run(frame, r, *HOLDOUT)} for r in chosen]}
    for r in report["rules"]:
        r["beats_hold"] = r["final"] > hold["final"]
    (OUT / "holdout.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("fetch", "search", "holdout"))
    stage = parser.parse_args().stage
    return {"fetch": fetch, "search": search, "holdout": holdout}[stage]()


if __name__ == "__main__":
    raise SystemExit(main())
