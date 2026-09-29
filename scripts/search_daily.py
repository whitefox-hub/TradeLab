#!/usr/bin/env python
"""The daily search, research/PREREG-012: 444 rules on daily closes and funding.

    python scripts/search_daily.py search            # discover + validate, seen era
    python scripts/search_daily.py unseen            # the committed rules, 10-day windows

A position is set at a day's close from data known then and earns the next day's return
minus its funding. Weights drift between rebalances; every dollar traded pays COST.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date
from itertools import product
from pathlib import Path

import numpy as np
import polars as pl

COST = 0.00065
OUT = Path("research/search_012")
ERAS = {
    "seen": (Path("data/parquet/binance_pit"), Path("data/parquet/intraday")),
    "unseen": (Path("data/parquet/binance_pit_2022"), Path("data/parquet/intraday_2022")),
}
DISCOVER = (date(2024, 11, 1), date(2025, 10, 31))
VALIDATE = (date(2025, 11, 1), date(2026, 9, 24))
UNSEEN = (date(2022, 3, 1), date(2024, 9, 24))
WINDOW_DAYS = 10
TOP = 20


@dataclass(frozen=True)
class Rule:
    family: str          # xs, ts, major
    signal: str          # mom or fund (xs); mom otherwise
    lookback: int
    hold: int
    direction: int       # +1 follow, -1 reverse
    legs: str            # ls, long, short (xs); ls, long (ts, major: long = long-flat)
    quantile: float = 0.0
    asset: str = ""


def rules() -> list[Rule]:
    out = []
    for (sig, lb), q, h, d, legs in product(
            [("mom", x) for x in (1, 3, 7, 14, 30)] + [("fund", x) for x in (1, 3, 7)],
            (0.1, 0.2), (1, 3, 7), (1, -1), ("ls", "long", "short")):
        out.append(Rule("xs", sig, lb, h, d, legs, q))
    for lb, h, d, legs in product((3, 7, 14, 30, 60), (1, 3, 7), (1, -1), ("ls", "long")):
        out.append(Rule("ts", "mom", lb, h, d, legs))
    for a, lb, h, d, legs in product(("BTC", "ETH"), (3, 7, 14, 30, 60, 90), (1, 7), (1, -1),
                                     ("ls", "long")):
        out.append(Rule("major", "mom", lb, h, d, legs, asset=a))
    return out


@dataclass
class Panel:
    days: list[date]
    assets: list[str]
    close: np.ndarray     # days x assets, NaN when not trading
    rate: np.ndarray      # daily funding, 0 when absent
    member: np.ndarray    # bool, in that month's universe


def load(era: str) -> Panel:
    panel_dir, intraday = ERAS[era]
    universe = pl.read_parquet(intraday / "_universe_monthly.parquet")
    names = sorted(set(universe["asset"]) | {"BTC", "ETH"})
    frames = [pl.read_parquet(panel_dir / f"{a}.parquet").select("day", "close", "rate")
              .with_columns(pl.lit(a).alias("asset"))
              for a in names if (panel_dir / f"{a}.parquet").exists()]
    long = pl.concat(frames).filter(pl.col("close").is_not_null() & (pl.col("close") > 0))
    days = pl.date_range(long["day"].min(), long["day"].max(), "1d", eager=True).to_list()
    assets = sorted(long["asset"].unique().to_list())
    di = {d: i for i, d in enumerate(days)}
    ai = {a: j for j, a in enumerate(assets)}
    close = np.full((len(days), len(assets)), np.nan)
    rate = np.zeros_like(close)
    for d, c, r, a in long.select("day", "close", "rate", "asset").iter_rows():
        close[di[d], ai[a]] = c
        rate[di[d], ai[a]] = r or 0.0
    member = np.zeros_like(close, dtype=bool)
    months = np.array([d.replace(day=1) for d in days])
    for m, a in universe.select("month", "asset").iter_rows():
        if a in ai:
            member[months == m, ai[a]] = True
    return Panel(days, assets, close, rate, member)


def signal(p: Panel, rule: Rule) -> np.ndarray:
    lb = rule.lookback
    if rule.signal == "fund":
        c = np.cumsum(np.vstack([np.zeros(p.rate.shape[1]), p.rate]), axis=0)
        s = np.full_like(p.close, np.nan)
        s[lb - 1:] = (c[lb:] - c[:-lb]) / lb
        return np.where(np.isfinite(p.close), s, np.nan)
    s = np.full_like(p.close, np.nan)
    s[lb:] = p.close[lb:] / p.close[:-lb] - 1
    return s


def targets(p: Panel, rule: Rule, sig: np.ndarray, t: int) -> np.ndarray:
    w = np.zeros(p.close.shape[1])
    if rule.family == "major":
        j = p.assets.index(rule.asset)
        s = sig[t, j]
        if np.isfinite(s) and s != 0:
            side = np.sign(s) * rule.direction
            w[j] = side if rule.legs == "ls" else max(side, 0.0)
        return w
    ok = p.member[t] & np.isfinite(sig[t]) & np.isfinite(p.close[t])
    idx = np.flatnonzero(ok)
    if idx.size < 10:
        return w
    s = sig[t, idx]
    if rule.family == "ts":
        side = np.sign(s) * rule.direction
        if rule.legs == "long":
            side = np.maximum(side, 0.0)
        w[idx] = side / idx.size
        return w
    k = max(1, round(rule.quantile * idx.size))
    order = np.argsort(s, kind="stable")
    top, bottom = idx[order[-k:]], idx[order[:k]]
    if rule.direction < 0:
        top, bottom = bottom, top
    if rule.legs == "ls":
        w[top], w[bottom] = 0.5 / k, -0.5 / k
    elif rule.legs == "long":
        w[top] = 1.0 / k
    else:
        w[bottom] = -1.0 / k
    return w


def run(p: Panel, rule: Rule, start: date, end: date) -> tuple[list[date], np.ndarray]:
    """Daily net returns for days in [start, end]; the book is opened at start's prior close."""
    sig = signal(p, rule)
    t0 = p.days.index(start) - 1
    t1 = p.days.index(end)
    w = np.zeros(p.close.shape[1])
    out = []
    for step, t in enumerate(range(t0, t1)):
        live = np.isfinite(p.close[t])
        cost = 0.0
        dead = (w != 0) & ~live
        if dead.any():                     # its price stopped: out at the last close
            cost += COST * np.abs(w[dead]).sum()
            w[dead] = 0.0
        if step % rule.hold == 0:
            new = targets(p, rule, sig, t)
            cost += COST * np.abs(new - w).sum()
            w = new
        nxt = p.close[t + 1] / p.close[t] - 1
        moving = (w != 0) & np.isfinite(nxt)
        r = np.where(moving, nxt, 0.0)
        funding = np.where(moving, p.rate[t + 1], 0.0)
        ret = float((w * (r - funding)).sum()) - cost
        out.append(ret)
        gross_before = w * (1 + r)
        total = 1 + float((w * r).sum())
        w = gross_before / total if total > 0 else np.zeros_like(w)
    return p.days[t0 + 1:t1 + 1], np.array(out)


def stats(r: np.ndarray) -> dict:
    sd = r.std(ddof=1)
    eq = np.cumprod(1 + r)
    return {"days": int(r.size), "mean_bps": float(r.mean() * 1e4),
            "sharpe": float(r.mean() / sd * np.sqrt(365)) if sd > 0 else 0.0,
            "return": float(eq[-1] - 1),
            "max_dd": float((eq / np.maximum.accumulate(eq) - 1).min())}


def search() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    p = load("seen")
    table = []
    for i, rule in enumerate(rules(), 1):
        _, r = run(p, rule, *DISCOVER)
        table.append({"trial": i, **asdict(rule), **{f"d_{k}": v for k, v in stats(r).items()}})
    frame = pl.DataFrame(table)
    frame.write_parquet(OUT / "discover.parquet")
    top = frame.sort("d_sharpe", descending=True).head(TOP)
    checked = []
    for row in top.iter_rows(named=True):
        rule = Rule(**{k: row[k] for k in Rule.__dataclass_fields__})
        _, r = run(p, rule, *VALIDATE)
        checked.append({**row, **{f"v_{k}": v for k, v in stats(r).items()}})
    val = pl.DataFrame(checked)
    val.write_parquet(OUT / "validate.parquet")
    passed = val.filter((pl.col("v_sharpe") > 1.0) & (pl.col("v_mean_bps") > 0)).head(3)
    summary = {"trials": frame.height, "positive_discover": int((frame["d_sharpe"] > 0).sum()),
               "passed_validation": passed.height,
               "forward": [{k: row[k] for k in Rule.__dataclass_fields__}
                           for row in passed.iter_rows(named=True)]}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pl.Config.set_tbl_width_chars(250)
    pl.Config.set_tbl_cols(30)
    pl.Config.set_tbl_rows(30)
    pl.Config.set_float_precision(2)
    print(json.dumps({k: v for k, v in summary.items() if k != "forward"}, indent=2))
    print(val.select("family", "signal", "lookback", "hold", "direction", "legs", "quantile",
                     "asset", "d_sharpe", "d_return", "v_sharpe", "v_return", "v_max_dd"))
    return 0


def unseen() -> int:
    chosen = json.loads((OUT / "summary.json").read_text())["forward"]
    if not chosen:
        raise SystemExit("no rule passed validation; the unseen era stays sealed")
    p = load("unseen")
    report = []
    for spec in chosen:
        rule = Rule(**spec)
        days, r = run(p, rule, *UNSEEN)
        windows = []
        for w0 in range(0, len(days), WINDOW_DAYS):
            part = r[w0:w0 + WINDOW_DAYS]
            cum = float(np.prod(1 + r[:w0 + part.size]) - 1)
            windows.append({"start": str(days[w0]), "window_return": float(np.prod(1 + part) - 1),
                            "cumulative": cum})
            if cum <= 0:
                break
        reached = w0 + part.size
        years = reached / 365
        report.append({"rule": spec, "windows": windows, "reached_days": reached,
                       "stats": stats(r[:reached]), "threshold": 1.65 / np.sqrt(years)})
    (OUT / "unseen.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("search", "unseen"))
    return search() if parser.parse_args().stage == "search" else unseen()


if __name__ == "__main__":
    raise SystemExit(main())
