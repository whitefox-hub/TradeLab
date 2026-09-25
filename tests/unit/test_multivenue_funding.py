"""Cross-venue funding normalisation. Plan.md §0.4 hypothesis 3, §4.2.

The collector's job is not fetching — it is making five venues comparable. Each
test here covers one way they are not, and each of those would corrupt a spread
silently rather than loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from tradelab.collectors.multivenue.funding import (
    SPECS,
    VENUES,
    spread,
    to_daily,
    utc_window,
)

DAY = 86_400_000
BASE = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)


@pytest.mark.fast
def test_every_venue_has_a_symbol_convention() -> None:
    """`BTCUSDT`, `BTC-USDT-SWAP`, `BTC_USDT`. Joining on a venue's own spelling
    silently produces an empty panel rather than an error."""
    spellings = {name: spec.symbol("BTC") for name, spec in SPECS.items()}
    assert spellings["binance"] == "BTCUSDT"
    assert spellings["okx"] == "BTC-USDT-SWAP"
    assert spellings["gate"] == "BTC_USDT"
    assert set(SPECS) == set(VENUES)


@pytest.mark.fast
def test_daily_aggregation_makes_different_schedules_comparable() -> None:
    """A venue settling every 4h collects twice as often as one on 8h.

    Comparing per settlement would compare half a day against a whole one and
    report a spread that is an artefact of the schedule, not of the market.
    """
    eight_hourly = [(BASE + h * 3_600_000, 0.0001) for h in (0, 8, 16)]
    four_hourly = [(BASE + h * 3_600_000, 0.00005) for h in (0, 4, 8, 12, 16, 20)]

    slow, fast = to_daily(eight_hourly), to_daily(four_hourly)
    assert slow["n"][0] == 3
    assert fast["n"][0] == 6
    # Both collected the same amount over the day, which is the point.
    assert slow["rate"][0] == pytest.approx(fast["rate"][0])


@pytest.mark.fast
def test_settlements_are_bucketed_by_utc_day_not_by_arrival() -> None:
    rows = [(BASE, 0.0001), (BASE + DAY, 0.0002), (BASE + DAY + 3_600_000, 0.0003)]
    daily = to_daily(rows)
    assert daily.height == 2
    assert daily["rate"][1] == pytest.approx(0.0005)


@pytest.mark.fast
def test_an_empty_history_gives_an_empty_frame_not_an_error() -> None:
    """A venue that never listed the asset is a narrower panel, not a failure."""
    assert to_daily([]).height == 0
    assert to_daily([]).columns == ["day", "rate", "n"]


def _daily(days: int, rate: float, start: int = BASE) -> pl.DataFrame:
    return to_daily([(start + d * DAY, rate) for d in range(days)])


@pytest.mark.fast
def test_the_spread_joins_on_days_both_venues_quoted() -> None:
    result = spread(_daily(60, 0.0003), _daily(60, 0.0001), min_days=30)
    assert result is not None
    assert result.height == 60
    assert result["spread"][0] == pytest.approx(0.0002)


@pytest.mark.fast
def test_a_missing_day_is_an_absence_not_a_zero_spread() -> None:
    """Filling gaps with zero pulls every statistic toward "no dispersion",
    which is the conclusion the study is trying to test."""
    complete = _daily(60, 0.0003)
    partial = _daily(30, 0.0001)          # the second venue quotes half the window
    result = spread(complete, partial, min_days=10)

    assert result is not None
    assert result.height == 30, "the join invented days one venue never quoted"
    assert (result["spread"] != 0).all()


@pytest.mark.fast
def test_too_little_overlap_returns_nothing_rather_than_a_thin_estimate() -> None:
    """A per-asset bias measured on a handful of days is noise, and returning it
    lets it into the cross-section looking like a measurement."""
    assert spread(_daily(60, 0.0003), _daily(5, 0.0001), min_days=30) is None


@pytest.mark.fast
def test_the_spread_keeps_both_settlement_counts() -> None:
    """So a spread computed across mismatched schedules can be found later
    rather than being indistinguishable from an ordinary one."""
    result = spread(_daily(60, 0.0003), _daily(60, 0.0001), min_days=30)
    assert result is not None
    assert {"n_a", "n_b"} <= set(result.columns)


@pytest.mark.fast
def test_the_window_is_closed_and_ends_now() -> None:
    start, end = utc_window(730)
    assert (end - start) == timedelta(days=730)
    assert end.tzinfo is UTC
    assert end.microsecond == 0
