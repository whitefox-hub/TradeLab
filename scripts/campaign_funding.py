"""Phase 4b, hypothesis 1: funding extremes as crowded positioning.

Pooled (§6.3): trials = the 9-config grid, not grid x universe (§8.3). Select on IS,
report OOS; never select on OOS.

    python scripts/campaign_funding.py [asset_limit]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from tradelab.orchestration.pooled import funding_universe, run_pooled
from tradelab.validation.deflated import deflated_sharpe, required_sharpe, sharpe_stats

ROOT = Path("/data/parquet")
OUT = ROOT / "_campaign"
PERIODS = (21, 45, 90)
ENTRIES = (1.0, 1.5, 2.0)
ANNUAL = float(np.sqrt(2_190))       # 4h bars


def rows_for(result) -> list[dict]:
    out = []
    for config in result.configs:
        stats = sharpe_stats(config.pooled_returns, effective_n=config.effective_n)
        out.append({
            **config.params,
            "is_sharpe": config.is_sharpe * ANNUAL,
            "oos_sharpe": stats.sharpe * ANNUAL,
            "bar": required_sharpe(result.trials, stats.n_obs) * ANNUAL,
            "dsr": deflated_sharpe(stats, result.trials),
            "breadth": config.breadth,
            "assets": len(config.per_asset_sharpe),
            "trades": config.total_trades,
            "effective_n": stats.n_obs,
        })
    # Ordered by what selection may use, never by the reported figure.
    return sorted(out, key=lambda r: -r["is_sharpe"])


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    universe = funding_universe(ROOT, limit=limit or None)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"universe {len(universe)} assets, grid {len(PERIODS) * len(ENTRIES)} configs",
          flush=True)

    summary = []
    for direction, label in ((1, "long"), (-1, "short")):
        started = time.time()
        result = run_pooled(ROOT, universe, "funding_z", "period", PERIODS, ENTRIES,
                            timeframe="4h", extra_params={}, direction=direction)
        rows = rows_for(result)
        summary.append({"direction": label, "trials": result.trials,
                        "usable_assets": len(result.assets), "configs": rows})
        (OUT / f"funding_z_{label}.json").write_text(json.dumps(summary[-1], indent=2))

        print(f"\n{label}: {len(result.assets)} usable assets, "
              f"{time.time() - started:.0f}s", flush=True)
        print(f"{'period':>7}{'entry':>7}{'IS':>8}{'OOS':>8}{'bar':>7}"
              f"{'DSR':>7}{'breadth':>9}{'n_eff':>8}", flush=True)
        for row in rows:
            mark = "  <-- clears" if row["oos_sharpe"] > row["bar"] else ""
            print(f"{row['period']:>7}{row['entry']:>7.1f}{row['is_sharpe']:>8.2f}"
                  f"{row['oos_sharpe']:>8.2f}{row['bar']:>7.2f}{row['dsr']:>7.3f}"
                  f"{row['breadth']:>8.0%}{row['effective_n']:>8,}{mark}", flush=True)

        if rows:
            chosen = rows[0]
            verdict = ("clears the bar" if chosen["oos_sharpe"] > chosen["bar"]
                       else "below the bar")
            print(f"  selected on IS: period {chosen['period']} entry "
                  f"{chosen['entry']:.1f} -> OOS {chosen['oos_sharpe']:.2f} "
                  f"vs bar {chosen['bar']:.2f}  [{verdict}]", flush=True)

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwritten to {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
