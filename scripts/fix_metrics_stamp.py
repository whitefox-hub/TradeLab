#!/usr/bin/env python
"""Move every downloaded metrics snapshot to the moment it became available.

The archive stamps a snapshot five minutes before the reading it describes
(`intraday.METRICS_STAMP_LAG_MS`, measured 2026-09-27). Files built before the
parser knew that are shifted here once rather than downloaded again, and then
cut at delisting and at the window's end exactly as the builder cuts them. A
marker file makes a second run a no-op, because shifting twice is a leak the
other way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from tradelab.collectors.binance.intraday import METRICS_STAMP_LAG_MS, cut

ROOT = Path("data/parquet/intraday/metrics")
MARKER = ROOT / "_stamp_lag_applied"
START = datetime(2024, 9, 25, tzinfo=UTC)
END = datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC)


def main() -> int:
    if MARKER.exists():
        print(f"already applied: {MARKER.read_text().strip()}")
        return 0
    delisted = dict(pl.read_parquet("data/parquet/binance_pit/_universe.parquet")
                    .select("asset", "delisted_at").rows())
    files = sorted(ROOT.glob("*/*.parquet"))
    for path in files:
        frame = pl.read_parquet(path)
        if not frame.is_empty():
            frame = cut(frame.with_columns(pl.col("available_ms") + METRICS_STAMP_LAG_MS),
                        START, END, delisted.get(path.parent.name))
        partial = path.with_suffix(".partial")
        frame.write_parquet(partial)
        partial.replace(path)
    MARKER.write_text(f"{len(files)} files shifted +{METRICS_STAMP_LAG_MS} ms, "
                      f"{datetime.now(UTC).isoformat()}\n")
    print(MARKER.read_text().strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
