"""Effective breadth of the Binance-Bybit funding spread signal, pre-registration. §0.4a.

    python scripts/measure_funding_breadth.py [--assets 60] [--days 180]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import polars as pl

BINANCE = "https://fapi.binance.com/fapi/v1/fundingRate"
BYBIT = "https://api.bybit.com/v5/market/funding/history"


def _daily(rows: list[tuple[int, float]]) -> dict[str, float]:
    """Funding summed per UTC day, since venues settle on different intervals."""
    out: dict[str, float] = {}
    for ms, rate in rows:
        day = datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()
        out[day] = out.get(day, 0.0) + rate
    return out


def binance_funding(symbol: str, start_ms: int, end_ms: int) -> dict[str, float]:
    rows: list[tuple[int, float]] = []
    cursor = start_ms
    with httpx.Client(timeout=20.0) as client:
        while cursor < end_ms:
            payload = client.get(BINANCE, params={
                "symbol": f"{symbol}USDT", "startTime": cursor,
                "endTime": end_ms, "limit": 1000}).json()
            if not isinstance(payload, list) or not payload:
                break
            rows += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in payload]
            nxt = int(payload[-1]["fundingTime"]) + 1
            if nxt <= cursor:
                break
            cursor = nxt
    return _daily(rows)


def bybit_funding(symbol: str, start_ms: int, end_ms: int) -> dict[str, float]:
    rows: list[tuple[int, float]] = []
    cursor = end_ms
    with httpx.Client(timeout=20.0) as client:
        while cursor > start_ms:
            payload = client.get(BYBIT, params={
                "category": "linear", "symbol": f"{symbol}USDT",
                "startTime": start_ms, "endTime": cursor, "limit": 200}).json()
            items = payload.get("result", {}).get("list") or []
            if not items:
                break
            rows += [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in items]
            oldest = min(int(r["fundingRateTimestamp"]) for r in items)
            if oldest >= cursor:
                break
            cursor = oldest - 1
    return _daily(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=int, default=60)
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args()

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    # Alphabetical selection, so the sample does not depend on the signal.
    binance_universe = {
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
    bybit_universe = {
        i["baseCoin"] for i in items
        if i.get("contractType") == "LinearPerpetual" and i.get("quoteCoin") == "USDT"
        and i.get("status") == "Trading"
    }
    both = sorted(binance_universe & bybit_universe)[: args.assets]
    print(f"{len(both)} assets on both venues, {args.days} days")

    def spread(symbol: str) -> tuple[str, dict[str, float]] | None:
        a, b = binance_funding(symbol, start_ms, end_ms), bybit_funding(symbol, start_ms, end_ms)
        shared = sorted(set(a) & set(b))
        if len(shared) < args.days * 0.5:
            return None
        return symbol, {day: a[day] - b[day] for day in shared}

    series: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(spread, both):
            if result:
                series[result[0]] = result[1]
    print(f"{len(series)} with usable overlap")

    days = sorted(set.intersection(*(set(v) for v in series.values())))
    matrix = np.array([[series[s][d] for d in days] for s in series])
    print(f"matrix {matrix.shape[0]} assets x {matrix.shape[1]} days")

    corr = np.corrcoef(matrix)
    off = corr[~np.eye(len(corr), dtype=bool)]
    mean_rho = float(np.nanmean(off))
    n = len(corr)
    # Mean-rho adjustment, and eigenvalue participation ratio (no single-factor assumption).
    n_eff_rho = n / (1 + (n - 1) * mean_rho) if mean_rho > 0 else float(n)
    eigenvalues = np.linalg.eigvalsh(np.nan_to_num(corr))
    n_eff_pr = float(eigenvalues.sum() ** 2 / (eigenvalues**2).sum())

    print(f"\nmean pairwise correlation : {mean_rho:+.4f}")
    print(f"nominal breadth           : {n}")
    print(f"effective breadth (rho)   : {n_eff_rho:.1f}")
    print(f"effective breadth (PR)    : {n_eff_pr:.1f}")
    print(f"IR multiplier vs one bet  : {np.sqrt(min(n_eff_rho, n_eff_pr)):.1f}x")

    mean_abs = float(np.mean(np.abs(matrix)))
    print(f"\nmean |daily spread|       : {mean_abs:.5%} "
          f"({mean_abs * 365:.2%}/yr gross)")

    # A spread that does not persist over the holding period is not harvestable.
    print("\nspread autocorrelation (mean across assets):")
    for lag in (1, 3, 7, 14, 30):
        rhos = [
            float(np.corrcoef(row[:-lag], row[lag:])[0, 1])
            for row in matrix if np.std(row[:-lag]) > 0 and np.std(row[lag:]) > 0
        ]
        print(f"  lag {lag:2d}d : {np.nanmean(rhos):+.4f}")

    out = pl.DataFrame(
        {"day": days, **{asset: matrix[i].tolist() for i, asset in enumerate(series)}}
    )
    out.write_parquet("data/parquet/funding_spreads.parquet")
    print("\nspread matrix -> data/parquet/funding_spreads.parquet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
