"""What actually happened after an event. Plan.md §11.3.

"The market moved after the news" is meaningless without a counterfactual, so
each reaction is measured against one and then normalised so reactions can be
pooled across assets at all.

**The benchmark.** For an asset-specific event — a listing, a hack, an unlock —
the counterfactual is cross-sectional: the asset's return less beta times the
market's, with beta estimated on a pre-event window that is embargoed so it
never touches the event itself. For a market-wide event there is no unaffected
asset to difference against, so the benchmark falls back to the time series and
that fact is recorded rather than hidden, because the two are not equally
trustworthy.

**The normalisation.** `z = CAR(h) / (sigma_pre * sqrt(h))`. Without it a 2% move
in a quiet altcoin and a 6% move in a volatile one are not comparable, and
pooling them produces garbage. `z` is the poolable quantity, and it is also the
unit a human rating is given in — which is what makes a rating scoreable against
what happened rather than merely recorded.

**What is deliberately not here.** No decay is fitted and no signal is produced.
This module measures; §11.5 fits and §11.10 differences. Keeping them apart
means a measurement can be checked without re-running a model, and a model can
be refitted without re-measuring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import numpy as np

#: Horizons in minutes, per §11's `event_reactions` grid. The negatives are the
#: pre-event window and they are not decoration: a reaction that had already
#: begun before the announcement was public is leakage, not a reaction, and the
#: only way to see it is to measure there too.
HORIZONS: tuple[int, ...] = (-60, -15, 5, 15, 60, 240, 1440, 4320, 10080)

#: Days of history used to estimate beta and the pre-event volatility.
ESTIMATION_DAYS = 30
#: Bars between the estimation window and the event. Without it the last bars of
#: the window contain the event's own run-up and beta absorbs the thing being
#: measured.
EMBARGO_BARS = 1


class BenchmarkKind(StrEnum):
    CROSS_SECTIONAL = "cross_sectional"
    TIME_SERIES = "time_series"


@dataclass(frozen=True, slots=True)
class Reaction:
    asset: str
    horizon_minutes: int
    car: float
    z: float
    benchmark_kind: BenchmarkKind
    beta_pre: float | None
    sigma_pre: float
    n_bars: int

    @property
    def is_pre_event(self) -> bool:
        return self.horizon_minutes < 0


@dataclass(frozen=True, slots=True)
class Window:
    """Returns around an event, already aligned to the event instant."""

    times: np.ndarray          # bar close times
    asset_returns: np.ndarray
    market_returns: np.ndarray
    event_index: int           # first bar at or after the event


def estimate_beta(
    asset: np.ndarray, market: np.ndarray, event_index: int,
    bars_per_day: int, embargo: int = EMBARGO_BARS,
) -> tuple[float | None, float]:
    """Beta and pre-event volatility, from an embargoed window before the event.

    Returns `(None, sigma)` when the market series is flat or the window is too
    short — a beta estimated on a handful of bars is noise, and substituting 1.0
    would quietly turn a cross-sectional benchmark into a raw return.
    """
    stop = max(0, event_index - embargo)
    start = max(0, stop - ESTIMATION_DAYS * bars_per_day)
    window_asset = asset[start:stop]
    window_market = market[start:stop]

    if window_asset.size < 20:
        return None, float(window_asset.std(ddof=1)) if window_asset.size > 1 else 0.0

    sigma = float(window_asset.std(ddof=1))
    variance = float(window_market.var(ddof=1))
    if variance <= 0:
        return None, sigma
    covariance = float(np.cov(window_asset, window_market, ddof=1)[0, 1])
    return covariance / variance, sigma


def measure(
    window: Window, asset: str, bars_per_day: int,
    market_wide: bool = False, horizons: tuple[int, ...] = HORIZONS,
) -> list[Reaction]:
    """Cumulative abnormal return and its z, at each horizon.

    Horizons that run past the end of the data are skipped rather than truncated
    to what exists: a 7-day figure computed over 3 days is not a 7-day figure,
    and reporting it as one is how a horizon quietly becomes whatever the data
    happened to hold.
    """
    beta, sigma = estimate_beta(
        window.asset_returns, window.market_returns, window.event_index, bars_per_day)

    kind = BenchmarkKind.TIME_SERIES if market_wide else BenchmarkKind.CROSS_SECTIONAL
    if beta is None and not market_wide:
        # No usable beta means no cross-sectional counterfactual, so the
        # benchmark is downgraded and says so rather than pretending.
        kind = BenchmarkKind.TIME_SERIES

    bars_per_minute = bars_per_day / (24 * 60)
    out: list[Reaction] = []

    for horizon in horizons:
        span = max(1, round(abs(horizon) * bars_per_minute))
        if horizon < 0:
            start, stop = window.event_index - span, window.event_index
        else:
            start, stop = window.event_index, window.event_index + span
        if start < 0 or stop > window.asset_returns.size:
            continue

        asset_leg = window.asset_returns[start:stop]
        if kind is BenchmarkKind.CROSS_SECTIONAL:
            abnormal = asset_leg - beta * window.market_returns[start:stop]
        else:
            # Without a cross-section the counterfactual is the asset's own
            # mean outside the event, which is weaker and recorded as such.
            abnormal = asset_leg - float(
                window.asset_returns[:max(1, window.event_index - EMBARGO_BARS)].mean())

        car = float(abnormal.sum())
        # sqrt(h) in bars, because sigma is a per-bar standard deviation and a
        # CAR accumulated over n bars scales with its square root under
        # independence. Using h in minutes would scale by the wrong quantity.
        denominator = sigma * np.sqrt(span)
        out.append(Reaction(
            asset=asset, horizon_minutes=horizon, car=car,
            z=float(car / denominator) if denominator > 0 else 0.0,
            benchmark_kind=kind, beta_pre=beta, sigma_pre=sigma, n_bars=int(span),
        ))
    return out


def leakage_flag(reactions: list[Reaction], threshold: float = 1.5) -> bool:
    """Whether the move had already begun before the event was public.

    A pre-event z of any size is a warning; one past the threshold means the
    reaction being measured is partly something the market already knew, and the
    post-event figure is not what it appears to be. Reported rather than
    subtracted out, because the honest handling of a contaminated measurement is
    to mark it.
    """
    return any(abs(r.z) > threshold for r in reactions if r.is_pre_event)


def scored_against(rating_sigma: float, reactions: list[Reaction],
                   horizon_minutes: int = 1440) -> float | None:
    """How far a human rating sat from what happened, in sigma. Plan.md §20.5.

    This is the whole reason ratings are stored in sigma. A rating of +2 against
    a measured z of +0.3 is not merely optimistic, it is 1.7 sigma optimistic —
    a number that accumulates into a calibration curve rather than an impression.
    """
    match = next((r for r in reactions if r.horizon_minutes == horizon_minutes), None)
    return None if match is None else rating_sigma - match.z


def utc_now() -> datetime:
    return datetime.now(UTC)


def window_bounds(event_at: datetime, bars_per_day: int) -> tuple[datetime, datetime]:
    """How much history a measurement needs either side of the event."""
    return (
        event_at - timedelta(days=ESTIMATION_DAYS + 2),
        event_at + timedelta(minutes=max(HORIZONS) + 1440),
    )
