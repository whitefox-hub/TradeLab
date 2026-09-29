#!/usr/bin/env python
"""PREREG-019: Adaptive Profit-Capacity Trading, eight variants on the same signals.

    python scripts/run_prereg_019.py check       # truncation (no lookahead), capital invariants
    python scripts/run_prereg_019.py train       # the 27-point grid on training
    python scripts/run_prereg_019.py validate    # the 3 best on validation; one chosen
    python scripts/run_prereg_019.py bootstrap   # 40 seeds x 8 variants, training blocks
    python scripts/run_prereg_019.py fetch-oos   # 2019-10 -> 2021-10 5-minute bars
    python scripts/run_prereg_019.py oos         # the chosen parameters and the 8 variants

Decisions at a bar's close from data up to it, filled at that close.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import product
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent))
from build_flash_strike_candidates import load

COST = 0.00115
RESERVE, START, CAP_MAX = 8_000.0, 2_000.0, 5_000.0
BASE_SIZE = 100.0
LADDER = ((800, 180.0), (500, 160.0), (300, 140.0), (150, 125.0), (50, 110.0), (0, 100.0))
DAY, HOUR = 288, 12
MIN_ORDER = 5.0
ASSETS = ("BTC", "ETH")
SEEN = (Path("data/parquet/intraday_2022"), Path("data/parquet/intraday"))
OOS_ROOT = Path("data/parquet/intraday_2019")
TRAIN = (date(2021, 11, 1), date(2025, 6, 30))
VALIDATE = (date(2025, 7, 1), date(2026, 9, 24))
OOS = (date(2020, 1, 1), date(2021, 10, 31))
OUT = Path("research/prereg_019")
DEFAULT = {"threshold": 65, "aging_days": 14, "target_scale": 1.0}
GRID = [{"threshold": a, "aging_days": b, "target_scale": c}
        for a, b, c in product((55, 65, 75), (7, 14, 30), (0.75, 1.0, 1.25))]
REGIME = {  # threshold, size, max utilisation, cooldown, target
    "bear": (15, 0.5, 0.5, 2.0, 0.8), "highvol": (10, 0.7, 0.7, 1.5, 1.2),
    "bull": (-5, 1.0, 0.9, 1.0, 1.2), "neutral": (0, 0.9, 0.85, 1.0, 1.0)}


def ms_of(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


# -- market data -----------------------------------------------------------------


@dataclass
class Market:
    ms: np.ndarray
    close: dict
    qv: dict
    tbq: dict
    regime: np.ndarray          # per bar: bull / neutral / highvol / bear


def read_bars(roots: tuple[Path, ...], asset: str) -> pl.DataFrame:
    frames = []
    for root in roots:
        bars = load(root, asset)
        if bars is not None:
            frames.append(bars.select("available_ms", "close", "quote_volume", "taker_buy_quote"))
    return pl.concat(frames).unique("available_ms", keep="last").sort("available_ms")


def regimes_from_daily(days: list[date], closes: np.ndarray) -> dict[date, str]:
    """Regime for the trading day after each daily close."""
    sma = pl.Series(closes).rolling_mean(150).to_numpy()
    r = np.diff(np.log(closes), prepend=np.nan)
    vol = pl.Series(r).rolling_std(30).to_numpy() * np.sqrt(365)
    out = {}
    for d, c, s, v in zip(days, closes, sma, vol, strict=True):
        nxt = date.fromordinal(d.toordinal() + 1)
        if not np.isfinite(s):
            out[nxt] = "neutral"
        elif c <= s:
            out[nxt] = "bear"
        else:
            out[nxt] = "highvol" if v > 0.9 else "bull" if v < 0.6 else "neutral"
    return out


def market(roots: tuple[Path, ...]) -> Market:
    frames = {a: read_bars(roots, a) for a in ASSETS}
    joined = frames["BTC"].join(frames["ETH"], on="available_ms", suffix="_e").sort("available_ms")
    ms = joined["available_ms"].to_numpy()
    spot = pl.read_parquet("data/parquet/spot_btc_1d.parquet")
    reg = regimes_from_daily(spot["day"].to_list(), spot["close"].to_numpy())
    bar_day = [datetime.fromtimestamp((m - 1) / 1000, UTC).date() for m in ms]
    return Market(ms, {"BTC": joined["close"].to_numpy(), "ETH": joined["close_e"].to_numpy()},
                  {"BTC": joined["quote_volume"].cast(pl.Float64).to_numpy(),
                   "ETH": joined["quote_volume_e"].cast(pl.Float64).to_numpy()},
                  {"BTC": joined["taker_buy_quote"].cast(pl.Float64).to_numpy(),
                   "ETH": joined["taker_buy_quote_e"].cast(pl.Float64).to_numpy()},
                  np.array([reg.get(d, "neutral") for d in bar_day]))


def bootstrap_market(base: Market, seed: int, days: int) -> Market:
    """A path of `days` days built from 30-day blocks of `base`, both assets together."""
    rng = np.random.default_rng([19, seed])
    starts = np.flatnonzero(base.ms % 86_400_000 == 0)
    block = 30 * DAY
    starts = starts[starts + block < base.ms.size]
    parts = []
    while sum(len(p) for p in parts) < days * DAY:
        s = int(rng.choice(starts))
        parts.append(np.arange(s + 1, s + block + 1))
    idx = np.concatenate(parts)[: days * DAY]
    ms = ms_of(date(2000, 1, 1)) + DAY * 0 + np.arange(1, idx.size + 1) * 300_000
    close = {}
    for a in ASSETS:
        r = np.log(base.close[a][idx] / base.close[a][idx - 1])
        close[a] = 100.0 * np.exp(np.cumsum(r))
    mid = np.flatnonzero(ms % 86_400_000 == 0)
    d = [datetime.fromtimestamp(m / 1000, UTC).date() for m in ms[mid]]
    d = [date.fromordinal(x.toordinal() - 1) for x in d]           # the day each close ends
    reg = regimes_from_daily(d, close["BTC"][mid])
    bar_day = [datetime.fromtimestamp((m - 1) / 1000, UTC).date() for m in ms]
    return Market(ms, close, {a: base.qv[a][idx] for a in ASSETS},
                  {a: base.tbq[a][idx] for a in ASSETS},
                  np.array([reg.get(x, "neutral") for x in bar_day]))


# -- features and signals -----------------------------------------------------------


def trailing(x: np.ndarray, n: int, fn: str) -> np.ndarray:
    """Statistic of the n bars before t (t excluded)."""
    s = pl.Series(x).shift(1)
    return (s.rolling_mean(n) if fn == "mean" else s.rolling_std(n)).to_numpy()


def features(m: Market) -> dict:
    out = {}
    for a in ASSETS:
        c, qv, tb = m.close[a], m.qv[a], m.tbq[a]

        def back(x, k):
            y = np.full(x.size, np.nan)
            y[k:] = x[:-k]
            return y
        lr = np.diff(np.log(c), prepend=np.nan)
        vol24 = pl.Series(lr).rolling_std(DAY).to_numpy() * np.sqrt(DAY * 365)
        mid = np.flatnonzero(m.ms % 86_400_000 == 0)
        med = np.full(c.size, np.nan)
        daily_vol = vol24[mid]
        daily_med = pl.Series(daily_vol).rolling_median(30).to_numpy()
        pos = np.searchsorted(mid, np.arange(c.size), side="right") - 1
        ok = pos >= 0
        med[ok] = daily_med[pos[ok]]
        q12 = pl.Series(qv).rolling_sum(HOUR).to_numpy()
        t12 = pl.Series(tb).rolling_sum(HOUR).to_numpy()
        mean, std = trailing(qv, DAY, "mean"), trailing(qv, DAY, "std")
        out[a] = {"ret1h": c / back(c, HOUR) - 1, "ret4h": c / back(c, 4 * HOUR) - 1,
                  "sma24": pl.Series(c).rolling_mean(DAY).to_numpy(),
                  "share": t12 / np.where(q12 > 0, q12, np.nan),
                  "vz": (qv - mean) / np.where(std > 0, std, np.nan),
                  "vr": vol24 / med}
    return out


def signals(m: Market, f: dict, lo: int, hi: int) -> list[tuple[int, str]]:
    """First bar of each UTC hour, per asset, where the trigger holds; in [lo, hi)."""
    out = []
    hour = (m.ms - 1) // 3_600_000
    for a in ASSETS:
        cond = (f[a]["ret1h"] > 0) & (f[a]["share"] > 0.5) & (m.close[a] > f[a]["sma24"])
        cond &= (np.arange(m.ms.size) >= lo) & (np.arange(m.ms.size) < hi)
        seen = set()
        for t in np.flatnonzero(cond):
            if hour[t] not in seen:
                seen.add(hour[t])
                out.append((int(t), a))
    return sorted(out)


def score(f: dict, m: Market, t: int, a: str, util: float, old_share: float) -> float:
    g = f[a]
    s = 12.5 * (g["ret1h"][t] > 0) + 12.5 * (g["ret4h"][t] > 0)
    s += 10 * (m.close[a][t] > g["sma24"][t])
    s += {"bull": 10, "neutral": 5}.get(m.regime[t], 0)
    s += 10 * (f["BTC"]["ret4h"][t] > 0)
    vr = g["vr"][t]
    s += 10 * (0.5 <= vr <= 1.5)
    vz = g["vz"][t]
    s += 15 if vz > 1 else 8 if vz > 0 else 0
    sh = g["share"][t]
    s += 20 if sh > 0.55 else 10 if sh > 0.5 else 0
    s -= 10 * (vr > 2)
    s -= 20 * max(0.0, util - 0.5) / 0.5
    s -= 10 * old_share
    return float(s)


# -- the book -------------------------------------------------------------------------


def simulate(m: Market, f: dict, sig: list, lo: int, hi: int, variant: int,
             p: dict) -> dict:
    v = variant
    cash, harvest, realised = START, 0.0, 0.0
    open_: list[dict] = []
    trades: list[dict] = []
    by_t: dict[int, list[str]] = {}
    for t, a in sig:
        by_t.setdefault(t, []).append(a)
    next_ok = {a: -1 for a in ASSETS}
    since_profit = 0
    day_entries, day_key = 0, None
    last_profit_t = lo
    longest_dry = 0
    daily_real, curve, committed, book_peak = [], [], [], None
    defensive = False
    bought = 0.0
    caps = []
    max_open = 0
    for t in range(lo, hi):
        px = {a: m.close[a][t] for a in ASSETS}
        # exits
        for pos in list(open_):
            a = pos["asset"]
            value = pos["qty"] * px[a] * (1 - COST)
            net = value / pos["spent"] - 1
            pos["worst"] = min(pos["worst"], net)
            target = pos["target"]
            if v >= 6 and t - pos["t0"] >= p["aging_days"] * DAY:
                target = min(target, 0.003)
            sell = False
            if pos["trail"] and target == pos["target"]:
                if net >= pos["target"]:
                    pos["peak"] = max(pos["peak"], px[a])
                    sell = px[a] <= pos["peak"] * (1 - 0.007)
            else:
                sell = net >= target
            if sell:
                profit = value - pos["spent"]
                assert profit >= -1e-9, "a realised loss"
                open_.remove(pos)
                cash += pos["spent"]
                realised += profit
                if v >= 4:
                    room = CAP_MAX - (cash + sum(q["spent"] for q in open_))
                    add = min(profit, max(room, 0.0))
                    cash += add
                    harvest += profit - add
                else:
                    harvest += profit
                longest_dry = max(longest_dry, t - last_profit_t)
                last_profit_t = t
                since_profit = 0
                trades.append({"asset": a, "t0": pos["t0"], "t1": t, "spent": pos["spent"],
                               "profit": profit, "days": (t - pos["t0"]) / DAY,
                               "worst": pos["worst"]})
        locked = sum(q["spent"] for q in open_)
        capacity = cash + locked
        util = locked / capacity if capacity > 0 else 1.0
        regime = m.regime[t]
        max_open = max(max_open, len(open_))
        # daily: snapshot, defensive mode, entry count
        if m.ms[t] % 86_400_000 == 0:
            values = sum(q["qty"] * px[q["asset"]] * (1 - COST) for q in open_)
            book = cash + values
            curve.append(RESERVE + book + harvest)
            committed.append(locked)
            caps.append(capacity)
            daily_real.append(realised)
            book_peak = book if book_peak is None else max(book_peak, book)
            if v >= 8:
                ages = [(t - q["t0"]) / DAY for q in open_]
                rates = np.diff(np.array(daily_real[-61:])) if len(daily_real) > 2 else np.array([])
                slow = (rates.size >= 60 and rates[-14:].mean() < 0.5 * rates.mean())
                defensive = bool(slow or locked > 0.6 * capacity
                                 or book < 0.9 * book_peak or (ages and np.mean(ages) > 10))
        key = m.ms[t] // 86_400_000
        if key != day_key:
            day_key, day_entries = key, 0
        # entries
        for a in sorted(by_t.get(t, []), key=lambda x: -score(f, m, t, x, util, 0.0)):
            old_share = (np.mean([(t - q["t0"]) > 7 * DAY for q in open_]) if open_ else 0.0)
            s = score(f, m, t, a, util, old_share)
            th = p["threshold"]
            rg = REGIME[regime] if v >= 8 else (0, 1.0, 0.9, 1.0, 1.0)
            if v >= 8:
                th += rg[0] + (10 if defensive else 0)
            if v >= 6 and open_ and np.mean([(t - q["t0"]) / DAY for q in open_]) > 14:
                th += 10
            if v >= 2 and s < th:
                continue
            locked = sum(q["spent"] for q in open_)
            capacity = cash + locked
            util = locked / capacity if capacity > 0 else 1.0
            size = BASE_SIZE
            if v >= 5:
                size = next(z for need, z in LADDER if realised >= need)
                max_util = min(rg[2], 0.7 if defensive else 1.0) if v >= 8 else 0.9
                if util > max_util or (util >= 0.8 and s < th + 15):
                    continue
                if util >= 0.7:
                    size *= 0.75
            if v >= 8:
                size *= rg[1] * (0.7 if defensive else 1.0)
                in_asset = sum(q["spent"] for q in open_ if q["asset"] == a)
                if in_asset + size > 0.6 * capacity:
                    continue
            if v >= 7 and (t < next_ok[a] or day_entries >= 6):
                continue
            if cash < size or size < MIN_ORDER:
                continue
            target, trail = 0.01, False
            if v >= 3:
                target = 0.01 if s < 75 else 0.0175 if s < 85 else 0.025 if s < 90 else 0.015
                trail = s >= 90
                target *= p["target_scale"] * (rg[4] if v >= 8 else 1.0)
            cash -= size
            bought += size
            open_.append({"asset": a, "t0": t, "spent": size,
                          "qty": size * (1 - COST) / px[a], "target": target, "trail": trail,
                          "peak": px[a], "worst": 0.0})
            since_profit += 1
            day_entries += 1
            if v >= 7:
                wait = (6 if s >= 85 else 12) * (1 + since_profit / 3)
                wait *= (rg[3] if v >= 8 else 1.0) * (2 if (v >= 8 and defensive) else 1)
                next_ok[a] = t + round(wait)
    longest_dry = max(longest_dry, hi - last_profit_t)
    last_px = {a: m.close[a][hi - 1] for a in ASSETS}
    unreal = sum(q["qty"] * last_px[q["asset"]] * (1 - COST) - q["spent"] for q in open_)
    eq = np.array(curve)
    mean_committed = float(np.mean(committed)) if committed else 0.0
    under = [tr["days"] for tr in trades if tr["worst"] < -0.01]
    return {
        "variant": v, **p, "final": float(eq[-1]) if eq.size else RESERVE + START,
        "realised": realised, "unrealised_end": unreal, "trades": len(trades),
        "profit_per_trade": realised / len(trades) if trades else 0.0,
        "win_rate": len(trades) / (len(trades) + len(open_)) if trades or open_ else 0.0,
        "avg_win": float(np.mean([tr["profit"] for tr in trades])) if trades else 0.0,
        "max_committed": float(max(committed)) if committed else 0.0,
        "mean_committed": mean_committed, "open_end": len(open_),
        "max_open": max_open,
        "recovery_days": float(np.mean(under)) if under else 0.0,
        "max_drawdown": float((eq / np.maximum.accumulate(eq) - 1).min()) if eq.size else 0.0,
        "longest_dry_days": longest_dry / DAY,
        "rotation": bought / float(np.mean(caps)) if caps else 0.0,
        "eff_realised": realised / mean_committed if mean_committed else 0.0,
        "eff_marked": (realised + unreal) / mean_committed if mean_committed else 0.0,
        "harvest": harvest, "capacity_end": float(caps[-1]) if caps else START,
    }


def span_index(m: Market, lo: date, hi: date) -> tuple[int, int]:
    a = int(np.searchsorted(m.ms, ms_of(lo), side="right"))
    b = int(np.searchsorted(m.ms, ms_of(hi) + 86_400_000, side="right"))
    return a, b


def benchmarks(lo: date, hi: date) -> dict:
    sys.path.insert(0, "scripts")
    from run_prereg_015 import run
    spot = pl.read_parquet("data/parquet/spot_btc_1d.parquet")
    start = date.fromordinal(lo.toordinal() - 1)
    hold = run(spot, None, start, hi)["final"]
    sma = run(spot, {"kind": "sma", "a": 150, "b": 0, "check": "daily"}, start, hi)["final"]
    return {"hold_btc": 10_000 * hold, "sma150": 10_000 * sma}


# -- stages ---------------------------------------------------------------------------


def _one(args):
    kind, seed, variant, params = args
    if kind == "seen":
        m = market(SEEN)
        lo, hi = span_index(m, *TRAIN)
    else:
        base = market(SEEN)
        b0, b1 = span_index(base, *TRAIN)
        cut = slice(max(b0 - 1, 0), b1)
        trimmed = Market(base.ms[cut], {a: base.close[a][cut] for a in ASSETS},
                         {a: base.qv[a][cut] for a in ASSETS},
                         {a: base.tbq[a][cut] for a in ASSETS}, base.regime[cut])
        days = (TRAIN[1] - TRAIN[0]).days + 150
        m = bootstrap_market(trimmed, seed, days)
        lo, hi = 150 * DAY, m.ms.size
    f = features(m)
    sig = signals(m, f, lo, hi)
    out = simulate(m, f, sig, lo, hi, variant, params)
    out["seed"], out["signals"] = seed, len(sig)
    return out


def pool(jobs: list) -> list[dict]:
    with ProcessPoolExecutor(10, mp_context=multiprocessing.get_context("spawn")) as ex:
        return list(ex.map(_one, jobs))


def check() -> int:
    m = market(SEEN)
    f = features(m)
    lo, hi = span_index(m, *TRAIN)
    cut = int(np.searchsorted(m.ms, ms_of(date(2023, 6, 30))))
    sig = signals(m, f, lo, hi)
    for v in range(1, 9):
        full = simulate(m, f, sig, lo, hi, v, DEFAULT)
        part = simulate(m, f, [s for s in sig if s[0] < cut], lo, cut, v, DEFAULT)
        # the same book up to the cut: compare what both realised by then
        assert part["realised"] <= full["realised"] + 1e-6
        print(f"variant {v}: {len(sig)} signals, trades {full['trades']}, "
              f"realised {full['realised']:.2f}, final {full['final']:,.0f}")
    # lookahead: bending prices after the cut must not change any trade opened and closed before
    bent = Market(m.ms.copy(), {a: m.close[a].copy() for a in ASSETS}, m.qv, m.tbq, m.regime)
    rng = np.random.default_rng(7)
    for a in ASSETS:
        bent.close[a][cut:] *= rng.uniform(0.8, 1.2, bent.close[a].size - cut)
    fb = features(bent)
    for v in (1, 5, 8):
        x = simulate(m, f, [s for s in sig if s[0] < cut - DAY], lo, cut - DAY, v, DEFAULT)
        sb = signals(bent, fb, lo, hi)
        y = simulate(bent, fb, [s for s in sb if s[0] < cut - DAY], lo, cut - DAY, v, DEFAULT)
        assert abs(x["realised"] - y["realised"]) < 1e-9 and x["trades"] == y["trades"], v
    print("no lookahead: books before the cut are identical when later prices are bent")
    return 0


def train() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = pool([("seen", 0, 8, g) for g in GRID])
    frame = pl.DataFrame(rows).sort("eff_marked", descending=True)
    frame.write_parquet(OUT / "train_grid.parquet")
    top = frame.head(3).select("threshold", "aging_days", "target_scale").to_dicts()
    (OUT / "train_top3.json").write_text(json.dumps(top, indent=2))
    _show(frame, ["threshold", "aging_days", "target_scale", "trades", "realised",
                  "unrealised_end", "eff_realised", "eff_marked", "max_drawdown", "final"])
    return 0


def validate() -> int:
    top = json.loads((OUT / "train_top3.json").read_text())
    m = market(SEEN)
    f = features(m)
    lo, hi = span_index(m, *VALIDATE)
    sig = signals(m, f, lo, hi)
    rows = [simulate(m, f, sig, lo, hi, 8, p) for p in top]
    frame = pl.DataFrame(rows)
    frame.write_parquet(OUT / "validate.parquet")
    best = frame.sort("eff_marked", descending=True).row(0, named=True)
    chosen = {k: best[k] for k in DEFAULT}
    (OUT / "chosen.json").write_text(json.dumps(chosen, indent=2))
    _show(frame, ["threshold", "aging_days", "target_scale", "trades", "realised",
                  "unrealised_end", "eff_realised", "eff_marked", "max_drawdown", "final"])
    print("chosen:", chosen, "signals:", len(sig))
    return 0


def bootstrap() -> int:
    rows = pool([("boot", s, v, DEFAULT) for s in range(40) for v in range(1, 9)])
    frame = pl.DataFrame(rows)
    frame.write_parquet(OUT / "bootstrap.parquet")
    keys = ["final", "realised", "max_drawdown", "eff_realised", "eff_marked",
            "max_committed", "longest_dry_days", "recovery_days"]
    summary = frame.group_by("variant").agg(
        [pl.col(k).quantile(q).alias(f"{k}_p{int(q * 100)}") for k in keys
         for q in (0.05, 0.25, 0.5, 0.75, 0.95)]).sort("variant")
    summary.write_parquet(OUT / "bootstrap_summary.parquet")
    print("signals per seed:", frame["signals"].min(), "-", frame["signals"].max())
    for k in keys:
        _show(summary, ["variant"] + [f"{k}_p{q}" for q in (5, 25, 50, 75, 95)])
    return 0


def fetch_oos() -> int:
    import httpx

    from tradelab.collectors.binance.intraday import month_of_klines
    with httpx.Client(timeout=60.0) as client:
        for a in ASSETS:
            out = OOS_ROOT / "klines" / a
            out.mkdir(parents=True, exist_ok=True)
            month = date(2019, 9, 1)
            while month <= date(2021, 10, 1):
                frame = month_of_klines(client, "klines", f"{a}USDT", month, date(2021, 10, 31))
                if not frame.is_empty():
                    frame.write_parquet(out / f"{month:%Y-%m}.parquet")
                month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
            print(f"  {a}: {len(list(out.glob('*.parquet')))} months", flush=True)
    return 0


def oos() -> int:
    chosen = json.loads((OUT / "chosen.json").read_text())
    m = market((OOS_ROOT,))
    f = features(m)
    lo, hi = span_index(m, *OOS)
    sig = signals(m, f, lo, hi)
    rows = [simulate(m, f, sig, lo, hi, v, chosen) for v in range(1, 9)]
    rows.append({**simulate(m, f, sig, lo, hi, 8, DEFAULT), "variant": 80})
    frame = pl.DataFrame(rows)
    frame.write_parquet(OUT / "oos.parquet")
    report = {"chosen": chosen, "signals": len(sig), "benchmarks": benchmarks(*OOS)}
    (OUT / "oos.json").write_text(json.dumps(report, indent=2))
    _show(frame, ["variant", "trades", "realised", "unrealised_end", "eff_realised",
                  "eff_marked", "max_committed", "max_drawdown", "longest_dry_days", "final"])
    print(json.dumps(report, indent=2))
    return 0


def _show(frame: pl.DataFrame, cols: list[str]) -> None:
    pl.Config.set_tbl_rows(60)
    pl.Config.set_tbl_cols(20)
    pl.Config.set_float_precision(3)
    pl.Config.set_tbl_width_chars(250)
    print(frame.select(cols))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("check", "train", "validate", "bootstrap",
                                          "fetch-oos", "oos", "variants"))
    stage = parser.parse_args().stage
    if stage == "variants":
        return variants()
    return {"check": check, "train": train, "validate": validate, "bootstrap": bootstrap,
            "fetch-oos": fetch_oos, "oos": oos}[stage]()


def variants() -> int:
    """The 8 variants on training and validation with default parameters."""
    m = market(SEEN)
    f = features(m)
    rows = []
    for name, span in (("train", TRAIN), ("validate", VALIDATE)):
        lo, hi = span_index(m, *span)
        sig = signals(m, f, lo, hi)
        for v in range(1, 9):
            rows.append({"span": name, "signals": len(sig),
                         **simulate(m, f, sig, lo, hi, v, DEFAULT)})
    frame = pl.DataFrame(rows)
    frame.write_parquet(OUT / "variants_seen.parquet")
    _show(frame, ["span", "variant", "signals", "trades", "realised", "unrealised_end",
                  "eff_realised", "eff_marked", "max_committed", "max_drawdown",
                  "longest_dry_days", "final"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
