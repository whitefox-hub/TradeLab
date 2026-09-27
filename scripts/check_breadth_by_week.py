"""Does the neutral book's breadth survive week to week? §13.1, §0.4a.

FEASIBILITY-008 measured a dollar-neutral book at breadth ~150 of 150 over the
whole panel, and said plainly what was missing: **that is an average, and it may
span zero.** §13.1 says correlations converge toward one under stress, and
FINDING-004 is this project's record of what skipping that check costs — a
pooled breadth of 151 that ran 11 / 0 / 103 / 4 by quarter.

So this runs the same measurement period by period. No rule is tested and no
return hypothesis is read, so it costs no trial.

**Why realised variance and not a covariance.** Estimating a 100x100 covariance
needs far more days than assets, and a week is seven. But breadth is a ratio of
two variances — the average asset's against the portfolio's — and both are
estimable from a handful of days for a *fixed* portfolio. So the portfolios are
drawn once, held across every period, and their realised variance is measured
in each. Drawing fresh weights per period would measure the draw.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from check_cross_sectional_breadth import returns_matrix

#: Held across every period. A portfolio redrawn each week would make the
#: measurement about the draw rather than about the market.
DRAWS = 200
SEED = 20260926

#: Days of history the trailing beta is fitted on. One quarter, the window this
#: project has used since PREREG-003, rather than one chosen here.
BETA_WINDOW = 90


def _full_sample(block: np.ndarray, market: np.ndarray) -> np.ndarray:
    variance = float(np.var(market, ddof=1))
    betas = np.array([float(np.cov(r, market, ddof=1)[0, 1] / variance)
                      for r in block])
    return block - betas[:, None] * market[None, :]


def _trailing(block: np.ndarray, market: np.ndarray) -> np.ndarray:
    """Beta fitted on the days before, applied forward. Never inside its own
    window, so the hedge is one a strategy could actually have put on."""
    out = np.full(block.shape, np.nan)
    for t in range(BETA_WINDOW, block.shape[1]):
        past_market = market[t - BETA_WINDOW:t]
        variance = float(np.var(past_market, ddof=1))
        if variance <= 0:
            continue
        past = block[:, t - BETA_WINDOW:t]
        betas = np.array([float(np.cov(r, past_market, ddof=1)[0, 1] / variance)
                          for r in past])
        out[:, t] = block[:, t] - betas * market[t]
    return out


def fixed_portfolios(count: int, draws: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    weights = np.zeros((draws, count))
    for row in range(draws):
        signs = np.ones(count)
        signs[rng.permutation(count)[: count // 2]] = -1.0
        weights[row] = signs / count
    return weights


def breadth_over(block: np.ndarray, weights: np.ndarray) -> float:
    """Mean per-asset variance over mean neutral-portfolio variance."""
    if block.shape[1] < 4:
        return float("nan")
    per_asset = float(np.mean(np.var(block, axis=1, ddof=1)))
    portfolio = float(np.mean(np.var(weights @ block, axis=1, ddof=1)))
    return per_asset / portfolio if portfolio > 0 else float("nan")


def summarise(label: str, values: list[float], count: int) -> None:
    clean = np.array([v for v in values if np.isfinite(v)])
    if clean.size < 3:
        print(f"  {label:9} too few periods")
        return
    worst = float(clean.min())
    print(f"  {label:9} {clean.size:>4} periods   median {np.median(clean):>6.1f}   "
          f"min {worst:>6.1f}   max {clean.max():>6.1f}   "
          f"below half of {count}: {np.mean(clean < count / 2):.0%}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    parser.add_argument("--assets", type=int, default=100)
    args = parser.parse_args()

    full, _names, _days = returns_matrix(args.root)
    coverage = np.argsort(-np.sum(~np.isnan(full), axis=1))[: args.assets]
    block = full[coverage]
    block = block[:, ~np.any(np.isnan(block), axis=0)]
    count = block.shape[0]
    print(f"assets {count}, days {block.shape[1]} "
          f"({block.shape[1] / 365.25:.2f} years)")

    market = block.mean(axis=0)
    weights = fixed_portfolios(count, DRAWS, SEED)

    # Two ways of removing the factor, and the difference is the point.
    #
    # Full-sample beta is what FEASIBILITY-008 used, and it is a lookahead
    # inside every window: the beta applied in week three was fitted using week
    # ninety. It is kept as the upper bound, labelled.
    #
    # Trailing beta is what a strategy can actually have — estimated on the
    # BETA_WINDOW days before the period and applied forward, never refitted
    # inside the window it hedges. If breadth survives that, it is real.
    residuals = {"full-sample beta (lookahead)": _full_sample(block, market),
                 "trailing beta (causal)": _trailing(block, market)}

    for label, residual in residuals.items():
        usable = residual[:, ~np.all(np.isnan(residual), axis=0)]
        print(f"{label}")
        print(f"  whole sample: breadth "
              f"{breadth_over(np.nan_to_num(usable), weights):.1f} of {count}")
        for sub, size in (("weekly", 7), ("fortnightly", 14), ("monthly", 30)):
            values = [breadth_over(np.nan_to_num(usable[:, s:s + size]), weights)
                      for s in range(0, usable.shape[1] - size + 1, size)]
            summarise(sub, values, count)
        print()
    residual = np.nan_to_num(
        residuals["trailing beta (causal)"][:, BETA_WINDOW:])

    # The worst periods, named, because an average that spans them is the
    # figure FINDING-004 was wrong about.
    size = 30
    values = [(s, breadth_over(residual[:, s:s + size], weights))
              for s in range(0, residual.shape[1] - size + 1, size)]
    ordered = sorted((v for v in values if np.isfinite(v[1])), key=lambda p: p[1])
    print("\n  worst monthly periods (day index, breadth):")
    for start, value in ordered[:4]:
        window = residual[:, start:start + size]
        print(f"    day {start:>4}  breadth {value:>6.1f}  "
              f"asset vol {np.mean(np.std(window, axis=1, ddof=1)):.3%}")

    print("\nNo rule was tested and no return hypothesis was read; no trial spent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
