#!/usr/bin/env python
"""Build the survivorship-neutral Binance panel. Plan.md §4.4, RESULT-007.

Every USDT crypto perpetual that traded on Binance inside the window, delisted
ones included, with daily close and daily funding. The same window as the
five-venue panel it replaces, so the two can be compared asset by asset where
they overlap.

    python scripts/build_binance_pit_panel.py              # everything, resumable
    python scripts/build_binance_pit_panel.py --only RAVEUSDT ALPACAUSDT

Resumes by skipping assets already written, and records the ones that never
traded in the window, so a rerun does not ask about them again. Funding is
paced under the venue's shared limit, so the full run takes about half an hour.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

from tradelab.collectors.binance.pit_panel import collect, universe

START = datetime(2024, 9, 25, tzinfo=UTC)
END = datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/parquet/binance_pit"))
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    # The day still in progress has an intraday "close" and part of its
    # settlements. The five-venue panel was fetched on its own last day and
    # carries exactly that on 2026-09-25: ADA at 0.2558 against a true close of
    # 0.2586, RAVE with five of six settlements.
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
