"""Evaluate PREREG-007. One trial, no knobs.

Everything here is fixed by research/PREREG-007-funding-carry-neutral.md,
committed (7e98c31) before this file existed. The constants are asserted rather
than parameterised, because a script with options is a grid however it is run.

Before the panel is read, the book is run on two synthetic panels and a
perturbed copy of itself: one where funding carries nothing, one where it is
planted to predict, and one probe that must not move a single past weight. A
construction that finds an edge in noise, misses a planted one, or sees the day
it trades has no business being pointed at the real data.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, PESSIMISTIC, CostModel
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.gates import evaluate
from tradelab.validation.power import resolvable_sharpe

VENUE = "binance"
HOLD_DAYS = 7              # upper edge of §0.2's band; cost killed PREREG-004
SIGNAL_DAYS = 7            # matches the hold, so one choice and not two
ELIGIBLE_DAYS = 90         # FINDING-009's beta window
MIN_CROSS_SECTION = 40     # FEASIBILITY-008's smallest measured row
TRIALS = 1                 # this family
TRIALS_ALL_FUNDING = 4     # every funding trial in the project, reported beside
CONCENTRATION_DAYS = 5
CONCENTRATION_LIMIT = 0.5  # five days above half the net return: inconclusive
SENSITIVITY_HOLDS = (3, 14)  # reported once; the decision rests on 7
YEAR = 365.25


@dataclass
class Book:
    """Daily series of one run, in fractions of equity."""

    net: np.ndarray
    funding: np.ndarray
    price: np.ndarray
    cost: np.ndarray
    market: np.ndarray
    contributions: np.ndarray   # assets x days, price leg per position
    rebalances: list[int]
    weights: dict[int, np.ndarray]
    start: int


def matrices(root: Path) -> tuple[np.ndarray, np.ndarray, list]:
    """Closes and daily funding, assets x calendar days, NaN where absent."""
    rows = pl.concat([pl.read_parquet(f) for f in sorted(root.glob("*.parquet"))])
    rows = rows.filter(pl.col("venue") == VENUE)
    days = pl.date_range(rows["day"].min(), rows["day"].max(), "1d", eager=True)
    frame = pl.DataFrame({"day": days})
    closes, rates = [], []
    for asset in rows["asset"].unique().sort():
        one = frame.join(rows.filter(pl.col("asset") == asset).select("day", "close", "rate"),
                         on="day", how="left")
        closes.append(one["close"].to_numpy().astype(np.float64))
        rates.append(one["rate"].to_numpy().astype(np.float64))
    return np.array(closes), np.array(rates), days.to_list()


def target_weights(close: np.ndarray, rate: np.ndarray, t: int) -> np.ndarray | None:
    """Weights to hold from day t, from data through t-1 only.

    Rank of the trailing mean funding, lowest longest. Demeaned, so the book is
    dollar-neutral; scaled to unit gross. None where the cross-section is below
    the registered minimum, which means standing flat.
    """
    if t < ELIGIBLE_DAYS:
        return None
    window = slice(t - ELIGIBLE_DAYS, t)
    eligible = (np.all(np.isfinite(close[:, window]), axis=1)
                & np.all(np.isfinite(rate[:, window]), axis=1))
    if eligible.sum() < MIN_CROSS_SECTION:
        return None
    signal = rate[eligible, t - SIGNAL_DAYS:t].mean(axis=1)
    ranks = _average_ranks(signal)
    raw = -(ranks - ranks.mean())
    gross = np.abs(raw).sum()
    if gross == 0:
        return None
    weights = np.zeros(close.shape[0])
    weights[eligible] = raw / gross
    return weights


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Ranks from 1, ties sharing their mean rank, as the registration says."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size)
    ranks[order] = np.arange(1, values.size + 1)
    for value in np.unique(values):
        tied = values == value
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return ranks


def run(close: np.ndarray, rate: np.ndarray, costs: CostModel = BINANCE_RETAIL,
        hold: int = HOLD_DAYS) -> Book:
    """The registered book, day by day, with weights drifting between rebalances."""
    assets, days = close.shape
    filled = _forward_fill(close)
    returns = np.zeros_like(close)
    returns[:, 1:] = np.nan_to_num(filled[:, 1:] / filled[:, :-1] - 1.0)
    funding = np.nan_to_num(rate)

    start = next((t for t in range(days) if target_weights(close, rate, t) is not None),
                 None)
    if start is None:
        raise SystemExit("the cross-section never reaches the registered minimum")

    equity, value = 1.0, np.zeros(assets)
    net, fund, price, cost, market = (np.zeros(days - start) for _ in range(5))
    contributions = np.zeros((assets, days - start))
    rebalances, weights = [], {}
    for t in range(start, days):
        k = t - start
        opening = equity
        traded = 0.0
        if k % hold == 0:
            target = target_weights(close, rate, t)
            target = np.zeros(assets) if target is None else target * opening
            traded = float(np.abs(target - value).sum()) * costs.per_side
            value = target
            rebalances.append(t)
            weights[t] = target / opening
        price_pnl = float(value @ returns[:, t])
        funding_pnl = -float(value @ funding[:, t])   # positive funding: longs pay
        equity = opening - traded + price_pnl + funding_pnl

        price[k], fund[k], cost[k] = (price_pnl / opening, funding_pnl / opening,
                                      -traded / opening)
        net[k] = equity / opening - 1.0
        contributions[:, k] = value * returns[:, t] / opening
        live = np.isfinite(close[:, t]) & np.isfinite(close[:, t - 1])
        market[k] = float(returns[live, t].mean()) if live.any() else 0.0
        value = value * (1.0 + returns[:, t])
    return Book(net, fund, price, cost, market, contributions, rebalances, weights, start)


def _forward_fill(matrix: np.ndarray) -> np.ndarray:
    out = matrix.copy()
    for row in out:
        last = np.nan
        for i, value in enumerate(row):
            if np.isfinite(value):
                last = value
            else:
                row[i] = last
    return out


def sharpe(series: np.ndarray) -> float:
    deviation = series.std(ddof=1)
    return float(series.mean() / deviation * np.sqrt(YEAR)) if deviation else 0.0


# -- the checks that run before the panel is read ----------------------------


def synthetic(seed: int, planted: float) -> tuple[np.ndarray, np.ndarray]:
    """120 assets, 500 days. `planted` makes high funding precede a fall."""
    rng = np.random.default_rng(seed)
    assets, days = 120, 500
    persistent = rng.normal(0.0, 0.0003, size=(assets, 1))
    rate = persistent + rng.normal(0.0, 0.0002, size=(assets, days))
    common = rng.normal(0.0, 0.03, size=days)
    returns = common + rng.normal(0.0, 0.03, size=(assets, days))
    returns[:, 1:] -= planted * (rate[:, :-1] - rate[:, :-1].mean(axis=0))
    close = 100 * np.cumprod(1 + returns, axis=1)
    return close, rate


def lookahead_probe(close: np.ndarray, rate: np.ndarray, day: int) -> int:
    """Perturb one day. Every weight formed on or before it must not move."""
    base = {t: target_weights(close, rate, t) for t in range(day - 30, day + 1)}
    close2, rate2 = close.copy(), rate.copy()
    close2[:, day] *= 1.5
    rate2[:, day] += 0.01
    moved = 0
    for t, before in base.items():
        after = target_weights(close2, rate2, t)
        if (before is None) != (after is None) or (
                before is not None and not np.allclose(before, after)):
            moved += 1
    return moved


def preflight() -> bool:
    noise = [sharpe(run(*synthetic(seed, 0.0)).price) for seed in range(5)]
    planted = [sharpe(run(*synthetic(seed, 20.0)).price) for seed in range(5)]
    moved = lookahead_probe(*synthetic(0, 0.0), day=200)
    print("preflight")
    print(f"  noise panel, price-leg Sharpe   : {np.round(noise, 2)}  (must straddle 0)")
    print(f"  planted panel, price-leg Sharpe : {np.round(planted, 2)}  (must be > 0)")
    print(f"  lookahead probe                 : {moved} weights moved (must be 0)")
    ok = moved == 0 and min(planted) > 0 and abs(float(np.mean(noise))) < 1.0
    print(f"  {'PASSED' if ok else 'FAILED — the real panel is not read'}\n")
    return ok


# -- the run -----------------------------------------------------------------


def report(book: Book, days: list) -> None:
    net = book.net
    years = net.size / YEAR
    print(f"evaluated          : {days[book.start]} -> {days[-1]}, "
          f"{net.size} days ({years:.2f} years), {len(book.rebalances)} rebalances")
    held = [int(np.count_nonzero(w)) for w in book.weights.values()]
    print(f"names held         : median {int(np.median(held))}, "
          f"min {min(held)}, max {max(held)}")

    total = float(net.sum())
    print("\nlegs, summed over the window (fractions of equity)")
    print(f"  funding          : {book.funding.sum():+.4%}")
    print(f"  price            : {book.price.sum():+.4%}")
    print(f"  costs            : {book.cost.sum():+.4%}")
    print(f"  net              : {total:+.4%}")
    for label, series in (("funding", book.funding), ("price", book.price),
                          ("gross", book.funding + book.price), ("net", net)):
        print(f"  Sharpe {label:10s}: {sharpe(series):+.3f}")

    beta = float(np.cov(net, book.market)[0, 1] / np.var(book.market, ddof=1))
    print(f"\nrealised beta to the equal-weighted market : {beta:+.3f}")
    per_position = float(np.sum(np.var(book.contributions, axis=1, ddof=1)))
    portfolio = float(np.var(book.contributions.sum(axis=0), ddof=1))
    print(f"realised breadth (price leg)             : "
          f"{per_position / portfolio if portfolio else float('nan'):.1f}")

    # Rule 1 of the registration, before anything else is read as an answer.
    top = np.sort(net)[::-1][:CONCENTRATION_DAYS]
    share = float(top.sum() / total) if total > 0 else float("nan")
    weekly = np.array([net[i:i + 7].sum() for i in range(0, net.size - 6, 7)])
    print(f"\nconcentration      : top {CONCENTRATION_DAYS} days = "
          f"{share:.1%} of net return (limit {CONCENTRATION_LIMIT:.0%})")
    print(f"weeks positive     : {np.mean(weekly > 0):.1%} of {weekly.size}")
    print(f"weekly median/mean : {np.median(weekly):+.4%} / {weekly.mean():+.4%}")


def verdict(book: Book) -> None:
    net = book.net
    total = float(net.sum())
    bar = resolvable_sharpe(net.size, TRIALS, YEAR)
    bar_all = resolvable_sharpe(net.size, TRIALS_ALL_FUNDING, YEAR)
    observed = sharpe(net)
    print(f"\nnet Sharpe         : {observed:+.3f}")
    print(f"resolvable, {TRIALS} trial : {bar:.3f}")
    print(f"resolvable, {TRIALS_ALL_FUNDING} trials: {bar_all:.3f}")

    stats = sharpe_stats(net, effective_n=float(net.size))
    gate = evaluate(stats, trials=TRIALS, n_oos_trades=len(book.rebalances),
                    bars_per_year=YEAR, min_detectable_sharpe=bar / np.sqrt(YEAR))
    for key, value in gate.metrics.items():
        print(f"  {key:26s} {value}")
    print(f"  gate: {gate.verdict.upper()}")
    for reason in gate.reasons:
        print(f"    - {reason}")

    # The registration's order, applied as written.
    top = np.sort(net)[::-1][:CONCENTRATION_DAYS].sum()
    if total > 0 and top / total > CONCENTRATION_LIMIT:
        answer = "INCONCLUSIVE — rule 1: the return is a few days, not frequent small gains"
    elif total <= 0 or observed <= 0:
        answer = "REFUTED — rule 5: net at or below zero"
    elif gate.metrics.get("moments_out_of_range"):
        answer = "INCONCLUSIVE — rule 2: moments outside the deflated Sharpe's range"
    elif observed > bar:
        answer = ("PASSES THIS FAMILY'S BAR — rule 3: not a candidate; the "
                  "survivorship rebuild and 180 forward days are next")
    else:
        answer = "INCONCLUSIVE — rule 4: positive and below the bar; the answer is calendar"
    print(f"\nVERDICT (PREREG-007): {answer}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    if not preflight():
        return 1

    close, rate, days = matrices(args.root)
    print(f"panel              : {close.shape[0]} assets, {len(days)} days, {VENUE}")
    moved = lookahead_probe(close, rate, day=len(days) // 2)
    print(f"lookahead probe    : {moved} weights moved on the real panel (must be 0)")
    if moved:
        return 1

    book = run(close, rate)
    report(book, days)
    verdict(book)

    print("\ndeclared sensitivities, reported once and not decided on")
    print(f"  PESSIMISTIC costs : net Sharpe {sharpe(run(close, rate, PESSIMISTIC).net):+.3f}")
    for hold in SENSITIVITY_HOLDS:
        print(f"  hold {hold:2d} days      : net Sharpe "
              f"{sharpe(run(close, rate, hold=hold).net):+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
