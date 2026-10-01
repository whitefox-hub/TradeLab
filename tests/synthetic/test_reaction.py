"""Event reaction measurement. Plan.md §11.3.

Built on data where the reaction is known by construction, because "the market
moved after the news" is exactly the claim that looks true whatever the code
does. Each test puts a reaction of a stated size in and checks the measurement
recovers it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from tradelab.events.reaction import (
    HORIZONS,
    BenchmarkKind,
    Window,
    estimate_beta,
    leakage_flag,
    measure,
    scored_against,
)

BARS_PER_DAY = 24 * 60
NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _window(
    *, n: int = 60 * 24 * 40, event_at: int = 60 * 24 * 35, beta: float = 1.0,
    jump: float = 0.0, jump_bars: int = 60, drift_before: float = 0.0,
    seed: int = 7,
) -> Window:
    """Market noise, an asset with a known beta, and a reaction of known size."""
    rng = np.random.default_rng(seed)
    market = rng.normal(0, 0.0004, n)
    idiosyncratic = rng.normal(0, 0.0003, n)
    asset = beta * market + idiosyncratic

    if jump:
        asset[event_at:event_at + jump_bars] += jump / jump_bars
    if drift_before:
        asset[event_at - jump_bars:event_at] += drift_before / jump_bars

    return Window(times=np.arange(n), asset_returns=asset,
                  market_returns=market, event_index=event_at)


@pytest.mark.fast
def test_beta_is_recovered_from_the_pre_event_window() -> None:
    window = _window(beta=1.8)
    beta, sigma = estimate_beta(window.asset_returns, window.market_returns,
                                window.event_index, BARS_PER_DAY)
    assert beta == pytest.approx(1.8, abs=0.15)
    assert sigma > 0


@pytest.mark.fast
def test_the_estimation_window_never_touches_the_event() -> None:
    """A beta fitted through the event absorbs the thing being measured, and the
    abnormal return then comes out near zero however large the move was."""
    contaminated = _window(beta=1.0, jump=0.30, jump_bars=60)
    beta, _ = estimate_beta(contaminated.asset_returns, contaminated.market_returns,
                            contaminated.event_index, BARS_PER_DAY)
    clean = _window(beta=1.0, jump=0.0)
    clean_beta, _ = estimate_beta(clean.asset_returns, clean.market_returns,
                                  clean.event_index, BARS_PER_DAY)
    assert beta == pytest.approx(clean_beta, abs=0.05), (
        "the jump leaked into the estimation window"
    )


@pytest.mark.fast
def test_a_known_jump_is_recovered_as_abnormal_return() -> None:
    """A 10% move over the first hour should come back as a 10% CAR at h=60."""
    window = _window(jump=0.10, jump_bars=60)
    at_hour = next(r for r in measure(window, "X", BARS_PER_DAY)
                   if r.horizon_minutes == 60)
    assert at_hour.car == pytest.approx(0.10, abs=0.01)
    assert at_hour.z > 3, "a 10% jump should be many sigma in a quiet asset"


@pytest.mark.fast
def test_a_move_the_market_made_is_not_abnormal() -> None:
    """The counterfactual is the point: an asset that moved because everything
    moved has no abnormal return, and a measurement that reports one is
    measuring beta rather than news.
    """
    rng = np.random.default_rng(11)
    n, event_at = 60 * 24 * 40, 60 * 24 * 35
    market = rng.normal(0, 0.0004, n)
    market[event_at:event_at + 60] += 0.10 / 60
    window = Window(np.arange(n), market.copy(), market, event_at)

    at_hour = next(r for r in measure(window, "X", BARS_PER_DAY)
                   if r.horizon_minutes == 60)
    assert abs(at_hour.car) < 0.02, "a market-wide move was called abnormal"


@pytest.mark.fast
def test_normalisation_makes_two_assets_comparable() -> None:
    """A 2% move in a quiet asset and a 6% move in a volatile one are the same
    event in sigma, and pooling them raw produces garbage."""
    quiet = _window(jump=0.02, seed=3)
    loud = Window(
        times=quiet.times,
        asset_returns=quiet.asset_returns * 3,
        market_returns=quiet.market_returns,
        event_index=quiet.event_index,
    )
    z_quiet = next(r.z for r in measure(quiet, "Q", BARS_PER_DAY)
                   if r.horizon_minutes == 60)
    z_loud = next(r.z for r in measure(loud, "L", BARS_PER_DAY)
                  if r.horizon_minutes == 60)
    assert z_quiet == pytest.approx(z_loud, rel=0.1)
    car_quiet = next(r.car for r in measure(quiet, "Q", BARS_PER_DAY)
                     if r.horizon_minutes == 60)
    car_loud = next(r.car for r in measure(loud, "L", BARS_PER_DAY)
                    if r.horizon_minutes == 60)
    assert car_loud > car_quiet * 2


@pytest.mark.fast
def test_the_pre_event_window_catches_a_move_that_started_early() -> None:
    """A reaction that had begun before the announcement was public is leakage,
    not a reaction — and the only way to see it is to measure there too."""
    leaked = _window(jump=0.05, drift_before=0.08)
    clean = _window(jump=0.05)
    assert leakage_flag(measure(leaked, "X", BARS_PER_DAY))
    assert not leakage_flag(measure(clean, "X", BARS_PER_DAY))


@pytest.mark.fast
def test_a_horizon_past_the_data_is_skipped_not_truncated() -> None:
    """A 7-day figure computed over 3 days is not a 7-day figure, and reporting
    it as one lets a horizon become whatever the data happened to hold."""
    short = _window(n=60 * 24 * 36, event_at=60 * 24 * 35)
    horizons = {r.horizon_minutes for r in measure(short, "X", BARS_PER_DAY)}
    assert 1440 in horizons
    assert 10080 not in horizons, "a 7-day horizon was reported on 1 day of data"


@pytest.mark.fast
def test_a_market_wide_event_says_which_benchmark_it_used() -> None:
    """There is no unaffected asset to difference against, so the weaker
    time-series counterfactual is used — and recorded, because the two are not
    equally trustworthy."""
    window = _window(jump=0.05)
    cross = measure(window, "X", BARS_PER_DAY)[0]
    wide = measure(window, "X", BARS_PER_DAY, market_wide=True)[0]
    assert cross.benchmark_kind is BenchmarkKind.CROSS_SECTIONAL
    assert wide.benchmark_kind is BenchmarkKind.TIME_SERIES


@pytest.mark.fast
def test_a_rating_is_scored_against_what_happened() -> None:
    """The whole reason ratings are stored in sigma. A +2 against a measured
    +0.3 is 1.7 sigma optimistic — a number that accumulates into a calibration
    curve rather than an impression."""
    window = _window(jump=0.02)
    reactions = measure(window, "X", BARS_PER_DAY)
    measured = next(r.z for r in reactions if r.horizon_minutes == 1440)

    assert scored_against(measured, reactions) == pytest.approx(0.0, abs=1e-9)
    assert scored_against(measured + 1.7, reactions) == pytest.approx(1.7, abs=1e-9)
    assert scored_against(2.0, reactions, horizon_minutes=99) is None


@pytest.mark.fast
def test_every_horizon_in_the_grid_is_attempted() -> None:
    window = _window(n=60 * 24 * 60, event_at=60 * 24 * 40)
    measured = {r.horizon_minutes for r in measure(window, "X", BARS_PER_DAY)}
    assert measured == set(HORIZONS)


@pytest.mark.fast
def test_an_asset_cannot_be_its_own_benchmark() -> None:
    """Differencing an asset against itself gives zero by construction, and a
    row of zeroes reads as "nothing happened" rather than "not measurable this
    way" — which is the more dangerous of the two, because it looks like data.
    """
    rng = np.random.default_rng(21)
    n, event_at = 200, 120
    series = rng.normal(0, 0.02, n)
    series[event_at] += 0.10
    self_window = Window(np.arange(n), series, series, event_at)

    cross = measure(self_window, "BTCUSDT", bars_per_day=1, horizons=(1440,))[0]
    assert cross.car == pytest.approx(0.0, abs=1e-12)

    fallback = measure(self_window, "BTCUSDT", bars_per_day=1,
                       market_wide=True, horizons=(1440,))[0]
    assert abs(fallback.car) > 0.05
    assert fallback.benchmark_kind is BenchmarkKind.TIME_SERIES
