"""Probe whether delisted perpetuals still serve klines. Plan.md §0.4 hypothesis 2.

If not, the listing sample silently reduces to survivors.

    python scripts/check_listing_survivorship.py [--probe 12]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx
import polars as pl

from tradelab.collectors.binance.announcements import base_ticker, classify, extract_assets

EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
KLINES = "https://fapi.binance.com/fapi/v1/klines"


def first_listings(root: Path) -> pl.DataFrame:
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
    return listings.sort("event_time").group_by("base").first()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/parquet"))
    parser.add_argument("--probe", type=int, default=12)
    args = parser.parse_args()

    listed = first_listings(args.root)
    info = httpx.get(EXCHANGE_INFO, timeout=30).json()
    # Both flavours are live: "PERPETUAL" alone would report every TRADIFI_PERPETUAL
    # (tokenised equity) contract as delisted.
    perpetual = {"PERPETUAL", "TRADIFI_PERPETUAL"}
    live = {s["symbol"] for s in info["symbols"] if s.get("contractType") in perpetual}
    tradfi = {base_ticker(s["symbol"]) for s in info["symbols"]
              if s.get("contractType") == "TRADIFI_PERPETUAL"}
    live_base = {base_ticker(s) for s in live}

    announced = set(listed["base"].to_list())
    # Tokenised equities are a different population; reported separately.
    print(f"of which tokenised equities: {len(announced & tradfi)}")
    gone = sorted(announced - live_base)
    print(f"announced as listed : {len(announced)}")
    print(f"still trading       : {len(announced & live_base)}")
    print(f"no longer listed    : {len(gone)}  ({len(gone) / len(announced):.0%})")

    # Klines resolving for a delisted contract means the cohort is reconstructible;
    # a 400 means survivors only.
    print(f"\nprobing {args.probe} delisted symbols for price history:")
    retrievable = 0
    for base in gone[: args.probe]:
        symbol = f"{base}USDT"
        response = httpx.get(
            KLINES, params={"symbol": symbol, "interval": "1d", "limit": 5}, timeout=20
        )
        bars = len(response.json()) if response.status_code == 200 else 0
        retrievable += bars > 0
        print(f"  {symbol:20s} HTTP {response.status_code}  bars={bars}")

    print(f"\n{retrievable}/{args.probe} retrievable")
    if retrievable == 0:
        print("SURVIVORS ONLY: delisted contracts cannot be reconstructed from this endpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
