#!/usr/bin/env python
"""PREREG-016: refinements of BTC's trend rule, searched on BTC, judged on twelve coins.

    python scripts/run_prereg_016.py check      # base == PREREG-015's engine; no lookahead
    python scripts/run_prereg_016.py search     # BTC, 2018-07 -> 2026-09
    python scripts/run_prereg_016.py fetch      # the twelve coins' spot daily closes
    python scripts/run_prereg_016.py holdout    # the committed three against the base

The exposure for day t+1 is decided from closes up to t's and traded at t's close. Every
change of exposure pays COST on the dollars moved.
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

COST = 0.00115
BTC = Path("data/parquet/spot_btc_1d.parquet")
COINS_FILE = Path("data/parquet/spot_holdout_1d.parquet")
OUT = Path("research/prereg_016")
SEARCH = (date(2018, 7, 1), date(2026, 9, 24))
HOLDOUT = (date(2019, 1, 1), date(2021, 10, 31))
COINS = ("ETH", "BNB", "NEO", "LTC", "QTUM", "ADA", "XRP", "EOS", "XLM", "TRX", "ETC", "ICX")
ENSEMBLE = (50, 75, 100, 125, 150, 175, 200)
BASE = {"kind": "sma", "length": 120, "band": 0.0, "confirm": 1, "sizing": "full"}
VOL_TARGET, VOL_DAYS, RESIZE = 0.50, 30, 0.10


def rules() -> list[dict]:
    out = [{"kind": "sma", "length": n, "band": b, "confirm": k, "sizing": z}
           for n, b, k, z in product((100, 120, 140), (0.0, 0.01, 0.02, 0.03), (1, 2),
                                     ("full", "vol"))]
    out += [{"kind": "ensemble", "length": 0, "band": 0.0, "confirm": 1, "sizing": z}
            for z in ("full", "vol")]
    return out


def sma(close: np.ndarray, n: int) -> np.ndarray:
    out = np.full(close.size, np.nan)
    c = np.concatenate([[0.0], np.cumsum(close)])
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def direction(close: np.ndarray, rule: dict) -> np.ndarray:
    """0..1 at each close, from closes up to it; NaN during warm-up."""
    if rule["kind"] == "ensemble":
        votes = np.array([close > sma(close, n) for n in ENSEMBLE], dtype=float)
        out = votes.mean(axis=0)
        out[: max(ENSEMBLE) - 1] = np.nan
        return out
    avg = sma(close, rule["length"])
    up = close > avg * (1 + rule["band"])
    down = close < avg * (1 - rule["band"])
    out = np.full(close.size, np.nan)
    state, streak_side, streak = 0.0, 0.0, 0
    for t in range(rule["length"] - 1, close.size):
        side = 1.0 if up[t] else 0.0 if down[t] else state
        if side != state:
            streak = streak + 1 if side == streak_side else 1
            streak_side = side
            if streak >= rule["confirm"]:
                state, streak = side, 0
        else:
            streak, streak_side = 0, state
        out[t] = state
    return out


def exposure(close: np.ndarray, rule: dict) -> np.ndarray:
    d = direction(close, rule)
    if rule["sizing"] == "full":
        return d
    r = np.diff(np.log(close), prepend=np.nan)
    vol = pl.Series(r).rolling_std(VOL_DAYS).to_numpy() * np.sqrt(365)
    scale = np.minimum(1.0, VOL_TARGET / vol)
    target = d * scale
    out = np.full(close.size, np.nan)
    held = 0.0
    for t in range(close.size):
        if not np.isfinite(target[t]):
            continue
        if target[t] == 0 or held == 0 or abs(target[t] - held) > RESIZE:
            held = target[t]
        out[t] = held
    return out


def account(close: np.ndarray, expo: np.ndarray, i0: int, i1: int) -> np.ndarray:
    """Daily returns for days i0+1..i1; the book opens flat at i0's close."""
    assert np.isfinite(expo[i0]), "warm-up not complete at the span's start"
    held, out = 0.0, []
    for t in range(i0, i1):
        cost = COST * abs(expo[t] - held)
        held = expo[t]
        out.append((1 - cost) * (1 + held * (close[t + 1] / close[t] - 1)) - 1)
    return np.array(out)


def stats(r: np.ndarray) -> dict:
    eq = np.cumprod(1 + r)
    sd = r.std(ddof=1)
    return {"final": float(eq[-1]), "sharpe": float(r.mean() / sd * np.sqrt(365)) if sd else 0.0,
            "max_drawdown": float((eq / np.maximum.accumulate(eq) - 1).min()),
            "cagr": float(eq[-1] ** (365 / r.size) - 1)}


def span(frame: pl.DataFrame, lo: date, hi: date) -> tuple[np.ndarray, int, int]:
    days = frame["day"].to_list()
    return frame["close"].to_numpy(), days.index(lo), days.index(hi)


def check() -> int:
    """The base reproduces PREREG-015's engine, and no exposure moves when the future does."""
    import sys
    sys.path.insert(0, "scripts")
    from run_prereg_015 import run
    frame = pl.read_parquet(BTC)
    close, i0, i1 = span(frame, *SEARCH)
    mine = stats(account(close, exposure(close, BASE), i0, i1))["final"]
    theirs = run(frame, {"kind": "sma", "a": 120, "b": 0, "check": "daily"}, *SEARCH)["final"]
    print(f"base {mine:.10f} vs PREREG-015 engine {theirs:.10f}")
    assert abs(mine - theirs) < 1e-9
    rng = np.random.default_rng(16)
    for rule in rules():
        full = exposure(close, rule)
        cut = int(rng.integers(400, close.size - 10))
        bent = close.copy()
        bent[cut + 1:] *= rng.uniform(0.5, 1.5, close.size - cut - 1)
        moved = exposure(bent, rule)[: cut + 1]
        assert np.array_equal(np.nan_to_num(full[: cut + 1], nan=-1),
                              np.nan_to_num(moved, nan=-1)), f"lookahead in {rule}"
    print(f"no lookahead in any of {len(rules())} rules")
    return 0


def search() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pl.read_parquet(BTC)
    close, i0, i1 = span(frame, *SEARCH)
    table = [{**rule, **stats(account(close, exposure(close, rule), i0, i1))}
             for rule in rules()]
    frame_out = pl.DataFrame(table).sort("sharpe", descending=True)
    frame_out.write_parquet(OUT / "search.parquet")
    base = frame_out.filter((pl.col("kind") == "sma") & (pl.col("length") == 120)
                            & (pl.col("band") == 0) & (pl.col("confirm") == 1)
                            & (pl.col("sizing") == "full")).row(0, named=True)
    better = frame_out.filter(pl.col("sharpe") > base["sharpe"]).head(3)
    summary = {"trials": len(table), "base": base, "better_than_base":
               int((frame_out["sharpe"] > base["sharpe"]).sum()),
               "holdout": better.select("kind", "length", "band", "confirm", "sizing").to_dicts()}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pl.Config.set_tbl_rows(60)
    pl.Config.set_float_precision(3)
    pl.Config.set_tbl_width_chars(200)
    print(frame_out)
    print(json.dumps({k: v for k, v in summary.items() if k != "holdout"}, indent=1))
    print("to the holdout:", summary["holdout"])
    return 0


def fetch() -> int:
    rows = []
    with httpx.Client(timeout=60.0) as client:
        for coin in COINS:
            month = date(2017, 8, 1)
            while month <= date(2021, 10, 1):
                text = fetch_text(client, "https://data.binance.vision/data/spot/monthly/"
                                  f"klines/{coin}USDT/1d/{coin}USDT-1d-{month:%Y-%m}.zip") or ""
                for line in text.splitlines():
                    cells = line.split(",")
                    if cells[0].isdigit():
                        day = datetime.fromtimestamp(int(cells[0]) / 1000, UTC).date()
                        rows.append((coin, day, float(cells[4])))
                month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
            print(f"  {coin}: {sum(r[0] == coin for r in rows)} days", flush=True)
    frame = pl.DataFrame(rows, schema={"coin": pl.Utf8, "day": pl.Date, "close": pl.Float64},
                         orient="row").unique(["coin", "day"]).sort(["coin", "day"])
    frame.write_parquet(COINS_FILE)
    return 0


def holdout() -> int:
    chosen = json.loads((OUT / "summary.json").read_text())["holdout"]
    if not chosen:
        raise SystemExit("no refinement beat the base on BTC; the holdout stays sealed")
    coins = pl.read_parquet(COINS_FILE)
    report: dict = {"coins": {}}
    candidates = [BASE, *chosen]
    for coin in COINS:
        frame = coins.filter(pl.col("coin") == coin).select("day", "close")
        close, i0, i1 = span(frame, *HOLDOUT)
        gaps = np.diff(np.array(frame["day"].to_list())[i0:i1 + 1]).astype("timedelta64[D]")
        assert (gaps == np.timedelta64(1, "D")).all(), f"{coin} has missing days"
        hold = np.ones(close.size)
        report["coins"][coin] = {
            "hold": stats(account(close, hold, i0, i1)),
            **{json.dumps(r, sort_keys=True): stats(account(close, exposure(close, r), i0, i1))
               for r in candidates}}
    base_key = json.dumps(BASE, sort_keys=True)
    verdicts = []
    for rule in chosen:
        key = json.dumps(rule, sort_keys=True)
        wins = sum(report["coins"][c][key]["sharpe"] > report["coins"][c][base_key]["sharpe"]
                   for c in COINS)
        median = float(np.median([report["coins"][c][key]["sharpe"] for c in COINS]))
        base_median = float(np.median([report["coins"][c][base_key]["sharpe"] for c in COINS]))
        verdicts.append({"rule": rule, "wins": int(wins), "median_sharpe": median,
                         "base_median_sharpe": base_median,
                         "adopted": wins >= 8 and median > base_median})
    adopted = [v for v in verdicts if v["adopted"]]
    report["verdicts"] = verdicts
    report["rule"] = max(adopted, key=lambda v: v["median_sharpe"])["rule"] if adopted else BASE
    (OUT / "holdout.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(verdicts, indent=2))
    print("the rule:", report["rule"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("check", "search", "fetch", "holdout"))
    stage = parser.parse_args().stage
    return {"check": check, "search": search, "fetch": fetch, "holdout": holdout}[stage]()


if __name__ == "__main__":
    raise SystemExit(main())
