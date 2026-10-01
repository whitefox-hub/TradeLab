"""Bybit half of the PREREG-002 cohort.

Listing day is the first available kline, as for Binance, so both venues match.

    python scripts/build_bybit_listing_events.py
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import polars as pl

INSTRUMENTS = "https://api.bybit.com/v5/market/instruments-info"
KLINE = "https://api.bybit.com/v5/market/kline"
FUNDING = "https://api.bybit.com/v5/market/funding/history"
WINDOW_DAYS = 14


def bybit_perpetuals() -> dict[str, int]:
    """Base coin -> launch timestamp in ms, USDT linear perpetuals only."""
    out: dict[str, int] = {}
    cursor: str | None = None
    while True:
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = httpx.get(INSTRUMENTS, params=params, timeout=30).json()["result"]
        for item in payload["list"]:
            if item.get("contractType") == "LinearPerpetual" and item.get("quoteCoin") == "USDT":
                out[item["baseCoin"]] = int(item["launchTime"])
        cursor = payload.get("nextPageCursor")
        if not cursor:
            return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binance", type=Path,
                        default=Path("data/parquet/listing_events.parquet"))
    parser.add_argument("--out", type=Path,
                        default=Path("data/parquet/listing_events_bybit.parquet"))
    args = parser.parse_args()

    perpetuals = bybit_perpetuals()
    already = {
        asset.removesuffix("USDT")
        for asset in pl.read_parquet(args.binance)["asset"].to_list()
    }
    targets = sorted(set(perpetuals) - already)
    print(f"bybit perpetuals {len(perpetuals)}, of which never on binance {len(targets)}")

    dropped = {"no_klines": 0, "short_history": 0}

    def one(base: str) -> dict:
        symbol = f"{base}USDT"
        start = perpetuals[base] - 2 * 86_400_000
        with httpx.Client(timeout=20.0) as client:
            payload = client.get(
                KLINE,
                params={"category": "linear", "symbol": symbol, "interval": "D",
                        "start": start, "limit": WINDOW_DAYS},
            ).json()
            bars = payload.get("result", {}).get("list") or []
            bars = list(reversed(bars))
            if len(bars) < 9:
                return {"_drop": "short_history" if bars else "no_klines"}

            closes = [float(bar[4]) for bar in bars]
            entry_ms = int(bars[1][0]) + 86_400_000
            exit_ms = int(bars[8][0]) + 86_400_000
            funding_payload = client.get(
                FUNDING,
                params={"category": "linear", "symbol": symbol,
                        "startTime": entry_ms, "endTime": exit_ms, "limit": 200},
            ).json()
        settlements = funding_payload.get("result", {}).get("list") or []
        return {
            "asset": symbol,
            "venue": "bybit",
            "listing_day_ms": int(bars[0][0]),
            "entry_close": closes[1],
            "exit_close": closes[8],
            "funding_received": sum(float(f["fundingRate"]) for f in settlements),
            "n_funding_settlements": len(settlements),
            "first_day_return": closes[1] / closes[0] - 1.0,
        }

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for done, result in enumerate(pool.map(one, targets), start=1):
            if "_drop" in result:
                dropped[result["_drop"]] += 1
            else:
                rows.append(result)
            if done % 100 == 0:
                print(f"  {done}/{len(targets)} fetched, {len(rows)} usable", flush=True)

    frame = pl.DataFrame(rows)
    frame.write_parquet(args.out)
    for reason, count in dropped.items():
        print(f"  dropped {reason:16s}: {count}")
    print(f"usable bybit events    : {frame.height} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
