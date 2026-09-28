"""Event dataset for PREREG-001: price and funding around each new listing.

Listing day = first available kline, not the announcement time (which precedes launch);
it exists for delisted contracts too, keeping the set survivorship-neutral.

    python scripts/build_listing_events.py
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import polars as pl

from tradelab.collectors.binance.announcements import base_ticker, classify, extract_assets

EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
KLINES = "https://fapi.binance.com/fapi/v1/klines"
FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"

#: Bars fetched per listing: the listing day, the entry day, seven held days,
#: and slack so a missing bar is visible rather than silently shifting the exit.
WINDOW_DAYS = 14


def announced_listings(root: Path) -> pl.DataFrame:
    rows = pl.read_parquet(root / "announcements" / "venue=binance" / "asset=_all" / "**/*.parquet")
    rows = rows.with_columns(
        pl.col("title").map_elements(classify, return_dtype=pl.Utf8).alias("cls"),
        pl.col("title").map_elements(extract_assets, return_dtype=pl.List(pl.Utf8)).alias("ass"),
    )
    listings = (
        rows.filter(pl.col("cls") == "futures_listing")
        .explode("ass")
        .drop_nulls("ass")
        .with_columns(pl.col("ass").map_elements(base_ticker, return_dtype=pl.Utf8).alias("base"))
    )
    return listings.sort("event_time").group_by("base").first().sort("event_time")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet"))
    parser.add_argument("--out", type=Path, default=Path("data/parquet/listing_events.parquet"))
    args = parser.parse_args()

    listings = announced_listings(args.root)
    info = httpx.get(EXCHANGE_INFO, timeout=30).json()
    tradfi = {
        base_ticker(s["symbol"])
        for s in info["symbols"]
        if s.get("contractType") == "TRADIFI_PERPETUAL"
    }

    rows: list[dict] = []
    dropped = {"tokenised_equity": 0, "no_klines": 0, "short_history": 0}
    targets = [r for r in listings.iter_rows(named=True) if r["base"] not in tradfi]
    dropped["tokenised_equity"] = listings.height - len(targets)

    def one(record: dict) -> dict:
        """Fetch one listing's window, or record why it cannot be used."""
        symbol = f"{record['base']}USDT"
        # Start a week before the announcement so the first bar is the contract's first.
        start_ms = int(record["event_time"].timestamp() * 1000) - 7 * 86_400_000
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                KLINES,
                params={"symbol": symbol, "interval": "1d", "startTime": start_ms,
                        "limit": WINDOW_DAYS},
            )
            if response.status_code != 200:
                return {"_drop": "no_klines"}
            bars = response.json()
            if len(bars) < 9:
                return {"_drop": "short_history"}

            closes = [float(bar[4]) for bar in bars]
            # Close time (index 6), not open: the registered entry is the close, and
            # funding settled during the entry day predates the position.
            entry_ms, exit_ms = int(bars[1][6]), int(bars[8][6])
            settlements_response = client.get(
                FUNDING,
                params={"symbol": symbol, "startTime": entry_ms, "endTime": exit_ms,
                        "limit": 100},
            )
        settlements = (
            settlements_response.json() if settlements_response.status_code == 200 else []
        )
        return {
            "asset": symbol,
            "announced_at": record["event_time"],
            "listing_day_ms": int(bars[0][0]),
            "entry_close": closes[1],
            "exit_close": closes[8],
            # Signed as the short earns it: positive funding is paid by longs.
            "funding_received": sum(float(f["fundingRate"]) for f in settlements),
            "n_funding_settlements": len(settlements),
            "first_day_return": closes[1] / closes[0] - 1.0,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        for done, result in enumerate(pool.map(one, targets), start=1):
            if "_drop" in result:
                dropped[result["_drop"]] += 1
            else:
                rows.append(result)
            if done % 100 == 0:
                print(f"  {done}/{len(targets)} fetched, {len(rows)} usable", flush=True)

    frame = pl.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(args.out)
    print(f"listings announced      : {listings.height}")
    for reason, count in dropped.items():
        print(f"  dropped {reason:20s}: {count}")
    print(f"usable events           : {frame.height}  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
