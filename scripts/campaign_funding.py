"""Phase 4b, hypothesis 1: funding extremes as crowded positioning.

Pooled rather than searched (§6.3): every configuration is evaluated on every
asset and scored on the pooled result, so the trial count is the grid and not the
grid times the universe. The asset count multiplies the sample instead of raising
the bar.

The grid is deliberately small. Three periods with a meaning — one week, two
weeks and one month of eight-hourly settlements — and three z levels. Nine
trials, declared before running, because §8.3's bar rises with every one spent.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from tradelab.orchestration.pooled import funding_universe, run_pooled, summarise
from tradelab.validation.deflated import deflated_sharpe, required_sharpe, sharpe_stats

ROOT = Path("/data/parquet")
OUT = ROOT / "_campaign"
PERIODS = (21, 45, 90)
ENTRIES = (1.0, 1.5, 2.0)
BARS_PER_YEAR = 2_190          # 4h


def report(name: str, result, direction: int) -> dict:
    rows = []
    for config in result.configs:
        stats = sharpe_stats(config.pooled_returns, effective_n=config.effective_n)
        bar = required_sharpe(result.trials, stats.n_obs)
        rows.append({
            **config.params,
            "sharpe_annual": stats.sharpe * float(np.sqrt(BARS_PER_YEAR)),
            "bar_annual": bar * float(np.sqrt(BARS_PER_YEAR)),
            "dsr": deflated_sharpe(stats, result.trials),
            "breadth": config.breadth,
            "assets": len(config.per_asset_sharpe),
            "trades": config.total_trades,
            "effective_n": stats.n_obs,
        })
    rows.sort(key=lambda r: -r["sharpe_annual"])
    return {"name": name, "direction": direction, "trials": result.trials,
            "universe": len(result.assets), "configs": rows}


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    universe = funding_universe(ROOT, limit=limit or None)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"universe {len(universe)} assets, grid {len(PERIODS) * len(ENTRIES)} configs",
          flush=True)

    summary = []
    for direction, label in ((1, "long"), (-1, "short")):
        started = time.time()
        result = run_pooled(
            ROOT, universe, "funding_z", "period", PERIODS, ENTRIES,
            timeframe="4h", extra_params={}, direction=direction,
        )
        payload = report(f"funding_z-{label}", result, direction)
        summary.append(payload)
        (OUT / f"funding_z_{label}.json").write_text(json.dumps(payload, indent=2))

        print(f"\n{label}: {len(result.assets)} usable assets, "
              f"{time.time() - started:.0f}s", flush=True)
        print(summarise(result).head(9), flush=True)
        for row in payload["configs"][:3]:
            verdict = "PASS" if row["sharpe_annual"] > row["bar_annual"] else "below bar"
            print(f"  period {row['period']:>3} entry {row['entry']:.1f}: "
                  f"SR {row['sharpe_annual']:>6.2f} vs bar {row['bar_annual']:>5.2f}  "
                  f"DSR {row['dsr']:.3f}  breadth {row['breadth']:.0%}  {verdict}",
                  flush=True)

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwritten to {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
