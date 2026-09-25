"""Evaluate PREREG-003. One trial, no knobs.

Everything here is fixed by research/PREREG-003-funding-dispersion.md, committed
while the panel was still downloading. The constants are asserted rather than
parameterised, because a script with options is a grid however it is run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.gates import evaluate
from tradelab.validation.power import assess

TRIALS = 1
LOOKBACK_DAYS = 90        # one quarter, per the registration
REBALANCE_DAYS = 30       # monthly
LEGS = 4                  # two legs, opened and closed
REFERENCE = "binance"


def panel(root: Path) -> pl.DataFrame:
    return pl.concat([pl.read_parquet(f) for f in sorted(root.glob("*.parquet"))])


def pair_spreads(rows: pl.DataFrame) -> dict[tuple[str, str], pl.DataFrame]:
    """Binance against each other venue, per asset. Inner join, never filled."""
    out: dict[tuple[str, str], pl.DataFrame] = {}
    reference = rows.filter(pl.col("venue") == REFERENCE)
    for asset in rows["asset"].unique().sort():
        left = reference.filter(pl.col("asset") == asset).select("day", "rate")
        if left.height < LOOKBACK_DAYS + REBALANCE_DAYS:
            continue
        for venue in ("bybit", "okx", "gate", "bitget"):
            right = rows.filter(
                (pl.col("asset") == asset) & (pl.col("venue") == venue)
            ).select("day", pl.col("rate").alias("rate_b"))
            joined = left.join(right, on="day", how="inner").sort("day")
            if joined.height < LOOKBACK_DAYS + REBALANCE_DAYS:
                continue
            out[(asset, venue)] = joined.with_columns(
                (pl.col("rate") - pl.col("rate_b")).alias("spread")
            )
    return out


def simulate(spread: np.ndarray) -> tuple[np.ndarray, int]:
    """Sign of the trailing 90-day mean, refreshed monthly. Returns daily net
    P&L in rate units, and the number of sign flips paid for."""
    n = spread.size
    daily = np.zeros(n)
    position = 0
    flips = 0
    for start in range(LOOKBACK_DAYS, n, REBALANCE_DAYS):
        window = spread[start - LOOKBACK_DAYS:start]
        target = int(np.sign(window.mean()))
        stop = min(start + REBALANCE_DAYS, n)
        daily[start:stop] = target * spread[start:stop]
        # Charged before `position` is updated. The first version assigned
        # `position = target` inside the comparison above and then tested
        # `target != position` again, which is False by construction — so no
        # cost was ever charged and the reported Sharpe was 40. Same shape as
        # the gross-for-net defect in run_study: a variable reassigned between
        # the test and its use.
        if target != position:
            flips += 1
            daily[start] -= LEGS * float(BINANCE_RETAIL.per_side)
        position = target
    return daily, flips


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    rows = panel(args.root)
    pairs = pair_spreads(rows)
    print(f"assets in panel        : {rows['asset'].n_unique()}")
    print(f"usable asset-venue pairs: {len(pairs)}")
    if not pairs:
        print("no usable pairs")
        return 1

    # Each pair runs on its own calendar and contributes to the days it has.
    # Intersecting every pair's day set collapsed a 730-day window to 142 days,
    # because one pair with a short history truncates all 470.
    all_days = sorted({d for f in pairs.values() for d in f["day"].to_list()})
    index = {day: i for i, day in enumerate(all_days)}
    print(f"union of days          : {len(all_days)}")

    contribution = np.zeros(len(all_days))
    present = np.zeros(len(all_days))
    columns: list[np.ndarray] = []
    total_flips = 0
    for frame in pairs.values():
        aligned = frame.sort("day")
        daily, flips = simulate(aligned["spread"].to_numpy())
        # Capital sits on both venues, so the return per unit of committed
        # capital is half the return on one leg's notional (§ the registration).
        daily = daily / 2.0
        positions = np.array([index[d] for d in aligned["day"].to_list()])
        live_mask = np.arange(daily.size) >= LOOKBACK_DAYS
        contribution[positions[live_mask]] += daily[live_mask]
        present[positions[live_mask]] += 1
        column = np.full(len(all_days), np.nan)
        column[positions[live_mask]] = daily[live_mask]
        columns.append(column)
        total_flips += flips

    traded = present > 0
    portfolio = np.zeros(len(all_days))
    portfolio[traded] = contribution[traded] / present[traded]
    live = portfolio[traded]
    matrix = np.array(columns)[:, traded]
    print(f"pairs live per day     : median {np.median(present[traded]):.0f}")

    years = len(live) / 365.25
    stats = sharpe_stats(live, effective_n=float(len(live)))
    annualised = stats.sharpe * np.sqrt(365.25)
    power = assess(target_sharpe_annual=0.5, trials=TRIALS,
                   effective_n_available=float(len(live)), bars_per_year=365.25)

    corr = np.corrcoef(np.nan_to_num(matrix))
    off = corr[~np.eye(len(corr), dtype=bool)]
    eig = np.linalg.eigvalsh(np.nan_to_num(corr))
    breadth = float(eig.sum() ** 2 / (eig**2).sum())

    print(f"\nlive window            : {len(live)} days ({years:.2f} years)")
    print(f"sign flips paid        : {total_flips} across {len(pairs)} pairs")
    print(f"mean daily (net, /2)   : {live.mean():+.6%}")
    print(f"annualised return      : {live.mean() * 365.25:+.2%}")
    print(f"daily vol              : {live.std(ddof=1):.6%}")
    print(f"mean pair correlation  : {np.nanmean(off):+.4f}")
    print(f"effective breadth      : {breadth:.1f} of {len(pairs)}")
    print(f"ANNUALISED SHARPE      : {annualised:+.3f}")
    print(f"resolvable at 1 trial  : {power.resolvable_sharpe_annual:.3f}")

    gate = evaluate(stats, trials=TRIALS, n_oos_trades=len(live),
                    bars_per_year=365.25, min_detectable_sharpe=0.5)
    print(f"\nVERDICT: {gate.verdict.upper()}")
    for reason in gate.reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
