"""Evaluate PREREG-001 exactly as registered. One trial, whole sample.

Nothing is tunable: every choice comes from research/PREREG-001-listing-reversal.md.

    python scripts/run_prereg_001.py [--events data/parquet/listing_events.parquet]
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

TRIALS = 1  # The registration. Changing it is a new study, not a new run.


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path,
                        default=Path("data/parquet/listing_events.parquet"))
    args = parser.parse_args()

    events = pl.read_parquet(args.events).sort("announced_at")
    n = events.height

    entry = events["entry_close"].to_numpy()
    exit_ = events["exit_close"].to_numpy()
    funding = events["funding_received"].to_numpy()

    # Short: gains when price falls and receives positive funding (registered mechanism).
    gross = -(exit_ / entry - 1.0)
    cost = np.full(n, BINANCE_RETAIL.round_trip)
    net = gross + funding - cost

    years = (events["announced_at"].max() - events["announced_at"].min()).days / 365.25
    per_year = n / years

    stats = sharpe_stats(net, effective_n=float(n))
    annualised = stats.sharpe * np.sqrt(per_year)
    power = assess(target_sharpe_annual=annualised if annualised > 0 else 0.59,
                   trials=TRIALS, effective_n_available=float(n), bars_per_year=per_year)

    print(f"events                 : {n} over {years:.1f} years ({per_year:.0f}/yr)")
    print(f"gross mean per trade   : {gross.mean():+.4%}")
    print(f"funding mean per trade : {funding.mean():+.4%}")
    print(f"cost per round trip    : {BINANCE_RETAIL.round_trip:.4%}")
    print(f"NET mean per trade     : {net.mean():+.4%}   median {np.median(net):+.4%}")
    print(f"net std                : {net.std(ddof=1):.4%}")
    print(f"win rate               : {(net > 0).mean():.1%}")
    print(f"SHARPE (annualised)    : {annualised:+.3f}")
    print(f"resolvable at 1 trial  : {power.resolvable_sharpe_annual:.3f}")

    gate = evaluate(stats, trials=TRIALS, n_oos_trades=n, bars_per_year=per_year,
                    min_detectable_sharpe=0.59)
    print(f"\nVERDICT: {gate.verdict.upper()}")
    for reason in gate.reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
