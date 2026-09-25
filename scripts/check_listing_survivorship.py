"""Are delisted perpetuals still retrievable? Plan.md §0.4 hypothesis 2.

The listings study is an event study, so each asset's reaction is observed at
its listing regardless of what happened later — the announcement corpus records
the event even for assets that died afterwards. The bias enters one level down:
if the venue stops serving klines for a contract once it is delisted, then the
sample silently reduces to survivors, and survivors of a listing cohort are
exactly the ones that went up.

This answers that question with the venue rather than with an assumption.
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
    # Both perpetual flavours count as live. Filtering to "PERPETUAL" alone drops
    # the 201 TRADIFI_PERPETUAL contracts — tokenised equities — and reports every
    # one of them as delisted, which inflated the first run of this check.
    perpetual = {"PERPETUAL", "TRADIFI_PERPETUAL"}
    live = {s["symbol"] for s in info["symbols"] if s.get("contractType") in perpetual}
    tradfi = {base_ticker(s["symbol"]) for s in info["symbols"]
              if s.get("contractType") == "TRADIFI_PERPETUAL"}
    live_base = {base_ticker(s) for s in live}

    announced = set(listed["base"].to_list())
    # Tokenised equities are a different instrument with different holders and a
    # different news flow; pooling them into a token-listing cross-section mixes
    # two populations. Reported separately rather than silently dropped.
    print(f"of which tokenised equities: {len(announced & tradfi)}")
    gone = sorted(announced - live_base)
    print(f"announced as listed : {len(announced)}")
    print(f"still trading       : {len(announced & live_base)}")
    print(f"no longer listed    : {len(gone)}  ({len(gone) / len(announced):.0%})")

    # The decisive part. A delisted contract whose klines still resolve means the
    # cohort can be reconstructed whole; a 400 means the sample is survivors only.
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
