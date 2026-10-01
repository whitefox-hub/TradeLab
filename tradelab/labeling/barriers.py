"""Triple-barrier labelling. Plan.md §7.

Records when each label became knowable (determination time, not horizon); the purge
(§8.2) covers that. The adverse barrier wins an ambiguous bar. Barriers scale by
`sigma * sqrt(max_holding)` since per-bar sigma is crossed within a few bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import polars as pl


class Barrier(IntEnum):
    STOP = -1
    HORIZON = 0
    PROFIT = 1


@dataclass(frozen=True, slots=True)
class BarrierSpec:
    """Barriers are multiples of recent volatility, never fixed percentages."""

    profit_mult: float = 2.0
    stop_mult: float = 1.0
    max_holding: int = 120
    vol_period: int = 100
    min_vol: float = 1e-6
    scale_to_horizon: bool = True


    @property
    def horizon_scale(self) -> float:
        return float(np.sqrt(self.max_holding)) if self.scale_to_horizon else 1.0


def _first_touch(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    upper: np.ndarray, lower: np.ndarray, horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    """First barrier touched, and how many bars it took (one vectorised pass per offset)."""
    n = close.size
    label = np.full(n, Barrier.HORIZON, dtype=np.int8)
    bars_held = np.full(n, horizon, dtype=np.int32)
    settled = np.zeros(n, dtype=bool)

    for step in range(1, horizon + 1):
        idx = np.arange(n) + step
        valid = (idx < n) & ~settled
        if not valid.any():
            break
        j = np.where(valid, idx, 0)

        hit_stop = valid & (low[j] <= lower)
        hit_profit = valid & (high[j] >= upper)

        stop_wins = hit_stop
        profit_wins = hit_profit & ~hit_stop

        newly = stop_wins | profit_wins
        label[stop_wins] = Barrier.STOP
        label[profit_wins] = Barrier.PROFIT
        bars_held[newly] = step
        settled |= newly

    incomplete = np.arange(n) + horizon >= n
    bars_held[incomplete & ~settled] = -1
    return label, bars_held


def triple_barrier(
    bars: pl.DataFrame, spec: BarrierSpec | None = None, side: int = 1,
) -> pl.DataFrame:
    """Label every bar with its determination time.

    `side`: +1 long, -1 short; meta-labelling attaches here (§7).
    """
    spec = spec if spec is not None else BarrierSpec()
    frame = bars.sort("event_time")

    vol = (
        frame.select(
            (pl.col("close") / pl.col("close").shift(1)).log()
            .ewm_std(span=spec.vol_period, min_samples=spec.vol_period,
                     ignore_nulls=False).alias("vol")
        )["vol"].fill_null(strategy="forward").fill_null(spec.min_vol).to_numpy()
    )
    vol = np.maximum(vol, spec.min_vol)

    close = frame["close"].to_numpy().astype(np.float64)
    high = frame["high"].to_numpy().astype(np.float64)
    low = frame["low"].to_numpy().astype(np.float64)

    width = vol * spec.horizon_scale
    if side >= 0:
        upper = close * np.exp(spec.profit_mult * width)
        lower = close * np.exp(-spec.stop_mult * width)
    else:
        upper = close * np.exp(spec.stop_mult * width)
        lower = close * np.exp(-spec.profit_mult * width)

    label, bars_held = _first_touch(close, high, low, upper, lower, spec.max_holding)
    if side < 0:
        label = np.where(label == 0, 0, -label).astype(np.int8)

    n = frame.height
    exit_idx = np.minimum(np.arange(n) + np.maximum(bars_held, 0), n - 1)

    return frame.select("event_time", "available_at", "close").with_columns(
        label=pl.Series(label, dtype=pl.Int8),
        bars_held=pl.Series(bars_held, dtype=pl.Int32),
        determined_at=frame["event_time"].gather(exit_idx),
        exit_price=pl.Series(close[exit_idx]),
        vol=pl.Series(vol),
    ).filter(pl.col("bars_held") >= 0)
