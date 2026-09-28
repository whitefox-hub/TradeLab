"""5-minute data for PREREG-008 from data.binance.vision (Plan.md §4.1, §4.3, §4.4):
klines, premiumIndexKlines (monthly, daily for an open month) and metrics (daily only).

Measured: archives continue after delisting (metrics OI at 0E-16, klines at a frozen
price), so drop rows at/after `deliveryDate`. `available_ms` is when a row became
knowable (kline close; metrics stamp + METRICS_STAMP_LAG_MS). Reads no returns."""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl

ARCHIVE = "https://data.binance.vision/data/futures/um"
REST = "https://fapi.binance.com"

#: The archive stamps a metrics snapshot five minutes early (REST 01:00 = archive 00:55,
#: measured 13/13 vs openInterestHist). Using the raw stamp voided RESULT-008.
METRICS_STAMP_LAG_MS = 300_000

UNIVERSE_SIZE = 100          # FEASIBILITY-008's breadth row, PREREG-008
RANKING_DAYS = 30
MIN_HISTORY_DAYS = 30
#: "Still trading" at month start: traded on one of the last three days.
ALIVE_DAYS = 3

KLINE_SCHEMA = {
    "open_ms": pl.Int64, "available_ms": pl.Int64, "open": pl.Float64,
    "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "quote_volume": pl.Float64, "trades": pl.Int64,
    "taker_buy_volume": pl.Float64, "taker_buy_quote": pl.Float64,
}
METRICS_SCHEMA = {
    "available_ms": pl.Int64, "open_interest": pl.Float64,
    "open_interest_value": pl.Float64,
    "toptrader_account_ratio": pl.Float64, "toptrader_position_ratio": pl.Float64,
    "global_account_ratio": pl.Float64, "taker_buy_sell_ratio": pl.Float64,
}


# -- parsing ---------------------------------------------------------------------


def parse_klines(text: str) -> pl.DataFrame:
    """Klines and premium-index klines share a layout; header rows occur in some files."""
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]
    return pl.DataFrame(
        [(int(r[0]), int(r[6]) + 1, float(r[1]), float(r[2]), float(r[3]),
          float(r[4]), float(r[5]), float(r[7]), int(r[8]), float(r[9]),
          float(r[10])) for r in rows],
        schema=KLINE_SCHEMA, orient="row")


def parse_metrics(text: str) -> pl.DataFrame:
    """Blank fields are None, not zero. `available_ms` = `create_time` + METRICS_STAMP_LAG_MS."""
    def number(value: str) -> float | None:
        return float(value) if value.strip() else None

    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0][:1].isdigit():
            continue
        stamp = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        out.append((int(stamp.timestamp() * 1000) + METRICS_STAMP_LAG_MS,
                    number(row[2]), number(row[3]),
                    number(row[4]), number(row[5]), number(row[6]), number(row[7])))
    return pl.DataFrame(out, schema=METRICS_SCHEMA, orient="row")


def cut(frame: pl.DataFrame, start: datetime, end: datetime,
        delisted_at: datetime | None) -> pl.DataFrame:
    """Rows knowable inside the window and before delisting, deduplicated (daily and
    monthly files can overlap)."""
    stop = min(end, delisted_at) if delisted_at else end
    return (frame.filter((pl.col("available_ms") > _ms(start))
                         & (pl.col("available_ms") <= _ms(stop)))
            .unique(subset="available_ms", keep="first").sort("available_ms"))


# -- fetching --------------------------------------------------------------------


def fetch_text(client: httpx.Client, url: str, attempts: int = 4) -> str | None:
    """A file's CSV, or None on 404 (not listed). Other failures retry, then raise."""
    for attempt in range(attempts):
        try:
            response = client.get(url)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if response.status_code == 404:
            return None
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
                return bundle.read(bundle.namelist()[0]).decode()
        if attempt == attempts - 1:
            response.raise_for_status()
        time.sleep(2 ** attempt)
    return None


def month_of_klines(client: httpx.Client, kind: str, symbol: str, month: date,
                    until: date) -> pl.DataFrame:
    """One month of 5-minute bars: the monthly file, or the daily ones if the
    archive has not closed the month yet."""
    text = fetch_text(client, f"{ARCHIVE}/monthly/{kind}/{symbol}/5m/"
                              f"{symbol}-5m-{month:%Y-%m}.zip")
    if text is not None:
        return parse_klines(text)
    frames = [parse_klines(t) for day in days_of(month, until)
              if (t := fetch_text(client, f"{ARCHIVE}/daily/{kind}/{symbol}/5m/"
                                          f"{symbol}-5m-{day:%Y-%m-%d}.zip"))]
    return pl.concat(frames) if frames else pl.DataFrame(schema=KLINE_SCHEMA)


def month_of_metrics(client: httpx.Client, symbol: str, month: date,
                     until: date) -> pl.DataFrame:
    frames = [parse_metrics(t) for day in days_of(month, until)
              if (t := fetch_text(client, f"{ARCHIVE}/daily/metrics/{symbol}/"
                                          f"{symbol}-metrics-{day:%Y-%m-%d}.zip"))]
    return pl.concat(frames) if frames else pl.DataFrame(schema=METRICS_SCHEMA)


def days_of(month: date, until: date) -> list[date]:
    out, day = [], month
    while day.month == month.month and day <= until:
        out.append(day)
        day += timedelta(days=1)
    return out


# -- the universe ----------------------------------------------------------------


def daily_quote_volume(client: httpx.Client, symbol: str, start: datetime,
                       end: datetime, delisted_at: datetime | None) -> pl.DataFrame:
    """Quote volume per traded UTC day, cut at delisting. Zero-volume days dropped
    (see `pit_panel.live_days`)."""
    rows = client.get(f"{REST}/fapi/v1/klines", params={
        "symbol": symbol, "interval": "1d", "startTime": _ms(start),
        "endTime": _ms(end), "limit": 1500}).json()
    if not isinstance(rows, list):
        # -1121: REST forgot the symbol (FINDING-011); the monthly archive has it.
        rows = []
        month = date(start.year, start.month, 1)
        while month <= end.date():
            text = fetch_text(client, f"{ARCHIVE}/monthly/klines/{symbol}/1d/"
                                      f"{symbol}-1d-{month:%Y-%m}.zip")
            if text:
                rows += [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    cutoff = delisted_at.date() if delisted_at else None
    kept = [(datetime.fromtimestamp(int(r[0]) / 1000, UTC).date(), float(r[7]))
            for r in rows if float(r[5]) > 0]
    if cutoff:
        kept = [(d, v) for d, v in kept if d <= cutoff]
    return pl.DataFrame(kept, schema={"day": pl.Date, "quote_volume": pl.Float64},
                        orient="row")


def monthly_universe(volumes: pl.DataFrame, months: list[date],
                     size: int = UNIVERSE_SIZE) -> pl.DataFrame:
    """Per month, the `size` assets with highest quote volume over the prior 30 days.

    Eligible: >= 30 traded days before the month and a traded day in the last `ALIVE_DAYS`.
    Mid-month delistings stay members (unknown on the 1st)."""
    out = []
    for month in months:
        before = volumes.filter(pl.col("day") < month)
        window = before.filter(pl.col("day") >= month - timedelta(days=RANKING_DAYS))
        history = before.group_by("asset").agg(pl.len().alias("days"),
                                                pl.col("day").max().alias("last"))
        eligible = history.filter((pl.col("days") >= MIN_HISTORY_DAYS)
                                  & (pl.col("last") >= month - timedelta(days=ALIVE_DAYS)))
        ranked = (window.join(eligible.select("asset"), on="asset")
                  .group_by("asset").agg(pl.col("quote_volume").sum().alias("volume_30d"))
                  .sort(["volume_30d", "asset"], descending=[True, False]).head(size))
        out.append(ranked.with_columns(pl.lit(month).alias("month"),
                                       pl.int_range(1, pl.len() + 1).alias("rank")))
    return pl.concat(out).select("month", "rank", "asset", "volume_30d")


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)
