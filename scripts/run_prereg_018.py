#!/usr/bin/env python
"""PREREG-018: "Impulso +1", simulated on 5-minute bars exactly as registered.

    python scripts/run_prereg_018.py            # both spans; research/prereg_018/

Decisions at a bar's close from inputs usable then (flash_data.assemble applies the
publication lags), filled at that close. Spot costs on both sides; no funding paid.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent))
from build_flash_strike_candidates import load

from tradelab.strategy import flash_strike as fs

COST = 0.00115
TARGET_NET = 0.01
ACCOUNT, OPERATIONAL, RESERVE = 10_000.0, 6_000.0, 4_000.0
POSITION = 1_000.0                       # 10% of the account
FIRST, SECOND = 0.40, 0.60
ENTER, WEAK = 56, 40                     # of the 80 points with history
REGIMES = {"bull": (3, 1.0), "neutral": (2, 0.75), "highvol": (1, 0.5)}
LOCK_LIMIT = 0.30 * ACCOUNT
DAY, HOUR = 288, 12
MIN_ORDER = 5.0
SPANS = {"2021-11 → 2024-09": (date(2021, 11, 1), date(2024, 9, 24),
                               Path("data/parquet/intraday_2022"),
                               Path("data/parquet/binance_pit_2022")),
         "2024-10 → 2026-09": (date(2024, 10, 1), date(2026, 9, 24),
                               Path("data/parquet/intraday"), Path("data/parquet/binance_pit"))}
OUT = Path("research/prereg_018")
ASSETS = ("BTC", "ETH")


# -- inputs --------------------------------------------------------------------


def features(root: Path, panel: Path, asset: str) -> pl.DataFrame:
    bars = load(root, asset)
    assert bars is not None, f"{asset} missing in {root}"
    c = bars["close"].to_numpy()
    qv = bars["quote_volume"].cast(pl.Float64).to_numpy()
    vol = bars["volume"].cast(pl.Float64).to_numpy()
    oi = bars["open_interest"].cast(pl.Float64).to_numpy()
    ms = bars["available_ms"].to_numpy()

    def back(x: np.ndarray, k: int) -> np.ndarray:
        out = np.full(x.size, np.nan)
        out[k:] = x[:-k]
        return out

    mean, std = fs.trailing_mean(qv, DAY), fs.trailing_std(qv, DAY)
    day = (ms - 1) // 86_400_000
    frame = pl.DataFrame({"ms": ms, "day": day, "qv": qv, "vol": vol})
    cum = frame.with_columns(q=pl.col("qv").cum_sum().over("day"),
                             v=pl.col("vol").cum_sum().over("day"))
    vwap_day = (cum["q"] / cum["v"]).to_numpy()
    q12 = pl.Series(qv).rolling_sum(HOUR).to_numpy()
    v12 = pl.Series(vol).rolling_sum(HOUR).to_numpy()
    daily = pl.read_parquet(panel / f"{asset}.parquet").select("day", "rate", "n").drop_nulls()
    per_settlement = {d: (r / n if n else 0.0) for d, r, n in daily.iter_rows()}
    days = [datetime.fromtimestamp(int(d) * 86_400, UTC).date() for d in day]
    prev_funding = np.array([per_settlement.get(date.fromordinal(d.toordinal() - 1), np.nan)
                             for d in days])
    return pl.DataFrame({
        "ms": ms, "close": c, "ret1h": c / back(c, HOUR) - 1,
        "ret1h_prev": back(c, HOUR) / back(c, 2 * HOUR) - 1, "ret4h": c / back(c, 4 * HOUR) - 1,
        "ret24h": c / back(c, DAY) - 1, "vol_z": (qv - mean) / np.where(std > 0, std, np.nan),
        "qv": qv, "qv_mean": mean, "vwap_day": vwap_day,
        "vwap_1h": q12 / np.where(v12 > 0, v12, np.nan), "oi_1h": oi / back(oi, HOUR) - 1,
        "funding_prev": prev_funding,
        "down_bar": c < back(c, 1),
    }).with_columns(pl.col("down_bar").shift(1).alias("prev_down"),
                    (pl.col("qv").shift(1) > 3 * pl.col("qv_mean").shift(1)).alias("prev_heavy"))


def score(row: dict, btc: dict, asset: str) -> float:
    s = 0.0
    s += 10 * (row["ret1h"] > 0) + 10 * (row["ret4h"] > 0)
    s += 10 * (row["ret1h"] > row["ret1h_prev"])
    z = row["vol_z"]
    s += 20 if z > 1 else 10 if z > 0 else 0
    s += 10 * (row["close"] > row["vwap_day"])
    s += 5 * (row["funding_prev"] < 0.0003)
    s += 5 * (row["oi_1h"] > 0 and row["ret1h"] > 0)
    s += 10 * ((btc["ret1h"] > 0) if asset == "ETH" else (btc["ret24h"] > 0))
    return s


def regimes() -> dict[date, tuple[str, float]]:
    """Per trading day D: the regime from spot closes up to D's opening 00:00, and BTC's
    30-day daily volatility then."""
    spot = pl.read_parquet("data/parquet/spot_btc_1d.parquet")
    c = spot["close"].to_numpy()
    sma = pl.Series(c).rolling_mean(150).to_numpy()
    r = np.diff(np.log(c), prepend=np.nan)
    vol = pl.Series(r).rolling_std(30).to_numpy() * np.sqrt(365)
    out = {}
    for i, d in enumerate(spot["day"].to_list()):          # row d closes at d+1 00:00
        nxt = date.fromordinal(d.toordinal() + 1)
        if not np.isfinite(sma[i]):
            continue
        if c[i] <= sma[i]:
            out[nxt] = ("riskoff", vol[i])
        else:
            out[nxt] = ("bull" if vol[i] < 0.6 else "neutral" if vol[i] < 0.9 else "highvol",
                        vol[i])
    return out


# -- the book --------------------------------------------------------------------


@dataclass
class Position:
    asset: str
    opened: int
    entry_price: float
    regime: str
    entry_score: float
    lots: list = field(default_factory=list)     # (qty, spent, pool)
    scaled: bool = False
    rescues: int = 0
    last_buy: float = 0.0
    reached_at: int | None = None
    peak: float = 0.0
    recovery_at: int | None = None
    accumulation: bool = False
    worst: float = 0.0

    @property
    def qty(self) -> float:
        return sum(q for q, _, _ in self.lots)

    @property
    def spent(self) -> float:
        return sum(s for _, s, _ in self.lots)

    def value(self, price: float) -> float:
        return self.qty * price * (1 - COST)

    def target_price(self) -> float:
        return self.spent * (1 + TARGET_NET) / (self.qty * (1 - COST))


class Book:
    def __init__(self) -> None:
        self.pools = {"operational": OPERATIONAL, "profits": 0.0, "reserve": RESERVE}
        self.open: list[Position] = []
        self.closed: list[dict] = []

    def buy(self, p: Position, amount: float, price: float, pool: str) -> bool:
        amount = min(amount, self.pools[pool])
        if amount < MIN_ORDER:
            return False
        self.pools[pool] -= amount
        p.lots.append((amount * (1 - COST) / price, amount, pool))
        p.last_buy = price
        return True

    def sell(self, p: Position, price: float, t: int, ms: int, reason: str) -> None:
        proceeds = p.value(price)
        for _, spent, pool in p.lots:
            self.pools[pool] += spent
        profit = proceeds - p.spent
        assert profit >= p.spent * TARGET_NET - 1e-6, "sold below the target"
        self.pools["profits"] += profit
        self.open.remove(p)
        self.closed.append({"asset": p.asset, "opened_ms": int(ms_at[p.opened]), "closed_ms": ms,
                            "spent": p.spent, "profit": profit, "reason": reason,
                            "days_to_target": (p.reached_at - p.opened) / DAY,
                            "days_held": (t - p.opened) / DAY, "worst": p.worst,
                            "rescues": p.rescues, "scaled": p.scaled, "regime": p.regime,
                            "entry_score": p.entry_score, "recovered": p.recovery_at is not None,
                            "accumulation": p.accumulation})

    def locked_recovery(self) -> float:
        return sum(p.spent for p in self.open if p.recovery_at is not None)

    def equity(self, prices: dict[str, float]) -> float:
        return sum(self.pools.values()) + sum(p.value(prices[p.asset]) for p in self.open)


ms_at: np.ndarray = np.array([])


def simulate(name: str, lo: date, hi: date, root: Path, panel: Path) -> dict:
    global ms_at
    feats = {a: features(root, panel, a) for a in ASSETS}
    common = feats["BTC"].join(feats["ETH"], on="ms", suffix="_eth")
    lo_ms = int(datetime(lo.year, lo.month, lo.day, tzinfo=UTC).timestamp() * 1000)
    hi_ms = int(datetime(hi.year, hi.month, hi.day, tzinfo=UTC).timestamp() * 1000) + 86_400_000
    common = common.filter((pl.col("ms") > lo_ms) & (pl.col("ms") <= hi_ms))
    rows = {"BTC": common.select([c for c in common.columns if not c.endswith("_eth")]),
            "ETH": common.select(["ms"] + [c for c in common.columns if c.endswith("_eth")])
            .rename(lambda c: c.removesuffix("_eth"))}
    ms_at = common["ms"].to_numpy()
    data = {a: rows[a].to_dicts() for a in ASSETS}
    regime_of = regimes()
    daily_vol = {a: _daily_vol(feats[a]) for a in ASSETS}

    book = Book()
    panic_until = -1
    curve, capacity = [], []
    for t in range(len(ms_at)):
        ms = int(ms_at[t])
        bar = {a: data[a][t] for a in ASSETS}
        price = {a: bar[a]["close"] for a in ASSETS}
        today = datetime.fromtimestamp((ms - 1) / 1000, UTC).date()
        regime, _ = regime_of.get(today, ("riskoff", np.nan))
        if bar["BTC"]["ret24h"] < -0.10:
            panic_until = t + DAY
        if t <= panic_until:
            regime = "panic"
        scores = {a: score(bar[a], bar["BTC"], a) for a in ASSETS}

        # exits first: capital freed on this bar can be used on it
        for p in list(book.open):
            px = price[p.asset]
            p.worst = min(p.worst, p.value(px) / p.spent - 1)
            if px >= p.target_price():
                if p.reached_at is None:
                    p.reached_at = t
                    p.peak = px
                    if scores[p.asset] < WEAK:
                        book.sell(p, px, t, ms, "target, momentum weak")
                        continue
                p.peak = max(p.peak, px)
                if px <= p.peak * (1 - 0.005):
                    book.sell(p, px, t, ms, "trailing floor")
                    continue
            if p.recovery_at is None and (t - p.opened > 7 * DAY or
                                          p.value(px) / p.spent - 1 < -0.08):
                p.recovery_at = t
            if p.recovery_at is not None and not p.accumulation and t - p.recovery_at > 90 * DAY:
                p.accumulation = True

        allows = regime in REGIMES
        # scale-ins, from the profits pool
        for p in book.open:
            if (not p.scaled and t - p.opened <= 2 * HOUR and allows
                    and price[p.asset] >= p.entry_price * 1.005 and scores[p.asset] >= ENTER):
                size = POSITION * REGIMES[p.regime][1] if p.regime in REGIMES else POSITION
                p.scaled = book.buy(p, SECOND * size, price[p.asset], "profits")

        # one rescue per bar at most: the lowest z, if z <= 1
        if allows and book.pools["profits"] >= 2 * MIN_ORDER:
            best = None
            for p in book.open:
                a, px = p.asset, price[p.asset]
                b = bar[a]
                exhausted = (b["prev_down"] and b["prev_heavy"] and px > data[a][t - 1]["close"]
                             and px > b["vwap_1h"]) if t > 0 else False
                room = POSITION - p.spent
                if not (exhausted and p.rescues < 2 and room >= MIN_ORDER
                        and p.value(px) / p.spent - 1 < -0.05 and px <= p.last_buy * 0.95):
                    continue
                amount = min(0.5 * book.pools["profits"], room)
                qty, spent = p.qty + amount * (1 - COST) / px, p.spent + amount
                need = spent * (1 + TARGET_NET) / (qty * (1 - COST)) / px - 1
                monthly = daily_vol[a].get(today, np.nan) * np.sqrt(30)
                z = need / monthly if monthly > 0 else np.inf
                key = (z, 0 if a == "BTC" else 1)
                if z <= 1.0 and (best is None or key < best[0]):
                    best = (key, p, amount)
            if best is not None:
                _, p, amount = best
                if book.buy(p, amount, price[p.asset], "profits"):
                    p.rescues += 1

        # entries
        active = [p for p in book.open if not p.accumulation]
        if allows and book.locked_recovery() <= LOCK_LIMIT:
            limit, factor = REGIMES[regime]
            for a in ASSETS:
                if len(active) >= limit or any(p.asset == a for p in active):
                    continue
                if scores[a] < ENTER:
                    continue
                p = Position(a, t, price[a], regime, scores[a])
                if book.buy(p, FIRST * POSITION * factor, price[a], "operational"):
                    book.open.append(p)
                    active.append(p)

        if ms % 86_400_000 == 0:
            curve.append((str(today), book.equity(price)))
            capacity.append((str(today), book.pools["operational"], book.pools["profits"],
                             sum(p.spent for p in book.open), book.locked_recovery(),
                             len(book.open)))
    last = {a: data[a][-1]["close"] for a in ASSETS}
    return _report(name, book, curve, capacity, last, lo, hi)


def _daily_vol(f: pl.DataFrame) -> dict[date, float]:
    mid = f.filter(pl.col("ms") % 86_400_000 == 0)
    c = mid["close"].to_numpy()
    r = np.diff(np.log(c), prepend=np.nan)
    sd = pl.Series(r).rolling_std(30).to_numpy()
    days = [datetime.fromtimestamp(m / 1000, UTC).date() for m in mid["ms"]]
    return dict(zip(days, sd, strict=True))       # usable on the day that 00:00 opens


def _report(name, book, curve, capacity, last, lo, hi) -> dict:
    eq = np.array([v for _, v in curve])
    trades = pl.DataFrame(book.closed) if book.closed else pl.DataFrame()
    spot = pl.read_parquet("data/parquet/spot_btc_1d.parquet")
    d = spot["day"].to_list()
    c = spot["close"].to_numpy()
    i0, i1 = d.index(date.fromordinal(lo.toordinal() - 1)), d.index(hi)
    hold = ACCOUNT * (1 - COST) * c[i1] / c[i0] * (1 - COST)
    from run_prereg_015 import run
    sma = run(spot, {"kind": "sma", "a": 150, "b": 0, "check": "daily"},
              date.fromordinal(lo.toordinal() - 1), hi)
    trapped = [{"asset": p.asset, "opened": str(datetime.fromtimestamp(ms_at[p.opened] / 1000,
                                                                       UTC).date()),
                "spent": p.spent, "now": p.value(last[p.asset]),
                "accumulation": p.accumulation} for p in book.open]
    cap = np.array([row[1:] for row in capacity], dtype=float)
    out = {
        "span": name, "account_end": float(eq[-1]),
        "max_drawdown": float((eq / np.maximum.accumulate(eq) - 1).min()),
        "hold_btc_end": float(hold), "sma150_end": float(ACCOUNT * sma["final"]),
        "closed_trades": trades.height,
        "realised_profit": float(trades["profit"].sum()) if trades.height else 0.0,
        "median_days_to_target":
            float(trades["days_to_target"].median()) if trades.height else None,
        "p90_days_to_target":
            float(trades["days_to_target"].quantile(0.9)) if trades.height else None,
        "longest_days_held": float(trades["days_held"].max()) if trades.height else None,
        "worst_adverse": float(trades["worst"].min()) if trades.height else None,
        "rescues": int(trades["rescues"].sum()) if trades.height else 0,
        "scaled": int(trades["scaled"].sum()) if trades.height else 0,
        "trapped_at_end": trapped, "pools_end": book.pools,
        "max_locked": float(cap[:, 2].max()), "days_entries_blocked":
            int((cap[:, 3] > LOCK_LIMIT).sum()), "days": len(curve),
        "mean_invested_share": float(cap[:, 2].mean() / ACCOUNT),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    tag = name.split()[0]
    if trades.height:
        trades.write_parquet(OUT / f"trades_{tag}.parquet")
    pl.DataFrame(curve, schema=["day", "equity"], orient="row").write_parquet(
        OUT / f"equity_{tag}.parquet")
    pl.DataFrame(capacity, schema=["day", "operational", "profits", "invested", "locked",
                                   "open"], orient="row").write_parquet(
        OUT / f"capacity_{tag}.parquet")
    return out


def main() -> int:
    report = [simulate(name, *spec) for name, spec in SPANS.items()]
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
