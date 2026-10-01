"""What actually happened after an event. Plan.md §11.3.

Asset-specific events: cross-sectional benchmark (return - beta x market, beta from an
embargoed pre-event window). Market-wide: time-series fallback, recorded as such.
`z = CAR(h) / (sigma_pre * sqrt(h))`, h in bars; z is poolable and the unit of human ratings.
Measures only: §11.5 fits, §11.10 differences.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

HORIZONS: tuple[int, ...] = (-60, -15, 5, 15, 60, 240, 1440, 4320, 10080)

ESTIMATION_DAYS = 30
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

    times: np.ndarray
    asset_returns: np.ndarray
    market_returns: np.ndarray
    event_index: int


def estimate_beta(
    asset: np.ndarray, market: np.ndarray, event_index: int,
    bars_per_day: int, embargo: int = EMBARGO_BARS,
) -> tuple[float | None, float]:
    """Beta and pre-event volatility, from an embargoed window before the event.

    `(None, sigma)` when the market is flat or the window too short; never substitute 1.0.
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

    Horizons past the end of the data are skipped, never truncated.
    """
    beta, sigma = estimate_beta(
        window.asset_returns, window.market_returns, window.event_index, bars_per_day)

    kind = BenchmarkKind.TIME_SERIES if market_wide else BenchmarkKind.CROSS_SECTIONAL
    if beta is None and not market_wide:
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
            abnormal = asset_leg - float(
                window.asset_returns[:max(1, window.event_index - EMBARGO_BARS)].mean())

        car = float(abnormal.sum())
        denominator = sigma * np.sqrt(span)
        out.append(Reaction(
            asset=asset, horizon_minutes=horizon, car=car,
            z=float(car / denominator) if denominator > 0 else 0.0,
            benchmark_kind=kind, beta_pre=beta, sigma_pre=sigma, n_bars=int(span),
        ))
    return out


def leakage_flag(reactions: list[Reaction], threshold: float = 1.5) -> bool:
    """Whether the move had already begun before the event was public. Flagged, not removed."""
    return any(abs(r.z) > threshold for r in reactions if r.is_pre_event)


def scored_against(rating_sigma: float, reactions: list[Reaction],
                   horizon_minutes: int = 1440) -> float | None:
    """How far a human rating sat from what happened, in sigma. Plan.md §20.5."""
    match = next((r for r in reactions if r.horizon_minutes == horizon_minutes), None)
    return None if match is None else rating_sigma - match.z


