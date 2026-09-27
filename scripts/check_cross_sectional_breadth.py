"""How many independent bets does a cross-sectional crypto book have? §0.4a.

RESULT-006 closed cross-venue funding dispersion and said what to do next:
choose the following hypothesis against the breadth constraint **before** its
story. §0.4a fixes the detection threshold by calendar span, and breadth — bets
held at the same time that are genuinely uncorrelated — is the only fast lever.

This measures the lever, for the whole class of cross-sectional strategies at
once. It asks nothing about returns and tests no rule, so it costs no trial.

**The question.** Crypto is widely held to be one factor: everything follows
Bitcoin. If that is true after the factor is removed, a hundred-asset book is
one bet and no cross-sectional hypothesis can clear the threshold at any
plausible per-bet Sharpe. If the residuals are genuinely independent, breadth is
close to the asset count and the arithmetic changes completely.

The answer is a property of the panel, not of any strategy, which is exactly why
it is worth knowing before one is written.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import polars as pl

VENUE = "binance"
#: An asset needs enough days for a covariance to mean anything.
MIN_DAYS = 200


def returns_matrix(root: Path) -> tuple[np.ndarray, list[str], list]:
    rows = pl.concat([pl.read_parquet(f) for f in sorted(root.glob("*.parquet"))])
    rows = rows.filter(pl.col("venue") == VENUE).drop_nulls("close")
    wide = rows.pivot(index="day", on="asset", values="close").sort("day")

    days = wide["day"].to_list()
    assets, columns = [], []
    for asset in wide.columns[1:]:
        prices = wide[asset].to_numpy().astype(np.float64)
        if np.count_nonzero(~np.isnan(prices)) < MIN_DAYS:
            continue
        series = np.full(prices.size, np.nan)
        series[1:] = np.diff(prices) / prices[:-1]
        assets.append(asset)
        columns.append(series)
    return np.array(columns), assets, days


def breadth(matrix: np.ndarray, neutral: bool = False,
            draws: int = 400, seed: int = 20260926) -> float:
    """Effective independent bets: mean per-asset variance over portfolio
    variance, which for equal weights is N / (1 + (N-1)·rho).

    `neutral` picks the portfolio the measure is taken over, and it is not a
    detail. A cross-sectional book is **dollar-neutral** — long some names,
    short others — so its weights sum to zero, not to one.

    Measuring residuals with equal *positive* weights gives nonsense, and the
    first version of this script did: the market factor here is the
    equal-weighted mean, so the equal-weighted residual portfolio is zero by
    construction, the denominator collapses and breadth came back as 3.2e32.
    The same shape as differencing BTCUSDT against itself and reading four rows
    of zeroes as "nothing happened".

    So the neutral case averages over random +/-1/N weight draws. Random and not
    a signal's weights, because a signal would make this a test of the signal.
    """
    filled = np.nan_to_num(matrix)
    per_asset = float(np.mean(np.var(filled, axis=1, ddof=1)))
    count = filled.shape[0]

    if not neutral:
        portfolio = float(np.var(filled.mean(axis=0), ddof=1))
        return per_asset / portfolio if portfolio > 0 else float("nan")

    rng = np.random.default_rng(seed)
    variances = []
    for _ in range(draws):
        signs = np.ones(count)
        signs[rng.permutation(count)[: count // 2]] = -1.0
        weights = signs / count
        variances.append(float(np.var(weights @ filled, ddof=1)))
    portfolio = float(np.mean(variances))
    return per_asset / portfolio if portfolio > 0 else float("nan")


def dimensions(matrix: np.ndarray) -> float:
    """Eigenvalue participation ratio: how many directions the correlation
    matrix really has. Reported beside the variance ratio because the two
    answer different questions, which FEASIBILITY-006 had to correct once."""
    corr = np.corrcoef(np.nan_to_num(matrix))
    eig = np.linalg.eigvalsh(np.nan_to_num(corr))
    return float(eig.sum() ** 2 / (eig**2).sum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    matrix, assets, _days = returns_matrix(args.root)
    # Days every surviving asset has, so the covariance is of one sample rather
    # than of a different subset per pair.
    complete = ~np.any(np.isnan(matrix), axis=0)
    matrix = matrix[:, complete]
    print(f"assets {len(assets)}, days {matrix.shape[1]} "
          f"({matrix.shape[1] / 365.25:.2f} years)\n")

    raw = breadth(matrix)
    print(f"{'':36} {'variance ratio':>16} {'eigen dimensions':>18}")
    print(f"  raw, equal-weight long-only        {raw:>16.1f} "
          f"{dimensions(matrix):>18.1f}")

    # The market factor: the equal-weighted mean return. Removed by regressing
    # each asset on it and keeping the residual, which is what a
    # dollar-neutral cross-sectional book actually holds.
    market = matrix.mean(axis=0)
    variance = float(np.var(market, ddof=1))
    betas = np.array([float(np.cov(row, market, ddof=1)[0, 1] / variance)
                      for row in matrix])
    residual = matrix - betas[:, None] * market[None, :]
    neutral = breadth(residual, neutral=True)
    print(f"  residual, dollar-neutral           {neutral:>16.1f} "
          f"{dimensions(residual):>18.1f}")
    print(f"  (of {len(assets)} assets)")
    print(f"\n  beta: median {np.median(betas):.2f}, "
          f"{np.mean((betas > 0.7) & (betas < 1.3)):.0%} between 0.7 and 1.3")

    corr = np.corrcoef(np.nan_to_num(matrix))
    off = corr[~np.eye(len(corr), dtype=bool)]
    rcorr = np.corrcoef(np.nan_to_num(residual))
    roff = rcorr[~np.eye(len(rcorr), dtype=bool)]
    print(f"\nmean pairwise correlation   raw {np.nanmean(off):+.3f}  "
          f"residual {np.nanmean(roff):+.3f}")

    eig = np.linalg.eigvalsh(np.nan_to_num(corr))
    frac = np.sort(eig)[::-1] / eig.sum()
    print(f"first factor explains {frac[0]:.0%} of the raw variance, "
          f"first five {frac[:5].sum():.0%}")

    # What it buys. A per-bet Sharpe of 0.2 with N independent bets held at once
    # gives 0.2*sqrt(N) for the portfolio, which is the number that must clear
    # the threshold §0.4a fixes from the calendar.
    print(f"\n{'per-bet Sharpe':>15} {'long-only book':>16} {'neutral book':>16}")
    for per_bet in (0.05, 0.1, 0.2, 0.3):
        print(f"{per_bet:>15.2f} {per_bet * np.sqrt(raw):>16.2f} "
              f"{per_bet * np.sqrt(neutral):>16.2f}")

    # The estimate's honesty depends on having more days than assets. A
    # 229x229 covariance from 207 observations is rank-deficient, and a
    # residual correlation near zero can be the deficiency rather than the
    # market. FINDING-004 is this project's record of reporting a breadth
    # figure that turned out to be one.
    print(f"\n{'assets':>8} {'days':>7} {'T/N':>6} {'raw':>8} {'neutral':>9}"
          f" {'resid rho':>10}")
    full, _names, _ = returns_matrix(args.root)
    for keep in (20, 40, 60, 100, 150, 229):
        coverage = np.argsort(-np.sum(~np.isnan(full), axis=1))[:keep]
        block = full[coverage]
        usable = block[:, ~np.any(np.isnan(block), axis=0)]
        if usable.shape[1] < keep + 10:
            print(f"{keep:>8} {usable.shape[1]:>7} "
                  f"{usable.shape[1]/keep:>6.2f} {'-':>8} {'-':>9} "
                  f"{'T < N, not estimable':>10}")
            continue
        mkt = usable.mean(axis=0)
        var = float(np.var(mkt, ddof=1))
        b = np.array([float(np.cov(r, mkt, ddof=1)[0, 1] / var) for r in usable])
        res = usable - b[:, None] * mkt[None, :]
        rc = np.corrcoef(np.nan_to_num(res))
        rho = float(np.nanmean(rc[~np.eye(len(rc), dtype=bool)]))
        print(f"{keep:>8} {usable.shape[1]:>7} {usable.shape[1]/keep:>6.2f} "
              f"{breadth(usable):>8.1f} {breadth(res, neutral=True):>9.1f} "
              f"{rho:>+10.3f}")

    print("\nNo rule was tested and no return hypothesis was read. This is a "
          "property of the panel, and it costs no trial.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
