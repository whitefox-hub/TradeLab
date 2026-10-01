"""Market regime classification. Plan.md §10.

Thresholds must not know the future: each is an expanding-window quantile over strictly
past data (covered by §16.3 truncation invariance). The first `warmup` bars are UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import polars as pl


class Direction(StrEnum):
    BULL = "bull"
    BEAR = "bear"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class Volatility(StrEnum):
    HIGH = "high_vol"
    LOW = "low_vol"
    UNKNOWN = "unknown"


class Structure(StrEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegimeSpec:
    trend_period: int = 100
    vol_period: int = 100
    warmup: int = 500
    vol_high_quantile: float = 0.66
    vol_low_quantile: float = 0.33
    trend_quantile: float = 0.60
    efficiency_quantile: float = 0.60


def _expanding_quantile(values: np.ndarray, q: float, warmup: int) -> np.ndarray:
    """Quantile of everything strictly BEFORE each point (excludes the current bar).

    Deliberately a plain loop for auditability over speed.
    """
    out = np.full(values.size, np.nan)
    for i in range(warmup, values.size):
        window = values[:i]
        window = window[np.isfinite(window)]
        if window.size >= warmup // 2:
            out[i] = np.quantile(window, q)
    return out


def classify(bars: pl.DataFrame, spec: RegimeSpec | None = None) -> pl.DataFrame:
    """Label every bar with direction, volatility and structure."""
    spec = spec if spec is not None else RegimeSpec()
    frame = bars.sort("event_time")
    close = frame["close"].to_numpy().astype(np.float64)
    n = close.size

    log_returns = np.zeros(n)
    log_returns[1:] = np.log(close[1:] / close[:-1])

    vol = (
        pl.Series(log_returns)
        .ewm_std(span=spec.vol_period, min_samples=spec.vol_period)
        .to_numpy()
    )
    trend = np.full(n, np.nan)
    trend[spec.trend_period:] = (
        close[spec.trend_period:] / close[: n - spec.trend_period] - 1.0
    )

    net = np.abs(pl.Series(close).diff(spec.trend_period).to_numpy())
    path = pl.Series(np.abs(np.diff(close, prepend=close[0]))).rolling_sum(
        spec.trend_period).to_numpy()
    efficiency = np.divide(net, path, out=np.full(n, np.nan), where=path > 0)

    vol_hi = _expanding_quantile(vol, spec.vol_high_quantile, spec.warmup)
    vol_lo = _expanding_quantile(vol, spec.vol_low_quantile, spec.warmup)
    trend_hi = _expanding_quantile(np.abs(trend), spec.trend_quantile, spec.warmup)
    eff_hi = _expanding_quantile(efficiency, spec.efficiency_quantile, spec.warmup)

    direction = np.full(n, Direction.UNKNOWN.value, dtype=object)
    known_trend = np.isfinite(trend) & np.isfinite(trend_hi)
    direction[known_trend & (trend > trend_hi)] = Direction.BULL.value
    direction[known_trend & (trend < -trend_hi)] = Direction.BEAR.value
    direction[known_trend & (np.abs(trend) <= trend_hi)] = Direction.NEUTRAL.value

    volatility = np.full(n, Volatility.UNKNOWN.value, dtype=object)
    known_vol = np.isfinite(vol) & np.isfinite(vol_hi) & np.isfinite(vol_lo)
    volatility[known_vol & (vol >= vol_hi)] = Volatility.HIGH.value
    volatility[known_vol & (vol <= vol_lo)] = Volatility.LOW.value
    volatility[known_vol & (vol > vol_lo) & (vol < vol_hi)] = Volatility.LOW.value

    structure = np.full(n, Structure.UNKNOWN.value, dtype=object)
    known_eff = np.isfinite(efficiency) & np.isfinite(eff_hi)
    structure[known_eff & (efficiency >= eff_hi)] = Structure.TRENDING.value
    structure[known_eff & (efficiency < eff_hi)] = Structure.RANGING.value

    return frame.select("event_time", "close").with_columns(
        direction=pl.Series(direction, dtype=pl.Utf8),
        volatility=pl.Series(volatility, dtype=pl.Utf8),
        structure=pl.Series(structure, dtype=pl.Utf8),
        realised_vol=pl.Series(vol),
        efficiency_ratio=pl.Series(efficiency),
        regime=pl.Series(
            [f"{d}|{v}" for d, v in zip(direction, volatility, strict=True)], dtype=pl.Utf8
        ),
    )


def coverage(labels: pl.DataFrame, min_bars: int = 200) -> dict[str, int]:
    """Bars per regime, dropping regimes below `min_bars` and unknown (§15)."""
    counts = labels.group_by("regime").len().sort("len", descending=True)
    return {
        row["regime"]: row["len"]
        for row in counts.iter_rows(named=True)
        if row["len"] >= min_bars and not row["regime"].startswith("unknown")
    }
