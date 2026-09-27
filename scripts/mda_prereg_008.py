"""Feature importance by MDA, per traded month, for PREREG-008. A diagnostic.

The registration requires it (§6.1) and the first run did not produce it
(RESULT-008, owed). It changes no decision and enters no verdict: the monthly
decisions are reproduced exactly as `run_prereg_008.py` makes them, with the
same queue thinning and the same seeds, and each frozen model is then read on
the month it traded.

**Mean decrease in accuracy, out of sample.** For each month, the log loss of
the frozen model's calibrated probabilities against that month's labels, and
then again with one feature's column shuffled. The increase is that feature's
importance in that month. A feature the model relies on costs log loss when
shuffled; one it ignores costs nothing.

**Stability.** The Spearman correlation of the importance vector between
consecutive months, and the top feature of each. A model whose top feature
changes every month is fitting what the month happened to contain.

    python scripts/mda_prereg_008.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl
from run_prereg_008 import (
    breakeven,
    matrix,
    month_bounds,
    procedure,
)
from scipy.stats import spearmanr
from sklearn.metrics import log_loss

from tradelab.strategy.flash_strike import FEATURES

SEED = 20260927


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet/intraday"))
    args = parser.parse_args()

    haircut = json.loads((args.root / "_queue_calibration.json").read_text())["confirmed_share"]
    events = pl.read_parquet(args.root / "_candidates.parquet").filter(
        pl.col("kind") == "event").sort("fill_ms")
    # The same thinning as the run, same seed, so the decisions are the run's.
    events = events.filter(pl.Series(np.random.default_rng(8).random(events.height) < haircut))
    decisions = procedure(events)

    rng = np.random.default_rng(SEED)
    table = []
    for d in decisions:
        lo, hi = month_bounds(d.month)
        month = events.filter((pl.col("fill_ms") >= lo) & (pl.col("fill_ms") < hi))
        if month.height < 50 or d.fitted.model is None:
            continue
        x, y = matrix(month), (month[f"reason_{d.config}"].to_numpy() == 1).astype(int)
        if len(set(y)) < 2:
            continue
        base = log_loss(y, np.clip(d.fitted.predict(x), 1e-6, 1 - 1e-6), labels=[0, 1])
        row = {"month": f"{d.month:%Y-%m}", "config": d.config, "n": month.height,
               "taken": int((d.fitted.predict(x) > breakeven(month, d.config)).sum())}
        for j, name in enumerate(FEATURES):
            shuffled = x.copy()
            shuffled[:, j] = rng.permutation(shuffled[:, j])
            loss = log_loss(y, np.clip(d.fitted.predict(shuffled), 1e-6, 1 - 1e-6),
                            labels=[0, 1])
            row[name] = loss - base
        table.append(row)

    frame = pl.DataFrame(table)
    imp = frame.select(FEATURES).to_numpy()
    rhos = [spearmanr(imp[i], imp[i + 1]).statistic for i in range(len(imp) - 1)]
    mean = dict(zip(FEATURES, imp.mean(axis=0), strict=True))
    top = [FEATURES[int(np.argmax(r))] for r in imp]

    print("mean MDA over the traded months (log-loss increase when shuffled):")
    for name, value in sorted(mean.items(), key=lambda kv: -kv[1]):
        share = sum(t == name for t in top)
        print(f"  {name:20s} {value:+.5f}   top feature in {share}/{len(top)} months")
    print(f"\nstability: Spearman between consecutive months, median {np.median(rhos):+.2f}, "
          f"min {min(rhos):+.2f}, max {max(rhos):+.2f}")
    print(f"top feature by month: {top}")
    frame.write_parquet(args.root / "_mda_prereg_008.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
