#!/usr/bin/env python
"""PREREG-017: patterns in BTC's daily moves: previous day, weekday, hour block.

    python scripts/run_prereg_017.py fetch      # spot BTCUSDT 1h, 2017-08 -> 2026-09
    python scripts/run_prereg_017.py check      # no lookahead; daily closes == the 1d file
    python scripts/run_prereg_017.py search     # discover + validate
    python scripts/run_prereg_017.py holdout    # the committed rules, once
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from itertools import product
from pathlib import Path

import httpx
import numpy as np
import polars as pl

from tradelab.collectors.binance.intraday import fetch_text

MONTHLY = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h"
DAILY = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1h"
DATA = Path("data/parquet/spot_btc_1h.parquet")
OUT = Path("research/prereg_017")
COST = 0.00115
SMA_DAYS = 150
DISCOVER = (date(2022, 1, 1), date(2024, 9, 24))
VALIDATE = (date(2024, 11, 1), date(2026, 9, 24))
HOLDOUT = (date(2018, 6, 1), date(2021, 12, 31))
BUCKETS = {"lt-3": (-np.inf, -0.03), "-3to-1": (-0.03, -0.01), "gt1": (0.01, np.inf),
           "gt3": (0.03, np.inf)}
TOP = 10


def fetch() -> int:
    rows = []
    month = date(2017, 8, 1)
    with httpx.Client(timeout=60.0) as client:
        while month <= date(2026, 9, 1):
            text = fetch_text(client, f"{MONTHLY}/BTCUSDT-1h-{month:%Y-%m}.zip")
            if text is None:
                text = "\n".join(filter(None, (
                    fetch_text(client, f"{DAILY}/BTCUSDT-1h-{month:%Y-%m}-{d:02d}.zip")
                    for d in range(1, 32))))
            for line in text.splitlines():
                cells = line.split(",")
                if cells[0].isdigit():
                    opened = int(cells[0])
                    opened = opened // 1000 if opened > 10**14 else opened   # µs from 2025
                    rows.append((opened + 3_600_000, float(cells[4])))      # the close's stamp
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    frame = (pl.DataFrame(rows, schema={"close_ms": pl.Int64, "close": pl.Float64}, orient="row")
             .unique("close_ms").sort("close_ms"))
    frame.write_parquet(DATA)
    print(f"{frame.height} hours, {frame['close_ms'].min()} -> {frame['close_ms'].max()}")
    return 0


def rules() -> list[dict]:
    out = [{"family": "prev", "bucket": b, "regime": g, "side": s}
           for b, g, s in product(BUCKETS, ("any", "up", "down"), (1, -1))]
    out += [{"family": "weekday", "day": d, "regime": g, "side": 1}
            for d, g in product(range(7), ("any", "up"))]
    out += [{"family": "block", "start": h, "regime": g, "side": s}
            for h, g, s in product(range(0, 24, 4), ("any", "up"), (1, -1))]
    return out


def hourly() -> tuple[np.ndarray, np.ndarray]:
    """A complete hourly grid; a missing hour (an exchange outage) is NaN, and no trade may
    enter or exit on it."""
    frame = pl.read_parquet(DATA)
    grid = pl.DataFrame({"close_ms": np.arange(frame["close_ms"].min(),
                                               frame["close_ms"].max() + 1, 3_600_000)})
    full = grid.join(frame, on="close_ms", how="left")
    return full["close_ms"].to_numpy(), full["close"].to_numpy()


def trades(ms: np.ndarray, close: np.ndarray, rule: dict, lo: date, hi: date) -> pl.DataFrame:
    """Every trade of the rule entered on a day in [lo, hi]: entry day, net return."""
    midnight = np.flatnonzero(ms % 86_400_000 == 0)            # index of each 00:00 close
    daily = close[midnight]
    days = [datetime.fromtimestamp(ms[i] / 1000, UTC).date() for i in midnight]
    avg = pl.Series(daily).fill_nan(None).forward_fill().rolling_mean(SMA_DAYS).to_numpy()
    out = []
    for k in range(1, midnight.size - 1):
        day = days[k]                    # the 00:00 close that opens this trading day
        if not lo <= day <= hi or not np.isfinite(avg[k]) or not np.isfinite(daily[k]):
            continue
        regime = "up" if daily[k] > avg[k] else "down"
        if rule["regime"] != "any" and rule["regime"] != regime:
            continue
        i = midnight[k]
        if rule["family"] == "prev":
            lo_b, hi_b = BUCKETS[rule["bucket"]]
            prev = daily[k] / daily[k - 1] - 1
            if not lo_b <= prev < hi_b:
                continue
            entry, exit_ = i, midnight[k + 1]
        elif rule["family"] == "weekday":
            if day.weekday() != rule["day"]:
                continue
            entry, exit_ = i, midnight[k + 1]
        else:
            entry, exit_ = i + rule["start"], i + rule["start"] + 4
        if not (np.isfinite(close[entry]) and np.isfinite(close[exit_])):
            continue                     # an outage: no price to trade at
        if rule["family"] == "prev" and not np.isfinite(daily[k - 1]):
            continue
        gross = rule["side"] * (close[exit_] / close[entry] - 1)
        out.append((day, gross, (1 - COST) ** 2 * (1 + gross) - 1))
    return pl.DataFrame(out, schema={"day": pl.Date, "gross": pl.Float64, "net": pl.Float64},
                        orient="row")


def score(t: pl.DataFrame, lo: date, hi: date) -> dict:
    days = pl.DataFrame({"day": pl.date_range(lo, hi, "1d", eager=True)})
    daily = days.join(t.group_by("day").agg(((1 + pl.col("net")).product() - 1).alias("r")),
                      on="day", how="left").fill_null(0.0)["r"].to_numpy()
    sd = daily.std(ddof=1)
    return {"trades": t.height, "mean_net_bps": float(t["net"].mean() * 1e4) if t.height else 0.0,
            "mean_gross_bps": float(t["gross"].mean() * 1e4) if t.height else 0.0,
            "win": float((t["net"] > 0).mean()) if t.height else 0.0,
            "sharpe": float(daily.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0,
            "final": float(np.prod(1 + daily))}


def check() -> int:
    ms, close = hourly()
    one_day = pl.read_parquet("data/parquet/spot_btc_1d.parquet")
    midnight = ms % 86_400_000 == 0
    hourly_daily = pl.DataFrame({
        "day": [(datetime.fromtimestamp(m / 1000, UTC) - timedelta(days=1)).date()
                for m in ms[midnight]], "h": close[midnight]})
    joined = one_day.join(hourly_daily.drop_nans("h"), on="day")
    worst = float((joined["close"] - joined["h"]).abs().max())
    print(f"1d closes against 00:00 hourly closes: {joined.height} days, max diff {worst}")
    assert worst < 1e-6
    rng = np.random.default_rng(17)
    for rule in rules():
        full = trades(ms, close, rule, *DISCOVER)
        cut = int(rng.integers(len(ms) // 2, len(ms) - 48))
        bent = close.copy()
        bent[cut + 1:] = bent[cut + 1:] * rng.uniform(0.7, 1.3, close.size - cut - 1)
        moved = trades(ms, bent, rule, *DISCOVER)
        cut_day = datetime.fromtimestamp(ms[cut] / 1000, UTC).date() - timedelta(days=2)
        a = full.filter(pl.col("day") < cut_day)
        b = moved.filter(pl.col("day") < cut_day)
        assert a.equals(b), f"lookahead in {rule}"
    print(f"no lookahead in any of {len(rules())} rules")
    return 0


def search() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ms, close = hourly()
    table = []
    for i, rule in enumerate(rules(), 1):
        d = score(trades(ms, close, rule, *DISCOVER), *DISCOVER)
        table.append({"trial": i, "rule": json.dumps(rule), **{f"d_{k}": v for k, v in d.items()}})
    disc = pl.DataFrame(table).sort("d_sharpe", descending=True)
    checked = []
    for row in disc.head(TOP).iter_rows(named=True):
        v = score(trades(ms, close, json.loads(row["rule"]), *VALIDATE), *VALIDATE)
        checked.append({**row, **{f"v_{k}": x for k, x in v.items()}})
    val = pl.DataFrame(checked)
    disc.write_parquet(OUT / "discover.parquet")
    val.write_parquet(OUT / "validate.parquet")
    passed = val.filter((pl.col("v_sharpe") > 0.5) & (pl.col("v_mean_net_bps") > 0)).head(3)
    summary = {"trials": len(table), "positive_discover": int((disc["d_sharpe"] > 0).sum()),
               "holdout": [json.loads(r) for r in passed["rule"]]}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pl.Config.set_tbl_rows(70)
    pl.Config.set_float_precision(2)
    pl.Config.set_tbl_width_chars(250)
    pl.Config.set_fmt_str_lengths(80)
    print(disc.select("rule", "d_trades", "d_win", "d_mean_gross_bps", "d_mean_net_bps",
                      "d_sharpe", "d_final"))
    print(val.select("rule", "d_sharpe", "v_trades", "v_win", "v_mean_gross_bps",
                     "v_mean_net_bps", "v_sharpe", "v_final"))
    print(json.dumps(summary, indent=1))
    return 0


def holdout() -> int:
    chosen = json.loads((OUT / "summary.json").read_text())["holdout"]
    if not chosen:
        raise SystemExit("no pattern passed validation; the holdout stays sealed")
    ms, close = hourly()
    hold = float(close[np.searchsorted(ms, _ms(HOLDOUT[1]) + 86_400_000)]
                 / close[np.searchsorted(ms, _ms(HOLDOUT[0]))])
    report = {"hold_final": hold, "threshold": 1.65 / np.sqrt(3.58), "rules": []}
    for rule in chosen:
        s = score(trades(ms, close, rule, *HOLDOUT), *HOLDOUT)
        report["rules"].append({"rule": rule, **s,
                                "pattern": s["sharpe"] > report["threshold"]
                                and s["mean_net_bps"] > 0})
    (OUT / "holdout.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("fetch", "check", "search", "holdout"))
    stage = parser.parse_args().stage
    return {"fetch": fetch, "check": check, "search": search, "holdout": holdout}[stage]()


if __name__ == "__main__":
    raise SystemExit(main())
