"""Event dataset for PREREG-001: price and funding around each new listing.

The listing day is taken as the **first available kline**, not the announcement
timestamp. The announcement precedes the launch by hours to days and says so in
prose, and parsing that prose would put a second parser between the hypothesis
and the data. The first bar a contract ever printed is the listing day by
definition, and it is available for delisted contracts too — which is what keeps
this survivorship-neutral.
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
        # A week before the announcement: the launch always follows it, so the
        # first bar returned is the contract's own first bar.
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
            # Close time of the entry bar, not its open. The registered entry is
            # the *close* of the first day after listing, so funding settled
            # during that day was paid before any position existed. Using the
            # open overstated the funding window by a full day.
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

    # Modest concurrency. Sequentially this takes long enough that one transient
    # failure costs the whole run, and progress is printed so a stall is visible
    # rather than inferred from silence.
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
