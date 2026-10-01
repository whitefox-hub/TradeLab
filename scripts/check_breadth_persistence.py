"""Does effective breadth persist window to window? Plan.md §0.4a, §13.1. FINDING-004.

Reads covariance only, never a return, so it costs no trial.

    python scripts/check_breadth_persistence.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from run_prereg_003 import LOOKBACK_DAYS, pair_spreads, panel, simulate
from scipy import stats

WINDOW = 30
SENSITIVITY = (15, 60)


def breadth(matrix: np.ndarray) -> float:
    """Effective independent bets: mean per-pair variance / portfolio variance (§0.4a).

    Equals N / (1 + (N-1) rho) under equal weights and common volatility.
    """
    live = matrix[~np.all(np.isnan(matrix), axis=1)]
    if live.shape[0] < 2 or live.shape[1] < 3:
        return float("nan")
    filled = np.nan_to_num(live)
    per_pair = float(np.mean(np.var(filled, axis=1, ddof=1)))
    portfolio = float(np.var(filled.mean(axis=0), ddof=1))
    if portfolio <= 0 or per_pair <= 0:
        return float("nan")
    return per_pair / portfolio


def windows(matrix: np.ndarray, size: int) -> list[float]:
    """Breadth over non-overlapping windows, so consecutive values share no days."""
    out = []
    for start in range(0, matrix.shape[1] - size + 1, size):
        out.append(breadth(matrix[:, start:start + size]))
    return out


def report(values: list[float], size: int) -> None:
    clean = [v for v in values if np.isfinite(v)]
    if len(clean) < 4:
        print(f"  window {size:>3}d: only {len(clean)} usable windows")
        return
    current, following = np.array(clean[:-1]), np.array(clean[1:])
    pearson = float(np.corrcoef(current, following)[0, 1])
    spearman = float(stats.spearmanr(current, following).statistic)

    median = float(np.median(clean))
    high_now = current > median
    hits = int(np.sum(following[high_now] > median))
    tries = int(np.sum(high_now))

    print(f"  window {size:>3}d: {len(clean)} windows, breadth "
          f"{min(clean):.1f} to {max(clean):.1f}, median {median:.1f}")
    print(f"             persistence  pearson {pearson:+.3f}  "
          f"spearman {spearman:+.3f}")
    if tries:
        print(f"             above median now -> above median next: "
              f"{hits}/{tries} = {hits / tries:.0%}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    rows = panel(args.root)
    pairs = pair_spreads(rows)
    all_days = sorted({d for f in pairs.values() for d in f["day"].to_list()})
    index = {day: i for i, day in enumerate(all_days)}

    columns = []
    for frame in pairs.values():
        aligned = frame.sort("day")
        daily, _ = simulate(aligned["spread"].to_numpy(), aligned["basis"].to_numpy())
        daily = daily / 2.0
        positions = np.array([index[d] for d in aligned["day"].to_list()])
        live = np.arange(daily.size) >= LOOKBACK_DAYS
        column = np.full(len(all_days), np.nan)
        column[positions[live]] = daily[live]
        columns.append(column)

    matrix = np.array(columns)
    traded = ~np.all(np.isnan(matrix), axis=0)
    matrix = matrix[:, traded]
    print(f"pairs {matrix.shape[0]}, live days {matrix.shape[1]}")
    print(f"breadth over the whole window: {breadth(matrix):.1f}\n")

    print("Does breadth in one window predict breadth in the next?")
    report(windows(matrix, WINDOW), WINDOW)
    print("\ndeclared sensitivity, reported and not deciding:")
    for size in SENSITIVITY:
        report(windows(matrix, size), size)

    print("\nNo return was read. This measures the covariance structure alone, "
          "so it costs no trial and it gates the one that would.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
