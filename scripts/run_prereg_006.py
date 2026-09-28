"""Evaluate PREREG-006 (PREREG-003's position plus a breadth gate). One trial, no knobs.

Constants fixed by research/PREREG-006-breadth-gated.md; do not parameterise.

    python scripts/run_prereg_006.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from run_prereg_003 import LOOKBACK_DAYS, pair_spreads, panel, simulate

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.gates import evaluate
from tradelab.validation.power import assess

TRIALS = 3                 # third on this panel; the bar moves with it
BREADTH_WINDOW = 30        # inherited from FEASIBILITY-006
FLIP_LEGS = 2              # one side of each of two legs, per pair
YEAR = 365.25


def breadth_series(matrix: np.ndarray, window: int) -> np.ndarray:
    """Trailing variance-ratio breadth ending at t-1; including t would leak (probed in main)."""
    out = np.full(matrix.shape[1], np.nan)
    for t in range(window, matrix.shape[1]):
        chunk = np.nan_to_num(matrix[:, t - window:t])
        live = chunk[~np.all(chunk == 0, axis=1)]
        if live.shape[0] < 2:
            continue
        per_pair = float(np.mean(np.var(live, axis=1, ddof=1)))
        portfolio = float(np.var(live.mean(axis=0), ddof=1))
        if portfolio > 0 and per_pair > 0:
            out[t] = per_pair / portfolio
    return out


def gate(breadth: np.ndarray) -> np.ndarray:
    """Held where breadth > expanding median of readings up to t-1 (registered; not rolling)."""
    held = np.zeros(breadth.size, dtype=bool)
    seen: list[float] = []
    for t, value in enumerate(breadth):
        if not np.isfinite(value):
            continue
        if seen:
            held[t] = value > float(np.median(seen))
        seen.append(value)
    return held


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    rows = panel(args.root)
    pairs = pair_spreads(rows)
    all_days = sorted({d for f in pairs.values() for d in f["day"].to_list()})
    index = {day: i for i, day in enumerate(all_days)}

    columns, present = [], np.zeros(len(all_days))
    for frame in pairs.values():
        aligned = frame.sort("day")
        daily, _ = simulate(aligned["spread"].to_numpy(), aligned["basis"].to_numpy())
        daily = daily / 2.0
        positions = np.array([index[d] for d in aligned["day"].to_list()])
        live = np.arange(daily.size) >= LOOKBACK_DAYS
        column = np.full(len(all_days), np.nan)
        column[positions[live]] = daily[live]
        columns.append(column)
        present[positions[live]] += 1

    matrix = np.array(columns)
    traded = present > 0
    matrix, present = matrix[:, traded], present[traded]
    ungated = np.nan_to_num(matrix).sum(axis=0) / np.maximum(present, 1)

    breadth = breadth_series(matrix, BREADTH_WINDOW)
    held = gate(breadth)

    # Leak probe: perturbing day t must not change whether day t is held.
    probe = matrix.copy()
    probe[:, BREADTH_WINDOW + 5] += 0.05
    moved = int(np.sum(gate(breadth_series(probe, BREADTH_WINDOW))[:BREADTH_WINDOW + 6]
                       != held[:BREADTH_WINDOW + 6]))
    print(f"lookahead probe   : {moved} decisions moved (must be 0)")
    if moved:
        print("LEAK: the gate sees the day it gates")
        return 1

    flips = int(np.sum(np.diff(held.astype(int)) != 0))
    flip_cost = np.zeros(held.size)
    changed = np.concatenate([[False], np.diff(held.astype(int)) != 0])
    flip_cost[changed] = (present[changed] * FLIP_LEGS
                          * float(BINANCE_RETAIL.per_side) / np.maximum(present[changed], 1))

    gated = np.where(held, ungated, 0.0) - flip_cost

    evaluated = np.zeros(held.size, dtype=bool)
    evaluated[BREADTH_WINDOW:] = True
    live_gated, live_ungated = gated[evaluated], ungated[evaluated]
    live_held = held[evaluated]
    years = live_gated.size / YEAR

    def sharpe(series: np.ndarray) -> float:
        deviation = series.std(ddof=1)
        return float(series.mean() / deviation * np.sqrt(YEAR)) if deviation else 0.0

    print(f"evaluated window  : {live_gated.size} days ({years:.2f} years)")
    print(f"held              : {live_held.mean():.1%} of days")
    print(f"gate flips        : {flips}, costing "
          f"{flip_cost[evaluated].sum():.4%} of equity in total")
    print()
    print(f"ungated (PREREG-003) SHARPE {sharpe(live_ungated):+.3f}  "
          f"mean {live_ungated.mean():+.6%}")
    print(f"gated (PREREG-006)   SHARPE {sharpe(live_gated):+.3f}  "
          f"mean {live_gated.mean():+.6%}")

    power = assess(target_sharpe_annual=0.5, trials=TRIALS,
                   effective_n_available=float(live_gated.size), bars_per_year=YEAR)
    print(f"\nresolvable at {TRIALS} trials : {power.resolvable_sharpe_annual:.3f}")
    print(f"gated clears it      : {sharpe(live_gated) > power.resolvable_sharpe_annual}")
    print(f"gate changed Sharpe  : {sharpe(live_gated) - sharpe(live_ungated):+.3f}")

    stats = sharpe_stats(live_gated, effective_n=float(live_gated.size))
    result = evaluate(stats, trials=TRIALS, n_oos_trades=int(live_held.sum()),
                      bars_per_year=YEAR, min_detectable_sharpe=0.5)
    for key, value in result.metrics.items():
        print(f"  {key:26s} {value}")
    print(f"\nVERDICT: {result.verdict.upper()}")
    for reason in result.reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
