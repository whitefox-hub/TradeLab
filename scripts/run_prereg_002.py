"""Evaluate PREREG-002: the PREREG-001 configuration, unchanged, on Binance and Bybit.

    python scripts/run_prereg_002.py
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

TRIALS = 1  # Replication on new events, not a search. See PREREG-002.
COLUMNS = ["asset", "listing_day_ms", "entry_close", "exit_close", "funding_received"]


def net_returns(frame: pl.DataFrame) -> np.ndarray:
    entry = frame["entry_close"].to_numpy()
    exit_ = frame["exit_close"].to_numpy()
    funding = frame["funding_received"].to_numpy()
    return -(exit_ / entry - 1.0) + funding - BINANCE_RETAIL.round_trip


def report(label: str, frame: pl.DataFrame) -> None:
    net = net_returns(frame)
    years = (frame["listing_day_ms"].max() - frame["listing_day_ms"].min()) / 86_400_000 / 365.25
    per_year = frame.height / years
    sharpe = sharpe_stats(net, effective_n=float(frame.height)).sharpe * np.sqrt(per_year)
    print(f"{label:22s} n={frame.height:4d}  net mean {net.mean():+7.3%}  "
          f"median {np.median(net):+7.3%}  win {(net > 0).mean():5.1%}  "
          f"Sharpe {sharpe:+6.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binance", type=Path,
                        default=Path("data/parquet/listing_events.parquet"))
    parser.add_argument("--bybit", type=Path,
                        default=Path("data/parquet/listing_events_bybit.parquet"))
    args = parser.parse_args()

    binance = pl.read_parquet(args.binance).select(COLUMNS)
    bybit = pl.read_parquet(args.bybit).select(COLUMNS)
    pooled = pl.concat([binance, bybit]).sort("listing_day_ms")

    # Per venue first: a pooled number can hide a sign flip between venues.
    report("binance", binance)
    report("bybit", bybit)
    print()
    report("POOLED (registered)", pooled)

    net = net_returns(pooled)
    n = pooled.height
    years = (pooled["listing_day_ms"].max() - pooled["listing_day_ms"].min()) / 86_400_000 / 365.25
    per_year = n / years
    stats = sharpe_stats(net, effective_n=float(n))
    annualised = stats.sharpe * np.sqrt(per_year)
    power = assess(target_sharpe_annual=0.59, trials=TRIALS,
                   effective_n_available=float(n), bars_per_year=per_year)

    print(f"\nspan {years:.1f} years, {per_year:.0f} events/yr")
    print(f"annualised Sharpe      : {annualised:+.3f}")
    print(f"resolvable at 1 trial  : {power.resolvable_sharpe_annual:.3f}")

    gate = evaluate(stats, trials=TRIALS, n_oos_trades=n, bars_per_year=per_year,
                    min_detectable_sharpe=0.59)
    print(f"\nVERDICT: {gate.verdict.upper()}")
    for reason in gate.reasons:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
