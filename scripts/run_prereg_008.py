"""Evaluate PREREG-008, Flash Strike. One procedure, one trial.

research/PREREG-008-flash-strike.md (2615de8), committed before any intraday
data existed. The procedure, not a configuration, is what is tested: every
month from 2025-03 it selects one of the twelve exit configurations by purged
cross-validation on the past, fits the fixed model, calibrates it, freezes it,
and trades the month. Only those months count.

    python scripts/run_prereg_008.py                # the verdict, with its nulls
    python scripts/run_prereg_008.py --preflight    # the checks alone, no market data read

Before the market candidates are read, the procedure runs on synthetic
candidates twice: once with an edge planted in one feature, which it must
find, and once on noise, which it must not. The run refuses to continue if
either fails, and it refuses a verdict without the queue calibration.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from tradelab.strategy.flash_strike import CONFIGS, FEATURES, MAKER, TAKER
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.pbo import probability_of_backtest_overfitting
from tradelab.validation.power import resolvable_sharpe

FIRST_TEST = date(2025, 3, 1)
LAST_TEST = date(2026, 9, 1)
EMBARGO_MS = 86_400_000                 # one day
INNER_FOLDS = 4
CALIBRATION_SHARE = 0.2
PERMUTATIONS = 20
YEAR = 365.25

MODEL = {"n_estimators": 200, "max_depth": 3, "num_leaves": 8, "learning_rate": 0.05,
         "min_child_samples": 200, "verbose": -1, "n_jobs": 8, "random_state": 20260927}

RISK_PER_TRADE = 0.0025                 # of equity, at the stop
EQUITY = 10_000.0                       # USD; sets only the volume cap's bite
MAX_OPEN = 10
MAX_POSITION = 0.25                     # §14.1 max_position_pct_equity
MAX_GROSS = 2.0                         # §14.1 max_gross_exposure
MAX_DAILY_LOSS = 0.05                   # §14.1: no new entries after it, that day
WIN_COST = MAKER + MAKER
LOSS_COST = MAKER + TAKER


# -- the model -----------------------------------------------------------------


@dataclass
class Fitted:
    model: lgb.LGBMClassifier | None
    calibrator: IsotonicRegression | None
    constant: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.model is None or self.calibrator is None:
            return np.full(x.shape[0], self.constant)
        return self.calibrator.predict(self.model.predict_proba(x)[:, 1])


def fit(x: np.ndarray, y: np.ndarray, weight: np.ndarray) -> Fitted:
    """The fixed model on the earlier 80%, isotonic calibration on the last 20%.

    Rows are in time order, so the calibration slice is the most recent part of
    the training window, which is the part the next month most resembles.
    """
    cut = int(len(y) * (1 - CALIBRATION_SHARE))
    if cut < 400 or len(set(y[:cut])) < 2 or len(set(y[cut:])) < 2:
        return Fitted(None, None, float(y.mean()) if len(y) else 0.0)
    model = lgb.LGBMClassifier(**MODEL)
    model.fit(x[:cut], y[:cut], sample_weight=weight[:cut])
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(model.predict_proba(x[cut:])[:, 1], y[cut:])
    return Fitted(model, calibrator, 0.0)


def uniqueness(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Average uniqueness per sample on a 5-minute clock (§7).

    Candidates across the universe overlap in time, and overlapping labels are
    not independent observations. Concurrency is counted across the whole book,
    which is conservative: two assets' trades in the same minutes share the
    market's move.
    """
    step = 300_000
    s = ((start - start.min()) // step).astype(np.int64)
    e = ((end - start.min()) // step).astype(np.int64)
    active = np.zeros(e.max() + 2)
    np.add.at(active, s, 1)
    np.add.at(active, e + 1, -1)
    active = np.cumsum(active)[:-1]
    inverse = np.divide(1.0, active, out=np.zeros_like(active), where=active > 0)
    cumulative = np.concatenate([[0.0], np.cumsum(inverse)])
    return (cumulative[e + 1] - cumulative[s]) / (e - s + 1)


def breakeven(frame: pl.DataFrame, config: int) -> np.ndarray:
    """p* = (SL + cost_loss) / (TP + SL + cost_loss - cost_win), per candidate.

    SL and TP as fractions of the entry price, from the candidate's own ATR.
    """
    sl, tp, _ = CONFIGS[config]
    stop = sl * frame["atr"].to_numpy() / frame["entry"].to_numpy()
    target = tp * stop
    # PREREG-010's market entry pays taker in: win = taker + maker, loss = 2 x taker.
    market = (frame["entry_market"].to_numpy() if "entry_market" in frame.columns
              else np.zeros(frame.height, dtype=bool))
    win = np.where(market, TAKER + MAKER, WIN_COST)
    loss = np.where(market, 2 * TAKER, LOSS_COST)
    return (stop + loss) / (target + stop + loss - win)


def matrix(frame: pl.DataFrame) -> np.ndarray:
    return frame.select(FEATURES).to_numpy().astype(np.float64)


# -- one month of the procedure --------------------------------------------------


def month_bounds(month: date) -> tuple[int, int]:
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    nxt = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    end = datetime(nxt.year, nxt.month, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def training(events: pl.DataFrame, month_start: int, config: int) -> pl.DataFrame:
    """Candidates whose label was determined a day before the month opens."""
    return events.filter(pl.col(f"exit_ms_{config}") < month_start - EMBARGO_MS)


def inner_value(train: pl.DataFrame, config: int) -> float:
    """Net expected value per trade under the break-even rule, by purged CV.

    Folds are contiguous in time. A training fold drops every sample whose label
    window overlaps the validation fold, and every sample in the day after it.
    """
    n = train.height
    if n < INNER_FOLDS * 500:
        return -np.inf
    fill = train["fill_ms"].to_numpy()
    exit_ = train[f"exit_ms_{config}"].to_numpy()
    x, y = matrix(train), (train[f"reason_{config}"].to_numpy() == 1).astype(int)
    net, threshold = train[f"net_{config}"].to_numpy(), breakeven(train, config)
    weight = uniqueness(fill, exit_)
    taken: list[np.ndarray] = []
    bounds = np.linspace(0, n, INNER_FOLDS + 1).astype(int)
    for k in range(INNER_FOLDS):
        test = np.arange(bounds[k], bounds[k + 1])
        lo, hi = fill[test].min(), exit_[test].max()
        keep = np.setdiff1d(np.arange(n), test)
        keep = keep[~((exit_[keep] >= lo) & (fill[keep] <= hi))]              # purge
        keep = keep[~((fill[keep] > hi) & (fill[keep] <= hi + EMBARGO_MS))]  # embargo
        model = fit(x[keep], y[keep], weight[keep])
        chosen = model.predict(x[test]) > threshold[test]
        taken.append(net[test][chosen])
    flat = np.concatenate(taken)
    return float(flat.mean()) if flat.size >= 30 else -np.inf


@dataclass
class MonthDecision:
    month: date
    config: int
    fitted: Fitted


def decide(events: pl.DataFrame, month: date, fixed: int | None = None,
           permute: np.random.Generator | None = None) -> MonthDecision:
    """Select a configuration on the past, fit and freeze the model for `month`.

    `fixed` bypasses the selection, for PBO's per-configuration series.
    `permute` shuffles labels within each training month, for the null.
    """
    month_start, _ = month_bounds(month)
    choices = [fixed] if fixed is not None else range(len(CONFIGS))
    best, best_value = choices[0] if fixed is not None else 0, -np.inf
    trains = {}
    for c in choices:
        train = training(events, month_start, c)
        if permute is not None:
            train = _permuted(train, permute)
        trains[c] = train
        if fixed is None:
            value = inner_value(train, c)
            if value > best_value:
                best, best_value = c, value
    train = trains[best]
    fitted = fit(matrix(train), (train[f"reason_{best}"].to_numpy() == 1).astype(int),
                 uniqueness(train["fill_ms"].to_numpy(), train[f"exit_ms_{best}"].to_numpy()))
    return MonthDecision(month, best, fitted)


def _permuted(train: pl.DataFrame, rng: np.random.Generator) -> pl.DataFrame:
    """Labels shuffled within each month; features and times untouched."""
    outcome = [c for c in train.columns if c.startswith(("net_", "reason_"))]
    parts = []
    for _, part in train.group_by("month", maintain_order=True):
        order = rng.permutation(part.height)
        parts.append(part.with_columns([part[c].gather(order).alias(c) for c in outcome]))
    return pl.concat(parts).sort(["fill_ms", "asset", "shock_ms"]) if parts else train


# -- the book ------------------------------------------------------------------


def book(trades: pl.DataFrame, days: list[date]) -> tuple[np.ndarray, np.ndarray]:
    """Fixed risk per trade under §14.1, one new entry per bar across the book.

    `trades` carries fill_ms, exit_ms, net, stop (fraction), cap_notional and
    priority. Returns daily returns on the evaluation calendar and each taken
    trade's result in equity terms.
    """
    trades = trades.sort(["fill_ms", "priority", "asset"], descending=[False, True, False])
    open_: list[tuple[int, float]] = []          # (exit_ms, notional fraction)
    last_bar, day_loss, day_key = -1, 0.0, None
    daily = {d: 0.0 for d in days}
    results = []
    for row in trades.iter_rows(named=True):
        now = row["fill_ms"]
        open_ = [(e, n) for e, n in open_ if e > now]
        today = datetime.fromtimestamp(now / 1000, UTC).date()
        if today != day_key:
            day_key, day_loss = today, 0.0
        if now == last_bar or len(open_) >= MAX_OPEN or day_loss <= -MAX_DAILY_LOSS:
            continue
        notional = min(RISK_PER_TRADE / max(row["stop"], 1e-6), MAX_POSITION,
                       row["cap_notional"] / EQUITY)
        if notional <= 0 or sum(n for _, n in open_) + notional > MAX_GROSS:
            continue
        pnl = notional * row["net"]
        exit_day = datetime.fromtimestamp(row["exit_ms"] / 1000, UTC).date()
        if exit_day in daily:
            daily[exit_day] += pnl
        if exit_day == today:
            day_loss += pnl
        open_.append((row["exit_ms"], notional))
        last_bar = now
        results.append(pnl)
    return np.array([daily[d] for d in days]), np.array(results)


#: Candidates in one bar share `fill_ms` across assets (62% of events). Every sort is on
#: this key so that thinning, CV folds and the book's tie-break are reproducible (§9.4):
#: sorted on `fill_ms` alone, a rebuilt file reordered ties and moved 186 trades to 107.
ORDER = ["fill_ms", "asset", "shock_ms"]


def trades_for(frame: pl.DataFrame, decisions: list[MonthDecision],
               take_all: bool = False) -> pl.DataFrame:
    parts = []
    for d in decisions:
        lo, hi = month_bounds(d.month)
        month = frame.filter((pl.col("fill_ms") >= lo) & (pl.col("fill_ms") < hi))
        if month.is_empty():
            continue
        c = d.config
        p = d.fitted.predict(matrix(month)) if not take_all else np.ones(month.height)
        taken = month.with_columns(
            pl.Series("priority", p), pl.Series("threshold", breakeven(month, c)),
            pl.col(f"net_{c}").alias("net"), pl.col(f"exit_ms_{c}").alias("exit_ms"),
            pl.col(f"reason_{c}").alias("reason"),
            (CONFIGS[c][0] * pl.col("atr") / pl.col("entry")).alias("stop"))
        if not take_all:
            taken = taken.filter(pl.col("priority") > pl.col("threshold"))
        parts.append(taken.select("asset", "fill_ms", "exit_ms", "net", "reason", "stop",
                                  "cap_notional", "priority"))
    return pl.concat(parts) if parts else pl.DataFrame()


def sharpe(daily: np.ndarray) -> float:
    sd = daily.std(ddof=1)
    return float(daily.mean() / sd * np.sqrt(YEAR)) if sd > 0 else 0.0


def evaluation_days() -> list[date]:
    lo, _ = month_bounds(FIRST_TEST)
    first = datetime.fromtimestamp(lo / 1000, UTC).date()
    last = date(2026, 9, 25)
    return [date.fromordinal(o) for o in range(first.toordinal(), last.toordinal() + 1)]


def test_months() -> list[date]:
    out, m = [], FIRST_TEST
    while m <= LAST_TEST:
        out.append(m)
        m = date(m.year + m.month // 12, m.month % 12 + 1, 1)
    return out


def procedure(events: pl.DataFrame, permute: np.random.Generator | None = None,
              fixed: int | None = None) -> list[MonthDecision]:
    return [decide(events, m, fixed=fixed, permute=permute) for m in test_months()]


# -- preflight on synthetic candidates -------------------------------------------


def synthetic(planted: bool, seed: int = 7) -> pl.DataFrame:
    """Candidates with the real schema. With `planted`, feature `shock_taker_share`
    raises the take-profit probability; otherwise outcomes are coin flips at the
    base rate."""
    rng = np.random.default_rng(seed)
    n = 60_000
    lo, _ = month_bounds(date(2024, 11, 1))
    _, hi = month_bounds(LAST_TEST)
    fill = np.sort(rng.integers(lo, hi, n))
    frame = {f: rng.normal(0, 1, n) for f in FEATURES}
    frame |= {"fill_ms": fill, "atr": np.full(n, 1.0), "entry": np.full(n, 400.0),
              "cap_notional": np.full(n, 1e9), "asset": np.array(["SYN"] * n),
              "month": [datetime.fromtimestamp(f / 1000, UTC).strftime("%Y-%m") for f in fill]}
    edge = frame["shock_taker_share"]
    for c, (sl, tp, h) in enumerate(CONFIGS):
        stop = sl / 400.0
        base = (stop + LOSS_COST) / (tp * stop + stop + LOSS_COST - WIN_COST)
        p = np.clip(base + (0.25 * np.tanh(edge) if planted else 0.0), 0.01, 0.99)
        win = rng.random(n) < p
        frame[f"reason_{c}"] = np.where(win, 1, -1)
        frame[f"net_{c}"] = np.where(win, tp * stop - WIN_COST, -stop - LOSS_COST)
        frame[f"exit_ms_{c}"] = fill + h * 60_000
    return pl.DataFrame(frame)


def preflight() -> bool:
    days = evaluation_days()
    results = {}
    for label, planted in (("planted", True), ("noise", False)):
        events = synthetic(planted)
        daily, _ = book(trades_for(events, procedure(events)), days)
        results[label] = sharpe(daily)
    print("preflight on synthetic candidates")
    print(f"  planted edge, OOS Sharpe : {results['planted']:+.2f}  (must be > 1)")
    print(f"  noise,        OOS Sharpe : {results['noise']:+.2f}  (must be < 1)")
    ok = results["planted"] > 1.0 and results["noise"] < 1.0
    print(f"  {'PASSED' if ok else 'FAILED: the market candidates are not read'}\n")
    return ok


# -- the run -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet/intraday"))
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--permutations", type=int, default=PERMUTATIONS)
    args = parser.parse_args()

    if not preflight():
        return 1
    if args.preflight:
        return 0

    haircut_path = args.root / "_queue_calibration.json"
    if not haircut_path.exists():
        print("REFUSED: no queue calibration. The registration applies its haircut to "
              "the whole backtest, and a verdict without it is on the bar model alone.")
        return 1
    haircut = json.loads(haircut_path.read_text())["confirmed_share"]

    candidates = pl.read_parquet(args.root / "_candidates.parquet")
    shocks = pl.read_parquet(args.root / "_shocks.parquet")
    events = candidates.filter(pl.col("kind") == "event").sort(["fill_ms", "asset", "shock_ms"])
    randoms = candidates.filter(pl.col("kind") == "random").sort(["fill_ms", "asset", "shock_ms"])
    # The haircut, as a random thinning of bar-model fills, fixed seed.
    thin = np.random.default_rng(8).random(events.height) < haircut
    events = events.filter(pl.Series(thin))
    days = evaluation_days()

    decisions = procedure(events)
    taken = trades_for(events, decisions)
    daily, per_trade = book(taken, days)
    observed = sharpe(daily)

    print(f"events {events.height:,} after a queue haircut of {haircut:.3f}; "
          f"shocks {shocks.height:,}, bar-model fill rate {shocks['filled'].mean():.3f}")
    adverse = shocks.group_by("filled").agg(pl.col("after_15m").mean()).sort("filled")
    print(f"15-minute move from the limit, unfilled vs filled: "
          f"{adverse['after_15m'].to_list()}")
    print(f"configurations chosen by month: {[d.config for d in decisions]}")
    print(f"trades taken {per_trade.size:,}, {per_trade.size / len(days):.2f} per day")
    print(f"\nOOS net Sharpe of the procedure: {observed:+.3f}")

    rules_daily, _ = book(trades_for(events, decisions, take_all=True), days)
    random_daily, _ = book(trades_for(randoms, decisions, take_all=True), days)
    print(f"rules-only baseline:   {sharpe(rules_daily):+.3f}")
    print(f"random-entry baseline: {sharpe(random_daily):+.3f}")

    null = []
    for k in range(args.permutations):
        rng = np.random.default_rng(1000 + k)
        null_daily, _ = book(trades_for(events, procedure(events, permute=rng)), days)
        null.append(sharpe(null_daily))
        print(f"  permutation {k + 1}/{args.permutations}: {null[-1]:+.3f}", flush=True)
    null_95 = float(np.percentile(null, 95)) if null else float("nan")

    per_config = np.column_stack([
        book(trades_for(events, procedure(events, fixed=c)), days)[0]
        for c in range(len(CONFIGS))])
    pbo = probability_of_backtest_overfitting(per_config, n_blocks=10).pbo

    stats = sharpe_stats(daily, effective_n=float(daily.size))
    bar = resolvable_sharpe(daily.size, 1, YEAR)
    worst = np.sort(per_trade)[:max(1, per_trade.size // 100)] if per_trade.size else np.array([])
    print(f"\npermuted-label null, 95th pct: {null_95:+.3f}")
    print(f"PBO over the 12 configurations: {pbo:.3f}")
    print(f"skew / kurtosis: {stats.skew:+.2f} / {stats.kurtosis:.2f}")
    print(f"worst 1% of trades: min {worst.min() if worst.size else float('nan'):+.4%}, "
          f"limit {-2 * RISK_PER_TRADE:+.4%}")
    print(f"resolvable at 1 trial: {bar:.3f}")

    if not (observed > null_95 and pbo <= 0.5):
        verdict = "INCONCLUSIVE, rule 1: the null or PBO"
    elif abs(stats.skew) > 3.0 or stats.kurtosis > 30.0:
        verdict = "INCONCLUSIVE, rule 2: moments"
    elif worst.size and worst.min() < -2 * RISK_PER_TRADE:
        verdict = "INCONCLUSIVE, rule 3: the stop does not bound the loss"
    elif observed > bar and observed > sharpe(rules_daily) and observed > sharpe(random_daily):
        verdict = "PASSES, rule 4: the forward simulation starts"
    elif observed > 0:
        verdict = "INCONCLUSIVE, rule 5: positive, below the bar; the forward simulation runs"
    else:
        verdict = "REFUTED, rule 6"
    print(f"\nVERDICT (PREREG-008): {verdict}")

    legs = taken.with_columns(pl.when(pl.col("reason") == 1).then(pl.lit("maker exit"))
                              .otherwise(pl.lit("taker exit")).alias("leg"))
    print("\nby exit leg (per-trade net, notional terms):")
    for row in legs.group_by("leg").agg(pl.len().alias("n"), pl.col("net").mean().alias("mean"))\
            .sort("leg").iter_rows(named=True):
        print(f"  {row['leg']:11s} n={row['n']:6d} mean {row['mean']:+.4%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
