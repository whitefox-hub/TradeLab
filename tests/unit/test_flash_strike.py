"""Flash Strike's rules, one at a time. research/PREREG-008-flash-strike.md."""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.strategy import flash_strike as fs
from tradelab.strategy.flash_strike import Fill, Series

TICK = 0.01


def _series(n: int = 400, seed: int = 7, **overrides: np.ndarray) -> Series:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.05, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    spread = np.abs(rng.normal(0, 0.03, n)) + 0.02
    arrays = {
        "close_ms": np.arange(n, dtype=np.int64) * 300_000,
        "open": open_, "high": np.maximum(open_, close) + spread,
        "low": np.minimum(open_, close) - spread, "close": close,
        "volume": np.full(n, 1000.0), "quote_volume": close * 1000.0,
        "trades": np.full(n, 50), "taker_buy_quote": close * 500.0,
        "premium": np.zeros(n), "open_interest": np.full(n, 1e6),
        "toptrader_ratio": np.ones(n), "tick": np.full(n, TICK),
    }
    arrays.update(overrides)
    return Series(**arrays)


def _flat(n: int = 40, price: float = 100.0, **overrides: np.ndarray) -> Series:
    base = np.full(n, price)
    arrays = {
        "close_ms": np.arange(n, dtype=np.int64) * 300_000,
        "open": base.copy(), "high": base + 0.05, "low": base - 0.05, "close": base.copy(),
        "volume": np.full(n, 1000.0), "quote_volume": base * 1000.0,
        "trades": np.full(n, 50), "taker_buy_quote": base * 600.0,
        "premium": np.zeros(n), "open_interest": np.full(n, 1e6),
        "toptrader_ratio": np.ones(n), "tick": np.full(n, TICK),
    }
    arrays.update(overrides)
    return Series(**arrays)


@pytest.mark.fast
def test_a_bar_is_never_inside_its_own_reference() -> None:
    values = np.arange(10, dtype=float)
    mean = fs.trailing_mean(values, 4)
    assert mean[4] == pytest.approx(1.5)            # bars 0-3, not 1-4
    assert np.isnan(mean[3])


@pytest.mark.fast
def test_a_planted_shock_is_found_and_the_threshold_is_the_assets_own(monkeypatch) -> None:
    monkeypatch.setattr(fs, "QUANTILE_BARS", 200)
    monkeypatch.setattr(fs, "ATR_BARS", 50)
    s = _series()
    close, open_, high = s.close.copy(), s.open.copy(), s.high.copy()
    close[300] = open_[300] + 5.0
    high[300] = close[300] + 0.1
    events, sides, _ = fs.shocks(_series(close=close, open=open_, high=high,
                                         low=s.low))
    assert 300 in events
    assert sides[list(events).index(300)] == 1


@pytest.mark.fast
def test_the_future_does_not_move_a_past_shock(monkeypatch) -> None:
    monkeypatch.setattr(fs, "QUANTILE_BARS", 200)
    monkeypatch.setattr(fs, "ATR_BARS", 50)
    s = _series()
    before = fs.shocks(s)[0]
    close = s.close.copy()
    close[350:] += 30.0
    after = fs.shocks(_series(close=close, open=s.open, high=np.maximum(s.high, close),
                              low=s.low))[0]
    assert list(before[before < 350]) == list(after[after < 350])


def _shocked(n: int = 40, **overrides: np.ndarray) -> Series:
    """An up shock at bar 10 from 100 to 104, VWAP 102."""
    s = _flat(n)
    arrays = {k: getattr(s, k).copy() for k in Series.__dataclass_fields__}
    arrays["open"][10], arrays["close"][10] = 100.0, 104.0
    arrays["high"][10], arrays["low"][10] = 104.1, 99.9
    arrays["quote_volume"][10], arrays["volume"][10] = 102_000.0, 1000.0
    arrays["taker_buy_quote"][10] = 80_000.0
    for j in range(11, n):
        arrays["open"][j] = arrays["close"][j] = 104.0
        arrays["high"][j], arrays["low"][j] = 104.05, 103.95
    arrays.update(overrides)
    return Series(**arrays)


def _with(s: Series, **changes) -> Series:
    arrays = {k: getattr(s, k).copy() for k in Series.__dataclass_fields__}
    for key, (index, value) in changes.items():
        arrays[key][index] = value
    return Series(**arrays)


@pytest.mark.fast
def test_touching_the_limit_is_not_a_fill_trading_through_it_is() -> None:
    touch = _with(_shocked(), low=(12, 102.0))
    assert fs.fill(touch, 10, 1, atr=1.0) is None
    through = _with(_shocked(), low=(12, 101.99))
    entry = fs.fill(through, 10, 1, atr=1.0)
    assert entry is not None and entry.fill == 12 and entry.price == pytest.approx(102.0)


@pytest.mark.fast
def test_the_order_expires_after_thirty_minutes() -> None:
    late = _with(_shocked(), low=(17, 101.0))           # seven bars after the shock
    assert fs.fill(late, 10, 1, atr=1.0) is None


@pytest.mark.fast
def test_persistence_refuses_flow_that_turned_and_open_interest_that_fell() -> None:
    s = _with(_shocked(), low=(12, 101.9))
    turned = _with(s, taker_buy_quote=(11, 0.0))
    turned = _with(turned, taker_buy_quote=(10, 10_000.0))
    assert fs.fill(turned, 10, 1, atr=1.0) is None
    fell = _with(s, open_interest=(11, 0.9e6))
    assert fs.fill(fell, 10, 1, atr=1.0) is None
    assert fs.fill(s, 10, 1, atr=1.0) is not None


def _entry(fill_bar: int = 12, side: int = 1, price: float = 102.0) -> Fill:
    return Fill(shock=10, fill=fill_bar, side=side, price=price, atr=1.0)


NO_FUNDING = np.zeros(40)


@pytest.mark.fast
def test_the_stop_wins_a_bar_that_touches_both() -> None:
    s = _with(_flat(40, 102.0), high=(14, 104.0), low=(14, 101.0))
    out = fs.exit_trade(s, _entry(), sl_mult=0.5, tp_mult=1.0, hold_minutes=120,
                        funding_per_bar=NO_FUNDING)
    assert out.reason == -1
    assert out.net == pytest.approx(-0.5 / 102.0 - fs.MAKER - fs.TAKER)


@pytest.mark.fast
def test_a_gap_through_the_stop_fills_at_the_worst_price() -> None:
    s = _with(_with(_flat(40, 102.0), open=(14, 100.0)), low=(14, 99.0))
    out = fs.exit_trade(s, _entry(), sl_mult=0.5, tp_mult=1.0, hold_minutes=120,
                        funding_per_bar=NO_FUNDING)
    assert out.reason == -1
    assert out.net == pytest.approx((99.0 - 102.0) / 102.0 - fs.MAKER - fs.TAKER)


@pytest.mark.fast
def test_the_take_profit_needs_a_trade_through_and_never_fires_in_the_fill_bar() -> None:
    at_target = _with(_flat(40, 102.0), high=(12, 103.0))
    out = fs.exit_trade(at_target, _entry(), sl_mult=1.0, tp_mult=1.0, hold_minutes=30,
                        funding_per_bar=NO_FUNDING)
    assert out.reason == 0, "a target touched in the fill bar is not taken"

    touched = _with(_flat(40, 102.0), high=(14, 103.0))
    assert fs.exit_trade(touched, _entry(), 1.0, 1.0, 30, NO_FUNDING).reason == 0
    through = _with(_flat(40, 102.0), high=(14, 103.02))
    won = fs.exit_trade(through, _entry(), 1.0, 1.0, 30, NO_FUNDING)
    assert won.reason == 1
    assert won.net == pytest.approx(1.0 / 102.0 - 2 * fs.MAKER)


@pytest.mark.fast
def test_time_ends_the_trade_at_the_close_and_funding_is_charged_to_the_payer() -> None:
    s = _flat(40, 102.0)
    funding = np.zeros(40)
    funding[15] = 0.001
    long_ = fs.exit_trade(s, _entry(), 1.0, 2.0, 30, funding)
    short = fs.exit_trade(s, _entry(side=-1), 1.0, 2.0, 30, funding)
    assert long_.reason == 0 and long_.exit_bar == 18
    assert long_.net == pytest.approx(-fs.MAKER - fs.TAKER - 0.001)
    assert short.net == pytest.approx(-fs.MAKER - fs.TAKER + 0.001)


@pytest.mark.fast
def test_the_twelve_registered_configurations() -> None:
    assert len(fs.CONFIGS) == 12
    assert set(fs.CONFIGS) == {(sl, tp, h) for sl in (0.5, 1.0)
                               for tp in (1.0, 1.5, 2.0) for h in (30, 120)}


@pytest.mark.fast
def test_the_tick_is_read_from_the_prices_of_the_month() -> None:
    assert fs.infer_tick(np.array([1.00, 1.03, 1.01, 1.01, 1.05])) == pytest.approx(0.01)


@pytest.mark.fast
def test_the_features_at_a_fill_do_not_see_the_bars_after_it() -> None:
    s = _series(600)
    entry = Fill(shock=500, fill=503, side=1, price=float(s.close[500]), atr=0.1)
    before = fs.features(s, fs.prepare(s), entry, 0.0, 0.0)
    arrays = {k: getattr(s, k).copy() for k in Series.__dataclass_fields__}
    for key in ("close", "open", "high", "low", "quote_volume", "taker_buy_quote",
                "open_interest", "premium", "toptrader_ratio"):
        arrays[key][504:] *= 3.0
    later = Series(**arrays)
    after = fs.features(later, fs.prepare(later), entry, 0.0, 0.0)
    assert before == pytest.approx(after, nan_ok=True)
