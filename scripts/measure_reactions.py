"""Measure what happened after each event. Plan.md §11.3.

Reads daily closes from the venue, differences each asset against BTC over an
embargoed pre-event window, normalises to sigma, and stores one row per event,
asset and horizon.

Daily bars, which bounds what this can say. The grid in §11 runs from five
minutes, and at daily resolution the short horizons are unmeasurable — they are
skipped rather than approximated from a daily bar, because a five-minute
reaction read off a daily close is not a five-minute reaction. Restoring them
needs the 1-minute archive, which exists but is 24GB and is a separate job.

Idempotent per (event, asset, horizon): re-running with a changed estimator
overwrites and records the new `code_hash`, so a corpus can always say what
measured it.
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import sqlalchemy as sa

from tradelab.config import Settings
from tradelab.news import reaction as reaction_module
from tradelab.news.reaction import Window, leakage_flag, measure

KLINES = "https://fapi.binance.com/fapi/v1/klines"
MARKET = "BTCUSDT"
BARS_PER_DAY = 1
#: Only the horizons a daily bar can carry. The shorter ones are left unmeasured
#: rather than approximated.
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
    # Counted by reason rather than as one number. "skipped 60" says nothing
    # about whether the measurement is broken or the events are unmeasurable,
    # and those want opposite responses.
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    with httpx.Client(timeout=25.0) as client, engine.begin() as connection:
        for row in events:
            start = row["first_seen_at"] - timedelta(days=45)
            end = row["first_seen_at"] + timedelta(days=12)
            # A future event has no reaction yet; measuring one would read a
            # window that does not exist.
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
                    # Almost always a listing: the contract's first day *is* the
                    # event, so there is no pre-event window to difference
                    # against. §11.3's machinery measures a deviation from a
                    # baseline the asset already had, and a listing has none —
                    # which is why PREREG-001 measured listings forward from the
                    # listing day rather than as abnormal returns.
                    skip("no pre-event history (listing, or too new)")
                    continue
                asset_days, asset_returns = returns(series)

                # Inner join on the day, never a fill: a missing bar is an
                # absence and pairing it with a zero invents a quiet day.
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
                # The benchmark asset cannot be differenced against itself: the
                # abnormal return is zero by construction, and a row of zeroes
                # reads as "nothing happened" rather than "not measurable this
                # way". It falls back to the time-series counterfactual, which
                # is the same treatment a market-wide event gets and for the
                # same reason — no unaffected asset exists.
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
