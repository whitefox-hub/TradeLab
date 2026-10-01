"""Funding history across venues (Plan.md §0.4 hypothesis 3, §4.2).

Venues differ in settlement interval (4h/8h, per contract), so compare only daily sums
(`to_daily`). All venues here quote positive = longs pay. Symbols keyed by base coin."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import polars as pl

Rate = tuple[int, float]

VENUES = ("binance", "bybit", "okx", "gate", "bitget")


@dataclass(frozen=True, slots=True)
class VenueSpec:
    name: str
    symbol: Callable[[str], str]
    fetch: Callable[[httpx.Client, str, int, int], list[Rate]]


def _binance(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[Rate]:
    out: list[Rate] = []
    cursor = start_ms
    while cursor < end_ms:
        payload = client.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": cursor, "endTime": end_ms,
                    "limit": 1000},
        ).json()
        if not isinstance(payload, list) or not payload:
            break
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in payload]
        nxt = int(payload[-1]["fundingTime"]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
    return out


def _bybit(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[Rate]:
    out: list[Rate] = []
    cursor = end_ms
    while cursor > start_ms:
        payload = client.get(
            "https://api.bybit.com/v5/market/funding/history",
            params={"category": "linear", "symbol": symbol, "startTime": start_ms,
                    "endTime": cursor, "limit": 200},
        ).json()
        items = payload.get("result", {}).get("list") or []
        if not items:
            break
        out += [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in items]
        oldest = min(int(r["fundingRateTimestamp"]) for r in items)
        if oldest >= cursor:
            break
        cursor = oldest - 1
    return out


FUNDING_HISTORY_DAYS: dict[str, int | None] = {
    "binance": None, "bybit": None, "gate": None, "okx": 96, "bitget": 90,
}


def _okx(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[Rate]:
    out: list[Rate] = []
    cursor = end_ms
    while cursor > start_ms:
        payload = client.get(
            "https://www.okx.com/api/v5/public/funding-rate-history",
            params={"instId": symbol, "before": start_ms, "after": cursor, "limit": 100},
        ).json()
        rows = payload.get("data") or []
        if not rows:
            break
        out += [(int(r["fundingTime"]), float(r["realizedRate"] or r["fundingRate"]))
                for r in rows]
        oldest = min(int(r["fundingTime"]) for r in rows)
        if oldest >= cursor:
            break
        cursor = oldest - 1
    return out


def _gate(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[Rate]:
    out: list[Rate] = []
    cursor = end_ms // 1000
    while cursor > start_ms // 1000:
        rows = client.get(
            "https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
            params={"contract": symbol, "limit": 1000, "to": cursor},
        ).json()
        if not isinstance(rows, list) or not rows:
            break
        out += [(int(r["t"]) * 1000, float(r["r"])) for r in rows]
        oldest = min(int(r["t"]) for r in rows)
        if oldest >= cursor:
            break
        cursor = oldest - 1
    return [r for r in out if start_ms <= r[0] <= end_ms]


def _bitget(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[Rate]:
    out: list[Rate] = []
    page = 1
    while page <= 50:
        payload = client.get(
            "https://api.bitget.com/api/v2/mix/market/history-fund-rate",
            params={"symbol": symbol, "productType": "usdt-futures",
                    "pageSize": 100, "pageNo": page},
        ).json()
        rows = payload.get("data") or []
        if not rows:
            break
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        if min(int(r["fundingTime"]) for r in rows) < start_ms:
            break
        page += 1
    return [r for r in out if start_ms <= r[0] <= end_ms]


SPECS: dict[str, VenueSpec] = {
    "binance": VenueSpec("binance", lambda base: f"{base}USDT", _binance),
    "bybit": VenueSpec("bybit", lambda base: f"{base}USDT", _bybit),
    "okx": VenueSpec("okx", lambda base: f"{base}-USDT-SWAP", _okx),
    "gate": VenueSpec("gate", lambda base: f"{base}_USDT", _gate),
    "bitget": VenueSpec("bitget", lambda base: f"{base}USDT", _bitget),
}


def fetch(venue: str, base: str, start: datetime, end: datetime,
          client: httpx.Client | None = None) -> list[Rate]:
    """One venue, one asset, deduplicated and sorted."""
    spec = SPECS[venue]
    owned = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        rows = spec.fetch(client, spec.symbol(base),
                          int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    finally:
        if owned:
            client.close()
    return sorted(dict(rows).items())


def to_daily(rows: list[Rate]) -> pl.DataFrame:
    """Sum settlements per UTC day, making venues on different schedules comparable."""
    if not rows:
        return pl.DataFrame(schema={"day": pl.Date, "rate": pl.Float64, "n": pl.UInt32})
    frame = pl.DataFrame(
        {"ms": [r[0] for r in rows], "rate": [r[1] for r in rows]}
    ).with_columns(
        pl.from_epoch("ms", time_unit="ms").dt.replace_time_zone("UTC").dt.date().alias("day")
    )
    return (
        frame.group_by("day")
        .agg(pl.col("rate").sum(), pl.len().alias("n"))
        .sort("day")
    )


def spread(a: pl.DataFrame, b: pl.DataFrame, min_days: int = 30) -> pl.DataFrame | None:
    """Daily difference on days both venues quoted. Inner join: a missing day is not zero."""
    joined = a.join(b, on="day", how="inner", suffix="_b")
    if joined.height < min_days:
        return None
    return joined.select(
        "day",
        (pl.col("rate") - pl.col("rate_b")).alias("spread"),
        pl.col("n").alias("n_a"),
        pl.col("n_b").alias("n_b"),
    )


def closes(venue: str, base: str, start: datetime, end: datetime,
           client: httpx.Client | None = None) -> dict[int, float]:
    """Daily closes keyed by UTC-midnight epoch ms."""
    owned = client is None
    client = client or httpx.Client(timeout=20.0)
    symbol = SPECS[venue].symbol(base)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    try:
        if venue == "binance":
            rows = client.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": symbol, "interval": "1d",
                        "startTime": start_ms, "limit": 1500}).json()
            out = {int(b[0]): float(b[4]) for b in rows} if isinstance(rows, list) else {}
        elif venue == "bybit":
            payload = client.get(
                "https://api.bybit.com/v5/market/kline",
                params={"category": "linear", "symbol": symbol, "interval": "D",
                        "start": start_ms, "end": end_ms, "limit": 1000}).json()
            out = {int(b[0]): float(b[4])
                   for b in (payload.get("result", {}).get("list") or [])}
        elif venue == "okx":
            out = {}
            cursor = end_ms
            while cursor > start_ms:
                rows = client.get(
                    "https://www.okx.com/api/v5/market/history-candles",
                    params={"instId": symbol, "bar": "1D", "after": cursor,
                            "limit": 100}).json().get("data") or []
                if not rows:
                    break
                out |= {int(b[0]): float(b[4]) for b in rows}
                oldest = min(int(b[0]) for b in rows)
                if oldest >= cursor:
                    break
                cursor = oldest - 1
        elif venue == "gate":
            rows = client.get(
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                params={"contract": symbol, "interval": "1d", "limit": 1000}).json()
            out = {int(r["t"]) * 1000: float(r["c"])
                   for r in rows} if isinstance(rows, list) else {}
        else:
            out = {}
            cursor = end_ms
            while cursor > start_ms:
                rows = client.get(
                    "https://api.bitget.com/api/v2/mix/market/history-candles",
                    params={"symbol": symbol, "productType": "usdt-futures",
                            "granularity": "1D", "endTime": cursor,
                            "limit": 200}).json().get("data") or []
                if not rows:
                    break
                out |= {int(b[0]): float(b[4]) for b in rows}
                oldest = min(int(b[0]) for b in rows)
                if oldest >= cursor:
                    break
                cursor = oldest - 1
    except Exception:
        return {}
    finally:
        if owned:
            client.close()
    return {ms: px for ms, px in out.items() if start_ms <= ms <= end_ms and px > 0}


def utc_window(days: int) -> tuple[datetime, datetime]:
    """A closed window ending now, to the second."""
    end = datetime.now(UTC).replace(microsecond=0)
    return end - timedelta(days=days), end
