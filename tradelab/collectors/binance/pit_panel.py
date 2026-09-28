"""Survivorship-neutral daily panel of Binance USD-M perpetuals (Plan.md §4.4, RESULT-007).

Measured venue facts:
* `exchangeInfo` keeps delisted perpetuals as `SETTLING`, `deliveryDate` = delisting instant;
  some are dropped and survive only in the public archive.
* Klines continue after delisting at the frozen price with zero volume: keep only traded
  days, never after `deliveryDate`.
* Some symbols are unknown to REST (-1121); the monthly archive is used for those alone.
* Funding: 500 requests / 5 min; a refused page must raise, never read as an empty one."""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import polars as pl

from tradelab.collectors.multivenue.funding import to_daily

REST = "https://fapi.binance.com"
ARCHIVE = "https://data.binance.vision/data/futures/um/monthly"
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

#: Crypto perpetuals only (excludes TradFi and index perpetuals).
CONTRACT = "PERPETUAL"
UNDERLYING = "COIN"
QUOTE = "USDT"

#: Published funding limit is 500 requests / 300 s; paced a little under it.
FUNDING_INTERVAL_S = 300 / 450


class Refused(RuntimeError):
    """The venue answered with an error where data was expected."""


@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    status: str                  # TRADING, SETTLING, or ARCHIVED (not in exchangeInfo)
    delisted_at: datetime | None

    @property
    def asset(self) -> str:
        return self.symbol.removesuffix(QUOTE)


def universe(client: httpx.Client) -> list[Contract]:
    """Every USDT crypto perpetual the venue lists, alive or settling, plus the
    ones that survive only in the archive."""
    info = _json(client.get(f"{REST}/fapi/v1/exchangeInfo"))
    assert isinstance(info, dict)
    known: dict[str, dict[str, Any]] = {s["symbol"]: s for s in info["symbols"]}
    out = [
        Contract(s["symbol"], s["status"],
                 _ms(s["deliveryDate"]) if s["status"] == "SETTLING" else None)
        for s in known.values()
        if s.get("contractType") == CONTRACT and s.get("underlyingType") == UNDERLYING
        and s.get("quoteAsset") == QUOTE and s["status"] in ("TRADING", "SETTLING")
    ]
    out += [Contract(symbol, "ARCHIVED", None)
            for symbol in archived_symbols(client) if symbol not in known]
    return sorted(out, key=lambda c: c.symbol)


def archived_symbols(client: httpx.Client) -> list[str]:
    """USDT perpetual symbols with monthly klines in the public archive.

    Delivery contracts carry an underscore (`BTCUSDT_250328`) and are excluded.
    """
    prefix = "data/futures/um/monthly/klines/"
    names, marker = [], ""
    while True:
        page = client.get(BUCKET, params={"delimiter": "/", "prefix": prefix,
                                          "marker": marker}).text
        names += [n[len(prefix):].strip("/")
                  for n in re.findall(r"<Prefix>([^<]+)</Prefix>", page) if n != prefix]
        if "<IsTruncated>true</IsTruncated>" not in page:
            break
        marker = re.findall(r"<NextMarker>([^<]+)</NextMarker>", page)[0]
    return sorted(n for n in names if n.endswith(QUOTE) and "_" not in n)


# -- prices --------------------------------------------------------------------


def live_days(bars: Iterable[tuple[int, float, float]],
              delisted_at: datetime | None) -> pl.DataFrame:
    """Daily closes on traded days, none after delisting. `bars` is (open ms, close, volume).

    Zero-volume days are dropped wherever they fall: the price is only a repeat."""
    cutoff = delisted_at.date() if delisted_at else None
    rows = [(datetime.fromtimestamp(ms / 1000, UTC).date(), close)
            for ms, close, volume in bars if volume > 0]
    if cutoff is not None:
        rows = [(d, c) for d, c in rows if d <= cutoff]
    return pl.DataFrame(rows, schema={"day": pl.Date, "close": pl.Float64}, orient="row")


def rest_bars(client: httpx.Client, symbol: str,
              start: datetime, end: datetime) -> list[tuple[int, float, float]]:
    out: list[tuple[int, float, float]] = []
    cursor = _to_ms(start)
    while cursor < _to_ms(end):
        rows = _json(client.get(f"{REST}/fapi/v1/klines", params={
            "symbol": symbol, "interval": "1d", "startTime": cursor,
            "endTime": _to_ms(end), "limit": 1500}))
        assert isinstance(rows, list)
        if not rows:
            break
        out += [(int(r[0]), float(r[4]), float(r[5])) for r in rows]
        cursor = int(rows[-1][0]) + 1
    return out


# -- funding -------------------------------------------------------------------


def rest_funding(client: httpx.Client, symbol: str, start: datetime, end: datetime,
                 pace: float = FUNDING_INTERVAL_S) -> list[tuple[int, float]]:
    """Every settlement in the window, paced under the shared limit.

    A 429/418 is waited out once, then raises: breaking out would return a truncated history."""
    out: list[tuple[int, float]] = []
    cursor, stop = _to_ms(start), _to_ms(end)
    while cursor < stop:
        time.sleep(pace)
        response = client.get(f"{REST}/fapi/v1/fundingRate", params={
            "symbol": symbol, "startTime": cursor, "endTime": stop, "limit": 1000})
        if response.status_code in (418, 429):
            time.sleep(int(response.headers.get("Retry-After", "60")))
            response = client.get(f"{REST}/fapi/v1/fundingRate", params={
                "symbol": symbol, "startTime": cursor, "endTime": stop, "limit": 1000})
        rows = _json(response)
        assert isinstance(rows, list)
        if not rows:
            break
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        nxt = int(rows[-1]["fundingTime"]) + 1
        if nxt <= cursor:
            break
        cursor = nxt
    return out


# -- the archive, for symbols the REST endpoint no longer knows ----------------


def archive_bars(client: httpx.Client, symbol: str,
                 months: Iterable[date]) -> list[tuple[int, float, float]]:
    out: list[tuple[int, float, float]] = []
    for month in months:
        for row in _archive_rows(client, f"{ARCHIVE}/klines/{symbol}/1d/"
                                 f"{symbol}-1d-{month:%Y-%m}.zip"):
            if row and row[0].isdigit():            # newer files carry a header
                out.append((int(row[0]), float(row[4]), float(row[5])))
    return out


def archive_funding(client: httpx.Client, symbol: str,
                    months: Iterable[date]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for month in months:
        for row in _archive_rows(client, f"{ARCHIVE}/fundingRate/{symbol}/"
                                 f"{symbol}-fundingRate-{month:%Y-%m}.zip"):
            if row and row[0].isdigit():
                out.append((int(row[0]), float(row[2])))
    return out


def _archive_rows(client: httpx.Client, url: str) -> list[list[str]]:
    response = client.get(url)
    if response.status_code == 404:
        return []                                   # not listed that month
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        text = bundle.read(bundle.namelist()[0]).decode()
    return list(csv.reader(io.StringIO(text)))


def months_between(start: datetime, end: datetime) -> list[date]:
    out, cursor = [], date(start.year, start.month, 1)
    while cursor <= end.date():
        out.append(cursor)
        cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    return out


# -- one asset -----------------------------------------------------------------


def collect(client: httpx.Client, contract: Contract,
            start: datetime, end: datetime) -> pl.DataFrame | None:
    """Daily close and funding for one contract. None if it never traded in
    the window."""
    try:
        bars = rest_bars(client, contract.symbol, start, end)
        rates = rest_funding(client, contract.symbol, start, end) if bars else []
        source = "rest"
    except Refused as refused:
        if "-1121" not in str(refused):
            raise
        months = months_between(start, end)
        bars = archive_bars(client, contract.symbol, months)
        rates = archive_funding(client, contract.symbol, months)
        source = "archive"

    prices = live_days(bars, contract.delisted_at)
    prices = prices.filter(pl.col("day").is_between(start.date(), end.date()))
    if prices.is_empty():
        return None
    window = [r for r in rates if _to_ms(start) <= r[0] <= _to_ms(end)]
    daily = to_daily(window)
    # Left join from prices: a settlement without a price is a day nothing could be held.
    return (prices.join(daily, on="day", how="left")
            .with_columns(pl.lit(contract.asset).alias("asset"),
                          pl.lit("binance").alias("venue"),
                          pl.lit(source).alias("source"))
            .sort("day"))


def _json(response: httpx.Response) -> object:
    payload = response.json()
    if isinstance(payload, dict) and "code" in payload and int(payload["code"]) < 0:
        raise Refused(f"{payload['code']}: {payload.get('msg', '')}")
    if response.status_code >= 400:
        raise Refused(f"HTTP {response.status_code}: {response.text[:200]}")
    return payload


def _ms(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)
