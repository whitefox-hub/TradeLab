"""Phase 4b, hypothesis 2: funding z ranked across the universe.

Long-short and long-only both run; long-only mostly measures market beta.

    python scripts/campaign_cross_sectional.py [asset_limit]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.engines.tier_a.cross_sectional import run_cross_sectional
from tradelab.features.materialise import materialise
from tradelab.orchestration.pooled import funding_universe
from tradelab.storage.materialise_bars import load_materialised
from tradelab.validation.deflated import deflated_sharpe, required_sharpe, sharpe_stats

ROOT = Path("/data/parquet")
ANNUAL = float(np.sqrt(2_190))
PERIODS = (21, 45, 90)
KS = (5, 10, 20)
REBALANCES = (1, 6)


def build_panel(assets: list[str], period: int):
    """Align close, signal and funding on one time index."""
    frames = []
    for asset in assets:
        bars = load_materialised(ROOT, asset, "4h")
        if bars is None or bars.height < 2_000:
            continue
        feats = materialise(bars, [("funding_z", {"period": period})],
                            keep=("event_time", "close"))
        frames.append(
            feats.rename({f"funding_z__period{period}": f"sig_{asset}",
                          "close": f"close_{asset}"})
            .join(bars.select("event_time", pl.col("funding_rate").alias(f"fund_{asset}")),
                  on="event_time", how="left")
        )
    if not frames:
        return None
    panel = frames[0]
    for frame in frames[1:]:
        panel = panel.join(frame, on="event_time", how="outer_coalesce")
    panel = panel.sort("event_time")
    names = [c[6:] for c in panel.columns if c.startswith("close_")]
    return (
        panel.select([f"close_{a}" for a in names]).to_numpy().astype(np.float64),
        panel.select([f"sig_{a}" for a in names]).to_numpy().astype(np.float64),
        np.nan_to_num(panel.select([f"fund_{a}" for a in names]).to_numpy().astype(np.float64)),
        names,
    )


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    assets = funding_universe(ROOT, limit=limit)
    trials = len(PERIODS) * len(KS) * len(REBALANCES) * 2
    print(f"{len(assets)} assets, {trials} configurations declared", flush=True)

    rows = []
    for period in PERIODS:
        panel = build_panel(assets, period)
        if panel is None:
            continue
        closes, signals, funding, names = panel
        split = int(len(closes) * 0.7)
        print(f"\nperiod {period}: {len(names)} assets, {len(closes):,} bars", flush=True)
        print(f"{'k':>4}{'reb':>5}{'side':>7}{'IS':>8}{'OOS':>8}{'bar':>7}"
              f"{'DSR':>7}{'names':>7}", flush=True)

        for k in KS:
            for rebalance in REBALANCES:
                for long_short, side in ((True, "LS"), (False, "LO")):
                    kw = dict(k=k, costs=BINANCE_RETAIL, long_short=long_short,
                              rebalance=rebalance)
                    is_r = run_cross_sectional(closes[:split], signals[:split],
                                               funding=funding[:split], **kw)
                    oos = run_cross_sectional(closes[split:], signals[split:],
                                              funding=funding[split:], **kw)
                    stats = sharpe_stats(oos.returns, effective_n=oos.effective_n)
                    bar = required_sharpe(trials, stats.n_obs) * ANNUAL
                    row = {"period": period, "k": k, "rebalance": rebalance, "side": side,
                           "is_sharpe": is_r.sharpe * ANNUAL,
                           "oos_sharpe": stats.sharpe * ANNUAL, "bar": bar,
                           "dsr": deflated_sharpe(stats, trials),
                           "names": oos.avg_names, "effective_n": stats.n_obs}
                    rows.append(row)
                    mark = "  <-- clears" if row["oos_sharpe"] > bar else ""
                    print(f"{k:>4}{rebalance:>5}{side:>7}{row['is_sharpe']:>8.2f}"
                          f"{row['oos_sharpe']:>8.2f}{bar:>7.2f}{row['dsr']:>7.3f}"
                          f"{row['names']:>7.1f}{mark}", flush=True)

    out = ROOT / "_campaign" / "cross_sectional.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    if rows:
        best = max(rows, key=lambda r: r["is_sharpe"])
        print(f"\nselected on IS: {best['side']} k={best['k']} reb={best['rebalance']} "
              f"period={best['period']} -> OOS {best['oos_sharpe']:.2f} "
              f"vs bar {best['bar']:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
