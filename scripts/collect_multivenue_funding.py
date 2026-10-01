"""Two years of funding and closes across five venues. Plan.md §0.4 hypothesis 3.

Universe: perpetuals listed on all five venues (availability-based, so registrable without
seeing returns; biased toward established names). Checkpointed per asset.

    python scripts/collect_multivenue_funding.py [--days 730] [--limit N]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import polars as pl

from tradelab.collectors.multivenue.funding import (
    SPECS,
    closes,
    fetch,
    to_daily,
    utc_window,
)


def universes() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    out["binance"] = {
        s["symbol"].removesuffix("USDT")
        for s in httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
        .json()["symbols"]
        if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
    }
    items, cursor = [], None
    while True:
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        result = httpx.get("https://api.bybit.com/v5/market/instruments-info",
                           params=params, timeout=30).json()["result"]
        items += result["list"]
        cursor = result.get("nextPageCursor")
        if not cursor:
            break
    out["bybit"] = {
        i["baseCoin"] for i in items
        if i.get("contractType") == "LinearPerpetual" and i.get("quoteCoin") == "USDT"
        and i.get("status") == "Trading"
    }
    out["okx"] = {
        i["instId"].split("-")[0]
        for i in httpx.get("https://www.okx.com/api/v5/public/instruments",
                           params={"instType": "SWAP"}, timeout=30).json()["data"]
        if i["instId"].endswith("-USDT-SWAP") and i.get("state") == "live"
    }
    out["gate"] = {
        c["name"].removesuffix("_USDT")
        for c in httpx.get("https://api.gateio.ws/api/v4/futures/usdt/contracts",
                           timeout=30).json()
        if not c.get("in_delisting")
    }
    out["bitget"] = {
        t["symbol"].removesuffix("USDT")
        for t in httpx.get("https://api.bitget.com/api/v2/mix/market/tickers",
                           params={"productType": "usdt-futures"}, timeout=30).json()["data"]
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--out", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    parser.add_argument("--limit", type=int, default=0, help="cap assets, for a dry run")
    args = parser.parse_args()

    start, end = utc_window(args.days)
    sets = universes()
    for venue, assets in sets.items():
        print(f"  {venue:9s} {len(assets):5d} perps")
    everywhere = sorted(set.intersection(*sets.values()))
    if args.limit:
        everywhere = everywhere[: args.limit]
    print(f"on all five venues: {len(everywhere)}  |  window {start.date()} -> {end.date()}")

    args.out.mkdir(parents=True, exist_ok=True)

    def collect(base: str) -> tuple[str, int]:
        target = args.out / f"{base}.parquet"
        if target.exists():
            return base, -1
        frames = []
        with httpx.Client(timeout=20.0) as client:
            for venue in SPECS:
                try:
                    daily = to_daily(fetch(venue, base, start, end, client=client))
                except Exception:
                    continue
                if not daily.height:
                    continue
                marks = closes(venue, base, start, end, client=client)
                price = pl.DataFrame({
                    "day": [
                        __import__("datetime").datetime.fromtimestamp(
                            ms / 1000, __import__("datetime").UTC).date()
                        for ms in marks
                    ],
                    "close": list(marks.values()),
                }) if marks else pl.DataFrame(schema={"day": pl.Date, "close": pl.Float64})
                daily = daily.join(price.unique(subset=["day"]), on="day", how="left")
                frames.append(daily.with_columns(pl.lit(venue).alias("venue")))
        if not frames:
            return base, 0
        combined = pl.concat(frames).with_columns(pl.lit(base).alias("asset"))
        combined.write_parquet(target)
        return base, combined.height

    done = skipped = empty = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        for index, (_base, rows) in enumerate(pool.map(collect, everywhere), start=1):
            if rows < 0:
                skipped += 1
            elif rows == 0:
                empty += 1
            else:
                done += 1
            if index % 25 == 0:
                print(f"  {index}/{len(everywhere)}  collected={done} "
                      f"resumed={skipped} empty={empty}", flush=True)

    print(f"collected {done}, resumed {skipped}, empty {empty} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
