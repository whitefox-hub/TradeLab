"""Effective breadth of a cross-sectional crypto book, raw and market-residual. §0.4a.

Follows RESULT-006. Tests no rule, reads no return hypothesis, so costs no trial.

    python scripts/check_cross_sectional_breadth.py
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
    """Effective bets: mean per-asset variance / portfolio variance (N / (1 + (N-1)·rho)).

    Use `neutral=True` for residuals: the equal-weight residual portfolio is zero by
    construction (breadth blows up), so it averages random dollar-neutral +/-1/N draws.
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
    """Eigenvalue participation ratio; answers a different question than `breadth`
    (FEASIBILITY-006)."""
    corr = np.corrcoef(np.nan_to_num(matrix))
    eig = np.linalg.eigvalsh(np.nan_to_num(corr))
    return float(eig.sum() ** 2 / (eig**2).sum())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    matrix, assets, _days = returns_matrix(args.root)
    # Complete days only, so the covariance is of one sample.
    complete = ~np.any(np.isnan(matrix), axis=0)
    matrix = matrix[:, complete]
    print(f"assets {len(assets)}, days {matrix.shape[1]} "
          f"({matrix.shape[1] / 365.25:.2f} years)\n")

    raw = breadth(matrix)
    print(f"{'':36} {'variance ratio':>16} {'eigen dimensions':>18}")
    print(f"  raw, equal-weight long-only        {raw:>16.1f} "
          f"{dimensions(matrix):>18.1f}")

    # Market factor = equal-weighted mean; the residual is what a neutral book holds.
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

    # Portfolio Sharpe = per-bet Sharpe * sqrt(N_eff), to compare with the §0.4a threshold.
    print(f"\n{'per-bet Sharpe':>15} {'long-only book':>16} {'neutral book':>16}")
    for per_bet in (0.05, 0.1, 0.2, 0.3):
        print(f"{per_bet:>15.2f} {per_bet * np.sqrt(raw):>16.2f} "
              f"{per_bet * np.sqrt(neutral):>16.2f}")

    # Needs T > N: a rank-deficient covariance fakes near-zero residual correlation
    # (see FINDING-004).
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
