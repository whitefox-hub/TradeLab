"""Live 5-minute inputs for the Flash Strike forward run, in the archive's conventions.

Everything returned here has the schema and time convention of `intraday`: a kline's
`available_ms` is its close, and a metrics row's is the moment it describes. REST
`openInterestHist` and `topLongShortPositionRatio` stamp that moment directly, the archive
stamps it five minutes early (`METRICS_STAMP_LAG_MS`). `flash_data.assemble` then applies
the publication margin, identically for both sources.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl

from tradelab.collectors.binance.intraday import (
    KLINE_SCHEMA,
    METRICS_SCHEMA,
    month_of_klines,
    month_of_metrics,
)

REST = "https://fapi.binance.com"
#: /futures/data shares 1000 requests per 5 minutes per IP; paced under it.
DATA_INTERVAL_S = 300 / 900


def recent_klines(client: httpx.Client, symbol: str, kind: str = "klines",
                  limit: int = 12, now_ms: int | None = None) -> pl.DataFrame:
    """Closed 5-minute bars (klines or premiumIndexKlines), oldest first."""
    path = "/fapi/v1/klines" if kind == "klines" else "/fapi/v1/premiumIndexKlines"
    rows = client.get(f"{REST}{path}", params={"symbol": symbol, "interval": "5m",
                                               "limit": limit}).json()
    if not isinstance(rows, list):
        return pl.DataFrame(schema=KLINE_SCHEMA)
    cutoff = now_ms if now_ms is not None else int(time.time() * 1000)
    closed = [r for r in rows if int(r[6]) < cutoff]
    return pl.DataFrame(
        [(int(r[0]), int(r[6]) + 1, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
          float(r[5]), float(r[7]), int(r[8]), float(r[9]), float(r[10])) for r in closed],
        schema=KLINE_SCHEMA, orient="row")


def recent_metrics(client: httpx.Client, symbol: str, limit: int = 12,
                   pace: float = DATA_INTERVAL_S) -> pl.DataFrame:
    """Open interest and the top-trader position ratio, joined on their timestamp."""
    params = {"symbol": symbol, "period": "5m", "limit": limit}
    time.sleep(pace)
    oi = client.get(f"{REST}/futures/data/openInterestHist", params=params).json()
    time.sleep(pace)
    top = client.get(f"{REST}/futures/data/topLongShortPositionRatio", params=params).json()
    ratio = ({int(r["timestamp"]): float(r["longShortRatio"]) for r in top}
             if isinstance(top, list) else {})
    rows = [(int(r["timestamp"]), float(r["sumOpenInterest"]), float(r["sumOpenInterestValue"]),
             None, ratio.get(int(r["timestamp"])), None, None)
            for r in (oi if isinstance(oi, list) else [])]
    return pl.DataFrame(rows, schema=METRICS_SCHEMA, orient="row")


def warm(client: httpx.Client, symbol: str, now: datetime,
         days: int = 31) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """`days` of history: the archive for closed days, REST for the last two.

    The daily archive lags about a day, so the most recent bars come from REST;
    overlapping rows are deduplicated on `available_ms`.
    """
    start = now - timedelta(days=days)
    months = sorted({date(start.year, start.month, 1), date(now.year, now.month, 1)})
    until = now.date() - timedelta(days=1)
    frames: dict[str, list[pl.DataFrame]] = {"klines": [], "premium": [], "metrics": []}
    for month in months:
        frames["klines"].append(month_of_klines(client, "klines", symbol, month, until))
        frames["premium"].append(month_of_klines(client, "premiumIndexKlines", symbol,
                                                 month, until))
        frames["metrics"].append(month_of_metrics(client, symbol, month, until))
    now_ms = int(now.timestamp() * 1000)
    frames["klines"].append(recent_klines(client, symbol, "klines", 1000, now_ms))
    frames["premium"].append(recent_klines(client, symbol, "premium", 1000, now_ms))
    frames["metrics"].append(recent_metrics(client, symbol, 500))
    low = int(start.timestamp() * 1000)
    out = []
    for key in ("klines", "premium", "metrics"):
        frame = pl.concat([f for f in frames[key] if not f.is_empty()] or
                          [pl.DataFrame(schema=METRICS_SCHEMA if key == "metrics"
                                        else KLINE_SCHEMA)])
        out.append(frame.filter(pl.col("available_ms") > low)
                   .unique("available_ms", keep="last").sort("available_ms"))
    return out[0], out[1], out[2]


def merge(old: pl.DataFrame, new: pl.DataFrame, keep_ms: int) -> pl.DataFrame:
    """Append `new`, prefer its rows on a clash, drop what is older than `keep_ms`."""
    both = pl.concat([old, new]) if not old.is_empty() else new
    return (both.unique("available_ms", keep="last").sort("available_ms")
            .filter(pl.col("available_ms") > keep_ms))


def utc_ms(moment: datetime | None = None) -> int:
    return int((moment or datetime.now(UTC)).timestamp() * 1000)
