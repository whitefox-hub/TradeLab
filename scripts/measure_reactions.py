"""Measure each event's abnormal reaction vs BTC from daily closes. Plan.md §11.3.

Daily bars only, so sub-daily horizons are skipped, not approximated. Upserts per
(event, asset, horizon) with the estimator's `code_hash`.

    python scripts/measure_reactions.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import sqlalchemy as sa
from tradelab.news import reaction as reaction_module

from tradelab.config import Settings
from tradelab.events.reaction import Window, leakage_flag, measure

KLINES = "https://fapi.binance.com/fapi/v1/klines"
MARKET = "BTCUSDT"
BARS_PER_DAY = 1
DAILY_HORIZONS = (-1440, 1440, 4320, 10080)


def code_hash() -> str:
    return hashlib.sha256(
        Path(reaction_module.__file__).read_bytes()).hexdigest()[:16]


def closes(symbol: str, start: datetime, end: datetime,
           client: httpx.Client) -> dict[int, float]:
    response = client.get(KLINES, params={
        "symbol": symbol, "interval": "1d",
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000), "limit": 400})
    if response.status_code != 200:
        return {}
    return {int(b[0]): float(b[4]) for b in response.json()}


def returns(series: dict[int, float]) -> tuple[np.ndarray, np.ndarray]:
    days = sorted(series)
    values = np.array([series[d] for d in days])
    return np.array(days[1:]), np.diff(values) / values[:-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = sa.create_engine(Settings().database_url)
    version = code_hash()

    with engine.connect() as connection:
        events = connection.execute(sa.text("""
            SELECT e.id, e.event_type, e.shape, e.first_seen_at,
                   array_agg(DISTINCT a.asset) AS assets
              FROM events e JOIN event_assets a ON a.event_id = e.id
             GROUP BY e.id ORDER BY e.first_seen_at DESC
        """)).mappings().all()
    if args.limit:
        events = events[: args.limit]

    print(f"{len(events)} events with assets, estimator {version}")
    measured = 0
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    with httpx.Client(timeout=25.0) as client, engine.begin() as connection:
        for row in events:
            start = row["first_seen_at"] - timedelta(days=45)
            end = row["first_seen_at"] + timedelta(days=12)
            if row["first_seen_at"] > datetime.now(UTC):
                skip("not yet happened")
                continue

            market = closes(MARKET, start, end, client)
            if len(market) < 25:
                skip("no market history")
                continue
            market_days, market_returns = returns(market)

            for asset in row["assets"]:
                symbol = asset if asset.endswith(("USDT", "USDC")) else f"{asset}USDT"
                series = closes(symbol, start, end, client)
                if len(series) < 25:
                    skip("no pre-event history (listing, or too new)")
                    continue
                asset_days, asset_returns = returns(series)

                shared = np.intersect1d(asset_days, market_days)
                if shared.size < 25:
                    skip("too few shared days with the market")
                    continue
                aligned_asset = asset_returns[np.isin(asset_days, shared)]
                aligned_market = market_returns[np.isin(market_days, shared)]

                event_ms = int(row["first_seen_at"].timestamp() * 1000)
                event_index = int(np.searchsorted(shared, event_ms))
                if not 25 <= event_index < shared.size - 1:
                    skip("event too close to the edge of the window")
                    continue

                window = Window(shared, aligned_asset, aligned_market, event_index)
                self_benchmarked = symbol == MARKET
                results = measure(
                    window, symbol, BARS_PER_DAY,
                    market_wide=row["shape"] == "regime_shift" or self_benchmarked,
                    horizons=DAILY_HORIZONS)
                if not results:
                    skip("no horizon fits the available data")
                    continue
                leaked = leakage_flag(results)

                if args.dry_run:
                    for r in results:
                        print(f"  {symbol:12s} h={r.horizon_minutes:6d} "
                              f"car={r.car:+.4f} z={r.z:+.2f}")
                    measured += 1
                    continue

                for r in results:
                    connection.execute(sa.text("""
                        INSERT INTO event_reactions (event_id, asset,
                            horizon_minutes, car, z, benchmark_kind, beta_pre,
                            sigma_pre, n_bars, leaked, code_hash)
                        VALUES (:e, :a, :h, :car, :z, :bk, :beta, :sigma, :n,
                                :leaked, :hash)
                        ON CONFLICT (event_id, asset, horizon_minutes)
                        DO UPDATE SET car = EXCLUDED.car, z = EXCLUDED.z,
                            benchmark_kind = EXCLUDED.benchmark_kind,
                            beta_pre = EXCLUDED.beta_pre,
                            sigma_pre = EXCLUDED.sigma_pre,
                            n_bars = EXCLUDED.n_bars, leaked = EXCLUDED.leaked,
                            measured_at = now(), code_hash = EXCLUDED.code_hash
                    """), {"e": row["id"], "a": symbol, "h": r.horizon_minutes,
                           "car": r.car, "z": r.z, "bk": str(r.benchmark_kind),
                           "beta": r.beta_pre, "sigma": r.sigma_pre,
                           "n": r.n_bars, "leaked": leaked, "hash": version})
                measured += 1

    print(f"measured {measured}")
    for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"  skipped {count:4d}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
