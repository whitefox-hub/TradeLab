#!/usr/bin/env python
"""Build the survivorship-neutral Binance panel (delisted included). Plan.md §4.4, RESULT-007.

    python scripts/build_binance_pit_panel.py              # everything, resumable
    python scripts/build_binance_pit_panel.py --only RAVEUSDT ALPACAUSDT

Resumes by skipping written assets and those logged as never traded. Funding is paced
under the venue's shared rate limit (~30 min full run).
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from tradelab.collectors.binance.pit_panel import collect, universe

START = datetime(2024, 9, 25, tzinfo=UTC)
END = datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC)


def set_window(start: date | None, end: date | None) -> None:
    global START, END
    if start:
        START = datetime(start.year, start.month, start.day, tzinfo=UTC)
    if end:
        END = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/parquet/binance_pit"))
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="window start, UTC (default: the 2024-09 window)")
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    set_window(args.start, args.end)
    args.out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if today <= END:
        raise SystemExit(f"END {END:%Y-%m-%d} is not a completed UTC day yet")

    client = httpx.Client(timeout=60.0)
    contracts = universe(client)
    if args.only:
        contracts = [c for c in contracts if c.symbol in set(args.only)]
    pl.DataFrame([{"symbol": c.symbol, "asset": c.asset, "status": c.status,
                   "delisted_at": c.delisted_at} for c in contracts],
                 schema={"symbol": pl.Utf8, "asset": pl.Utf8, "status": pl.Utf8,
                         "delisted_at": pl.Datetime("us", "UTC")}
                 ).write_parquet(args.out / "_universe.parquet")

    empty_log = args.out / "_never_traded.txt"
    never = set(empty_log.read_text().split()) if empty_log.exists() else set()
    print(f"universe: {len(contracts)} contracts "
          f"({sum(c.status == 'SETTLING' for c in contracts)} settling, "
          f"{sum(c.status == 'ARCHIVED' for c in contracts)} archive-only)", flush=True)

    written = skipped = 0
    for index, contract in enumerate(contracts, start=1):
        target = args.out / f"{contract.asset}.parquet"
        if target.exists() or contract.symbol in never:
            skipped += 1
            continue
        frame = collect(client, contract, START, END)
        if frame is None:
            never.add(contract.symbol)
            empty_log.write_text("\n".join(sorted(never)) + "\n")
        else:
            frame.write_parquet(target)
            written += 1
        if index % 25 == 0 or (frame is not None and len(contracts) < 25):
            print(f"  {index}/{len(contracts)} written={written} skipped={skipped} "
                  f"never_traded={len(never)}", flush=True)
    print(f"done: written {written}, resumed {skipped}, never traded {len(never)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
