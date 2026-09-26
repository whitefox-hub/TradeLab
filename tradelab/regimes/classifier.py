"""Market regime classification. Plan.md §10.

The trap this module is written around is easy to fall into and invisible
afterwards: **the thresholds must not know the future.**

Calling a period "high volatility" because it sits above the median of the whole
sample uses data from years later to label a day in 2020. Nothing raises, the
labels look entirely sensible, and every per-regime result computed from them is
contaminated. Worse, the contamination flatters: regimes defined with hindsight
separate outcomes better than regimes anyone could have identified at the time.

So every threshold here is an **expanding-window quantile over past data only**.
The classification of a bar depends on that bar and its history, never on what
came after. §16.3's truncation invariance covers it, because a regime label is a
feature in every sense that matters.

The cost is real and worth stating: the first `warmup` bars cannot be classified
at all, because there is no history to set a threshold against. They are returned
as UNKNOWN rather than guessed.
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
    warmup: int = 500               # bars before any threshold is trustworthy
    vol_high_quantile: float = 0.66
    vol_low_quantile: float = 0.33
    trend_quantile: float = 0.60    # |trend| above this is directional
    # Efficiency ratio: net move divided by total path travelled. High means the
    # market went somewhere; low means it wandered.
    efficiency_quantile: float = 0.60

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__slots__}


def _expanding_quantile(values: np.ndarray, q: float, warmup: int) -> np.ndarray:
    """Quantile of everything strictly BEFORE each point.

    The whole module turns on this being exclusive of the current bar and of
    everything after it. A vectorised sort-based version would be faster; this
    is written for auditability, since being obviously correct matters more here
    than being quick.
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

    # Exponentially weighted, for the reason given in labeling/barriers.py: a
    # rolling window drops an observation as sharply as it took it, so a shock
    # is paid for twice and the second time lands on a bar where nothing
    # happened. Here that files a day under the wrong regime — and §13.1's point
    # is that correlation is a property of the regime, which FINDING-004
    # confirmed the expensive way.
    vol = (
        pl.Series(log_returns)
        .ewm_std(span=spec.vol_period, min_samples=spec.vol_period)
        .to_numpy()
    )
    trend = np.full(n, np.nan)
    trend[spec.trend_period:] = (
        close[spec.trend_period:] / close[: n - spec.trend_period] - 1.0
    )

    # Efficiency ratio (Kaufman): |net move| / sum |bar moves| over the window.
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
    """Bars per regime. A regime below `min_bars` cannot support a conclusion.

    §15 requires a strategy to be positive in at least two regimes; that means
    two regimes with enough observations to say anything, not two labels that
    happen to appear.
    """
    counts = labels.group_by("regime").len().sort("len", descending=True)
    return {
        row["regime"]: row["len"]
        for row in counts.iter_rows(named=True)
        if row["len"] >= min_bars and not row["regime"].startswith("unknown")
    }
