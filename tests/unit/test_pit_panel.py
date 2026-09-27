"""The survivorship-neutral panel. Plan.md §4.4."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime

import httpx
import pytest

from tradelab.collectors.binance import pit_panel
from tradelab.collectors.binance.pit_panel import (
    Contract,
    Refused,
    archive_bars,
    live_days,
    months_between,
    rest_funding,
    universe,
)

DAY = 86_400_000
START = datetime(2025, 4, 28, tzinfo=UTC)


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


@pytest.mark.fast
def test_a_frozen_price_after_delisting_is_not_a_price() -> None:
    """ALPACA, as the venue serves it: its last traded day, then the settlement
    price repeated at zero volume for as long as anyone asks."""
    bars = [(_ms(date(2025, 4, 29)), 0.19187, 9.7e9),
            (_ms(date(2025, 4, 30)), 1.19, 2.6e9),
            (_ms(date(2025, 5, 1)), 1.19, 0.0),
            (_ms(date(2025, 5, 2)), 1.19, 0.0)]
    days = live_days(bars, delisted_at=None)["day"].to_list()
    assert days == [date(2025, 4, 29), date(2025, 4, 30)]


@pytest.mark.fast
def test_nothing_after_the_delisting_instant_even_if_it_traded() -> None:
    bars = [(_ms(date(2025, 4, 29)) + i * DAY, 1.0, 5.0) for i in range(4)]
    kept = live_days(bars, delisted_at=datetime(2025, 4, 30, 9, tzinfo=UTC))
    assert kept["day"].max() == date(2025, 4, 30)


@pytest.mark.fast
def test_a_refused_funding_page_raises_rather_than_ending_the_series() -> None:
    """The older collector broke out of its loop on any non-list answer, so a
    rate-limited asset came back short and looked complete."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -1003, "msg": "Too many requests"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(Refused, match="-1003"):
        rest_funding(client, "RAVEUSDT", START, datetime(2025, 5, 1, tzinfo=UTC), pace=0)


@pytest.mark.fast
def test_funding_pages_forward_until_the_window_is_covered() -> None:
    pages = [[{"fundingTime": _ms(date(2025, 4, 28)) + i * 8 * 3_600_000,
               "fundingRate": "0.0001"} for i in range(3)], []]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages.pop(0))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = rest_funding(client, "X", START, datetime(2025, 5, 1, tzinfo=UTC), pace=0)
    assert len(rows) == 3


def _zipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("x.csv", text)
    return buffer.getvalue()


@pytest.mark.fast
def test_the_archive_is_read_with_or_without_a_header_and_a_missing_month_is_empty() -> None:
    header = "open_time,open,high,low,close,volume,close_time\n"
    files = {
        "2025-01": _zipped(header + f"{_ms(date(2025, 1, 1))},1,1,1,1.5,10,0\n"),
        "2025-02": _zipped(f"{_ms(date(2025, 2, 1))},1,1,1,2.5,20,0\n"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        month = request.url.path.rsplit("-", 2)[-2] + "-" + \
            request.url.path.rsplit("-", 1)[-1].removesuffix(".zip")
        body = files.get(month)
        return httpx.Response(200, content=body) if body else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bars = archive_bars(client, "SXPUSDT",
                        [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)])
    assert [(close, volume) for _, close, volume in bars] == [(1.5, 10.0), (2.5, 20.0)]


@pytest.mark.fast
def test_months_cover_the_window_across_a_year_end() -> None:
    months = months_between(datetime(2024, 11, 25, tzinfo=UTC),
                            datetime(2025, 2, 3, tzinfo=UTC))
    assert months == [date(2024, 11, 1), date(2024, 12, 1), date(2025, 1, 1),
                      date(2025, 2, 1)]


@pytest.mark.fast
def test_the_universe_keeps_the_settling_and_drops_tradfi(monkeypatch) -> None:
    symbols = [
        {"symbol": "ADAUSDT", "status": "TRADING", "contractType": "PERPETUAL",
         "underlyingType": "COIN", "quoteAsset": "USDT", "deliveryDate": 4133404800000},
        {"symbol": "ALPACAUSDT", "status": "SETTLING", "contractType": "PERPETUAL",
         "underlyingType": "COIN", "quoteAsset": "USDT", "deliveryDate": 1746003600000},
        {"symbol": "AMZNUSDT", "status": "TRADING", "contractType": "TRADIFI_PERPETUAL",
         "underlyingType": "EQUITY", "quoteAsset": "USDT", "deliveryDate": 0},
        {"symbol": "BTCDOMUSDT", "status": "TRADING", "contractType": "PERPETUAL",
         "underlyingType": "INDEX", "quoteAsset": "USDT", "deliveryDate": 0},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbols": symbols})

    monkeypatch.setattr(pit_panel, "archived_symbols",
                        lambda client: ["ADAUSDT", "AMZNUSDT", "SXPUSDT"])
    found = universe(httpx.Client(transport=httpx.MockTransport(handler)))

    assert [(c.symbol, c.status) for c in found] == [
        ("ADAUSDT", "TRADING"), ("ALPACAUSDT", "SETTLING"), ("SXPUSDT", "ARCHIVED")]
    alpaca = next(c for c in found if c.symbol == "ALPACAUSDT")
    assert alpaca.delisted_at == datetime(2025, 4, 30, 9, tzinfo=UTC)
    assert Contract("1000PEPEUSDT", "TRADING", None).asset == "1000PEPE"
