"""Evaluate PREREG-004: vol-target sizing of the PREREG-003 portfolio. One trial, no knobs.

Constants fixed by research/PREREG-004-breadth-sizing.md; do not parameterise. The
unsized portfolio is imported from run_prereg_003 so the two cannot drift.

    python scripts/run_prereg_004.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from run_prereg_003 import LOOKBACK_DAYS, pair_spreads, panel, simulate

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.risk.limits import RiskLimits
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.gates import evaluate
from tradelab.validation.power import assess

TRIALS = 2
VOL_WINDOW = 90
CALIBRATION_DAYS = 90
LEVERAGE_CAP = float(RiskLimits().max_gross_exposure)
RESIZE_LEGS = 4
YEAR = 365.25


def unsized_portfolio(root: Path) -> tuple[np.ndarray, list[str], np.ndarray, int]:
    """The PREREG-003 series, net of sign-flip costs, on its union calendar."""
    rows = panel(root)
    pairs = pair_spreads(rows)
    if not pairs:
        raise SystemExit("no usable pairs")
    all_days = sorted({d for f in pairs.values() for d in f["day"].to_list()})
    index = {day: i for i, day in enumerate(all_days)}

    contribution = np.zeros(len(all_days))
    present = np.zeros(len(all_days))
    for frame in pairs.values():
        aligned = frame.sort("day")
        daily, _ = simulate(aligned["spread"].to_numpy(), aligned["basis"].to_numpy())
        daily = daily / 2.0
        positions = np.array([index[d] for d in aligned["day"].to_list()])
        live = np.arange(daily.size) >= LOOKBACK_DAYS
        contribution[positions[live]] += daily[live]
        present[positions[live]] += 1

    traded = present > 0
    portfolio = contribution[traded] / present[traded]
    days = [d for d, keep in zip(all_days, traded, strict=True) if keep]
    return portfolio, days, present[traded], len(pairs)


def trailing_vol(series: np.ndarray, window: int) -> np.ndarray:
    """Std over the `window` days ending at t-1; including t would leak (probed in main)."""
    out = np.full(series.size, np.nan)
    for t in range(window, series.size):
        out[t] = series[t - window:t].std(ddof=1)
    return out


def size(portfolio: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (sized net series, leverage path, evaluation mask)."""
    vol = trailing_vol(portfolio, VOL_WINDOW)

    sigma_target = portfolio[:CALIBRATION_DAYS].std(ddof=1)

    leverage = np.zeros(portfolio.size)
    usable = ~np.isnan(vol) & (vol > 0)
    leverage[usable] = np.clip(sigma_target / vol[usable], 0.0, LEVERAGE_CAP)

    delta = np.zeros(portfolio.size)
    delta[1:] = np.abs(np.diff(leverage))
    delta[0] = leverage[0]
    sized = leverage * portfolio - delta * RESIZE_LEGS * float(BINANCE_RETAIL.per_side)

    evaluated = np.zeros(portfolio.size, dtype=bool)
    evaluated[max(VOL_WINDOW, CALIBRATION_DAYS):] = True
    return sized, leverage, evaluated


def quarters(days: list[str], values: np.ndarray) -> dict[str, np.ndarray]:
    out: dict[str, list[float]] = {}
    for day, value in zip(days, values, strict=True):
        text = day if isinstance(day, str) else day.isoformat()
        year, month = int(text[:4]), int(text[5:7])
        out.setdefault(f"{year}Q{(month - 1) // 3 + 1}", []).append(float(value))
    return {k: np.array(v) for k, v in out.items()}


def report(label: str, live: np.ndarray) -> float:
    stats = sharpe_stats(live, effective_n=float(len(live)))
    annualised = stats.sharpe * np.sqrt(YEAR)
    print(f"{label:24s} n {len(live):4d}  mean {live.mean():+.6%}  "
          f"vol {live.std(ddof=1):.6%}  SHARPE {annualised:+.3f}")
    return annualised


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    portfolio, days, present, n_pairs = unsized_portfolio(args.root)
    print(f"usable asset-venue pairs : {n_pairs}")
    print(f"live days                : {len(portfolio)}")

    sized, leverage, evaluated = size(portfolio)

    probe = portfolio.copy()
    probe[VOL_WINDOW + 5] += 0.05
    moved = int(np.sum(size(probe)[1][:VOL_WINDOW + 6] != leverage[:VOL_WINDOW + 6]))
    print(f"lookahead probe          : {moved} leverage values moved (must be 0)")
    if moved:
        print("LEAK: the sizing window sees the day it sizes")
        return 1

    eval_days = [d for d, keep in zip(days, evaluated, strict=True) if keep]
    live_sized, live_unsized = sized[evaluated], portfolio[evaluated]
    live_lev = leverage[evaluated]
    years = len(live_sized) / YEAR

    print(f"\nevaluated window         : {eval_days[0]} .. {eval_days[-1]} "
          f"({len(eval_days)} days, {years:.2f} years)")
    print(f"leverage median          : {np.median(live_lev):.3f}")
    print(f"leverage at the cap      : {np.mean(live_lev >= LEVERAGE_CAP):.1%} of days")
    print(f"leverage min/max         : {live_lev.min():.3f} / {live_lev.max():.3f}")

    gross = (live_lev * live_unsized).sum()
    cost = gross - live_sized.sum()
    print(f"resize cost / gross      : {cost / gross:+.1%}" if gross else "gross zero")

    print()
    unsized_sharpe = report("unsized (PREREG-003)", live_unsized)
    sized_sharpe = report("sized (PREREG-004)", live_sized)

    print("\nby quarter, sized vs unsized (trimmed drops the most extreme 1%)")
    qs, qu = quarters(eval_days, live_sized), quarters(eval_days, live_unsized)
    ql = quarters(eval_days, live_lev)
    qp = quarters(eval_days, present[evaluated])
    print(f"{'':8s} {'pairs':>6s} {'lev':>6s} {'unsized':>10s} {'sized':>10s} "
          f"{'unsz trim':>10s} {'sz trim':>10s}")
    for q in sorted(qs):
        s, u = qs[q], qu[q]
        cut = max(1, round(0.01 * len(s)))

        def trim_sharpe(x: np.ndarray, cut: int = cut) -> float:
            kept = np.delete(x, np.argsort(-np.abs(x))[:cut])
            sd = kept.std(ddof=1)
            return float(kept.mean() / sd * np.sqrt(YEAR)) if sd else float("nan")

        print(f"{q:8s} {np.median(qp[q]):6.0f} {np.median(ql[q]):6.2f} "
              f"{u.mean() / u.std(ddof=1) * np.sqrt(YEAR):+10.2f} "
              f"{s.mean() / s.std(ddof=1) * np.sqrt(YEAR):+10.2f} "
              f"{trim_sharpe(u):+10.2f} {trim_sharpe(s):+10.2f}")

    power = assess(target_sharpe_annual=0.5, trials=TRIALS,
                   effective_n_available=float(len(live_sized)), bars_per_year=YEAR)
    print(f"\nresolvable at {TRIALS} trials  : {power.resolvable_sharpe_annual:.3f}")
    print(f"sized clears it          : {sized_sharpe > power.resolvable_sharpe_annual}")
    print(f"sizing changed Sharpe by : {sized_sharpe - unsized_sharpe:+.3f}")

    stats = sharpe_stats(live_sized, effective_n=float(len(live_sized)))
    gate = evaluate(stats, trials=TRIALS, n_oos_trades=len(live_sized),
                    bars_per_year=YEAR, min_detectable_sharpe=0.5)
    for key, value in gate.metrics.items():
        print(f"  {key:26s} {value}")
    print(f"\nVERDICT: {gate.verdict.upper()}")
    for reason in gate.reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
