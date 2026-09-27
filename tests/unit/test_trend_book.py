"""The trend book's rules. research/PREREG-009-trend-book.md."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from tradelab.strategy import trend_book as tb
from tradelab.strategy.trend_book import Bar, Setting, State

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ATR = 0.03


def _bar(close: float, at: datetime = T0, high: float | None = None,
         low: float | None = None, open_: float | None = None) -> Bar:
    return Bar(at, open_ if open_ is not None else close,
               high if high is not None else close, low if low is not None else close, close)


def _operator(trend: int = 8, at: datetime = T0) -> Setting:
    return Setting(trend, "manual", at)


@pytest.mark.fast
def test_an_operator_setting_sizes_the_book_on_the_slider_scale() -> None:
    d = tb.step(State(), _operator(8), _bar(1.0), ATR, signal=-1)
    assert d.state.guide == "operator"
    assert d.state.exposure == pytest.approx(0.30)
    assert d.state.entry == 1.0 and d.state.atr == ATR
    assert tb.step(State(), _operator(1), _bar(1.0), ATR, 1).state.exposure == pytest.approx(-0.5)


@pytest.mark.fast
def test_the_operators_view_has_no_expiry_by_time() -> None:
    state = tb.step(State(), _operator(8), _bar(1.0), ATR, 0).state
    later = tb.step(state, _operator(8), _bar(1.01, T0 + timedelta(days=40)), ATR, -1)
    assert not later.changed and later.state.exposure == pytest.approx(0.30)


@pytest.mark.fast
def test_take_profit_at_two_atr_then_flat_until_the_operator_changes_the_trend() -> None:
    state = tb.step(State(), _operator(8), _bar(1.0), ATR, 0).state
    hit = tb.step(state, _operator(8), _bar(1.05, high=1.061), ATR, 0)
    assert hit.reason == "take profit" and hit.state.exposure == 0.0
    assert tb.exit_level(state, _bar(1.05, high=1.061), "take profit") == pytest.approx(1.06)
    still = tb.step(hit.state, _operator(8), _bar(1.2), ATR, 0)
    assert still.state.exposure == 0.0, "the same view does not re-enter by itself"
    again = tb.step(hit.state, _operator(7, T0 + timedelta(hours=3)), _bar(1.2), ATR, 0)
    assert again.state.exposure == pytest.approx(0.20)


@pytest.mark.fast
def test_the_stop_wins_a_bar_that_touches_both_and_a_gap_exits_at_the_worst() -> None:
    state = tb.step(State(), _operator(8), _bar(1.0), ATR, 0).state
    both = _bar(1.0, high=1.07, low=0.96)
    assert tb.step(state, _operator(8), both, ATR, 0).reason == "stop"
    gap = _bar(0.95, open_=0.955, low=0.94, high=0.96)
    assert tb.exit_level(state, gap, "stop") == pytest.approx(0.94)
    assert tb.exit_level(state, _bar(0.975, low=0.969, open_=0.99), "stop") == pytest.approx(0.97)


@pytest.mark.fast
def test_a_short_view_has_its_barriers_mirrored() -> None:
    state = tb.step(State(), _operator(2), _bar(1.0), ATR, 0).state
    assert state.exposure < 0
    assert tb.step(state, _operator(2), _bar(1.0, low=0.939), ATR, 0).reason == "take profit"
    assert tb.step(state, _operator(2), _bar(1.0, high=1.031), ATR, 0).reason == "stop"


@pytest.mark.fast
def test_at_five_the_algorithm_guides_once_a_day_at_twenty_percent() -> None:
    five = Setting(5, "manual", T0)
    first = tb.step(State(setting_at=T0), five, _bar(1.0), ATR, signal=1)
    assert first.state.guide == "algorithm" and first.state.exposure == pytest.approx(0.20)
    same_day = tb.step(first.state, five, _bar(1.0, T0 + timedelta(hours=2)), ATR, signal=-1)
    assert same_day.state.exposure == pytest.approx(0.20), "decides once per UTC day"
    next_day = tb.step(first.state, five, _bar(1.0, T0 + timedelta(days=1)), ATR, signal=-1)
    assert next_day.state.exposure == pytest.approx(-0.20)


@pytest.mark.fast
def test_leaving_five_hands_the_book_to_the_operator_and_returning_hands_it_back() -> None:
    algo = tb.step(State(setting_at=T0), Setting(5, "manual", T0), _bar(1.0), ATR, 1).state
    operator = tb.step(algo, _operator(9, T0 + timedelta(minutes=5)), _bar(1.0), ATR, 1).state
    assert operator.guide == "operator" and operator.exposure == pytest.approx(0.40)
    back = tb.step(operator, Setting(5, "manual", T0 + timedelta(minutes=10)),
                   _bar(1.0, T0 + timedelta(minutes=10)), ATR, -1).state
    assert back.guide == "algorithm" and back.exposure == pytest.approx(-0.20)


@pytest.mark.fast
def test_each_decision_records_the_setting_that_caused_it() -> None:
    d = tb.step(State(), Setting(8, "adopted", T0), _bar(1.0), ATR, 0)
    assert (d.state.trend, d.state.source) == (8, "adopted")


@pytest.mark.fast
def test_atr_and_momentum_use_closed_days_only() -> None:
    close = np.array([100.0 + i for i in range(16)])
    high, low = close + 2, close - 2
    assert tb.daily_atr(high, low, close) == pytest.approx(4.0 / 115.0)
    assert tb.momentum(close) == 1
    assert tb.momentum(close[::-1]) == -1
    assert tb.momentum(close[:5]) == 0
