"""The intraday data set for PREREG-008. Plan.md §4.3, §4.4."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl
import pytest

from tradelab.collectors.binance.intraday import (
    cut,
    fetch_text,
    month_of_klines,
    monthly_universe,
    parse_klines,
    parse_metrics,
)

HEADER = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,"
          "taker_buy_volume,taker_buy_quote_volume,ignore\n")
BAR = "1775001600000,0.26,0.263,0.261,0.262,100002,1775001899999,26194.7,542,66988,17549.1,0\n"


def _zipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("x.csv", text)
    return buffer.getvalue()


@pytest.mark.fast
def test_a_kline_is_available_at_its_close_not_its_open() -> None:
    """§4.3. A bar stamped 00:00 is not known until 00:05."""
    bars = parse_klines(HEADER + BAR)
    assert bars["open_ms"][0] == 1775001600000
    assert bars["available_ms"][0] == 1775001900000
    assert bars["taker_buy_quote"][0] == pytest.approx(17549.1)


@pytest.mark.fast
def test_old_files_without_a_header_parse_the_same() -> None:
    assert parse_klines(BAR).equals(parse_klines(HEADER + BAR))


@pytest.mark.fast
def test_a_blank_ratio_is_absent_not_zero() -> None:
    """ALPACA after delisting, as the archive serves it."""
    text = ("create_time,symbol,sum_open_interest,sum_open_interest_value,"
            "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
            "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
            "2025-05-10 02:30:00,ALPACAUSDT,0E-16,0E-16,,0.20866200,,\n")
    row = parse_metrics(text).row(0, named=True)
    assert row["open_interest"] == 0.0
    assert row["toptrader_account_ratio"] is None
    assert row["toptrader_position_ratio"] == pytest.approx(0.208662)
    assert row["available_ms"] == int(datetime(2025, 5, 10, 2, 30, tzinfo=UTC).timestamp() * 1000)


@pytest.mark.fast
def test_nothing_survives_delisting_and_overlaps_are_counted_once() -> None:
    start = datetime(2025, 4, 1, tzinfo=UTC)
    stamps = [int((start + timedelta(minutes=5 * i)).timestamp() * 1000) for i in range(1, 7)]
    frame = pl.DataFrame({"available_ms": stamps + stamps[:2], "x": list(range(8))})
    delisted = start + timedelta(minutes=15)
    kept = cut(frame, start, start + timedelta(days=1), delisted)
    assert kept["available_ms"].to_list() == stamps[:3]


@pytest.mark.fast
def test_a_month_the_archive_has_not_closed_is_read_day_by_day() -> None:
    """Measured: the monthly file for 2026-09 is a 404 in September."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if "/monthly/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=_zipped(HEADER + BAR))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bars = month_of_klines(client, "klines", "RAVEUSDT", date(2026, 9, 1), date(2026, 9, 3))
    assert bars.height == 3
    assert sum("/daily/" in path for path in asked) == 3


@pytest.mark.fast
def test_a_file_that_fails_to_arrive_is_not_a_file_that_does_not_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_text(client, "https://data.binance.vision/x.zip", attempts=1)
    assert fetch_text(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(404))), "https://data.binance.vision/x.zip") is None


def _volumes(rows: dict[str, tuple[date, date, float]]) -> pl.DataFrame:
    out = []
    for asset, (first, last, volume) in rows.items():
        day = first
        while day <= last:
            out.append((asset, day, volume))
            day += timedelta(days=1)
    return pl.DataFrame(out, schema={"asset": pl.Utf8, "day": pl.Date,
                                     "quote_volume": pl.Float64}, orient="row")


@pytest.mark.fast
def test_the_universe_is_ranked_on_the_past_and_requires_a_history() -> None:
    month = date(2025, 5, 1)
    volumes = _volumes({
        "BIG": (date(2025, 1, 1), date(2025, 6, 1), 1e9),
        "MID": (date(2025, 1, 1), date(2025, 6, 1), 1e8),
        "NEW": (date(2025, 4, 20), date(2025, 6, 1), 1e12),     # 11 days: too young
        "DEAD": (date(2025, 1, 1), date(2025, 4, 10), 1e11),    # gone before the month
    })
    ranked = monthly_universe(volumes, [month], size=5)
    assert ranked["asset"].to_list() == ["BIG", "MID"]
    assert ranked["rank"].to_list() == [1, 2]


@pytest.mark.fast
def test_the_future_does_not_rank_the_present() -> None:
    """Volume in May cannot put an asset in May's universe."""
    volumes = _volumes({
        "A": (date(2025, 1, 1), date(2025, 6, 1), 1e8),
        "B": (date(2025, 1, 1), date(2025, 6, 1), 1e7),
    })
    surge = pl.DataFrame({"asset": ["B"], "day": [date(2025, 5, 2)],
                          "quote_volume": [1e15]})
    ranked = monthly_universe(pl.concat([volumes, surge]), [date(2025, 5, 1)], size=1)
    assert ranked["asset"].to_list() == ["A"]


@pytest.mark.fast
def test_one_quiet_day_before_the_month_is_not_a_delisting() -> None:
    volumes = _volumes({"A": (date(2025, 1, 1), date(2025, 4, 29), 1e8)})
    assert monthly_universe(volumes, [date(2025, 5, 1)])["asset"].to_list() == ["A"]
