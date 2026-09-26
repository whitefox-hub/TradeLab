"""Triple-barrier labelling. Plan.md §7.

Labels are the one part of the pipeline that legitimately looks forward — that is
what a target is. The discipline is not to avoid the future but to record exactly
*when* each label became knowable, because that instant is what the purge in
§8.2 must cover.

Two details that naive implementations get wrong, both of which flatter results:

**Determination time, not horizon.** A label whose take-profit is touched after
three hours is known after three hours, not after the five-day maximum. Purging
on the horizon is needlessly wasteful; purging on the determination time is
correct and tighter. Both are recorded.

**Intrabar ordering is unknowable.** When a bar's high reaches the profit barrier
and its low reaches the stop, bar data cannot say which came first. Assuming the
favourable one is how a backtest invents profit it never had, so the adverse
barrier is assumed to win. It is pessimistic on purpose and it is stated here
rather than buried.

**Barrier width must match the holding period.** Volatility is measured per bar,
so a barrier set at one per-bar sigma is crossed within a few bars regardless of
the horizon: labelling a five-day trade that way resolved in three minutes in
practice. Barriers therefore scale by `sigma * sqrt(max_holding)`, the expected
move over the intended holding period, which is what makes the horizon barrier
reachable at all.
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
    """Barriers are multiples of recent volatility, never fixed percentages.

    A fixed 2% stop is a different trade in a calm market than in a violent one,
    so a strategy labelled that way is really several strategies wearing one name.
    """

    profit_mult: float = 2.0
    stop_mult: float = 1.0
    max_holding: int = 120          # bars; bounded to the §0.2 band
    vol_period: int = 100
    min_vol: float = 1e-6
    # Barriers widen with the square root of the holding period. Disable only to
    # study the per-bar behaviour; leaving it off makes the horizon barrier
    # effectively unreachable.
    scale_to_horizon: bool = True

    def as_dict(self) -> dict:
        return {
            "profit_mult": self.profit_mult, "stop_mult": self.stop_mult,
            "max_holding": self.max_holding, "vol_period": self.vol_period,
            "scale_to_horizon": self.scale_to_horizon,
        }

    @property
    def horizon_scale(self) -> float:
        return float(np.sqrt(self.max_holding)) if self.scale_to_horizon else 1.0


def _first_touch(
    close: np.ndarray, high: np.ndarray, low: np.ndarray,
    upper: np.ndarray, lower: np.ndarray, horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    """First barrier touched, and how many bars it took.

    Walks the horizon one offset at a time: `horizon` vectorised passes rather
    than a Python loop per event.
    """
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

        # Both barriers inside one bar: the order is unknowable from bar data, so
        # the adverse one is taken. Any other choice manufactures profit.
        stop_wins = hit_stop
        profit_wins = hit_profit & ~hit_stop

        newly = stop_wins | profit_wins
        label[stop_wins] = Barrier.STOP
        label[profit_wins] = Barrier.PROFIT
        bars_held[newly] = step
        settled |= newly

    # Events whose horizon runs past the end of the data have no determined
    # outcome and must be dropped, not labelled HORIZON.
    incomplete = np.arange(n) + horizon >= n
    bars_held[incomplete & ~settled] = -1
    return label, bars_held


def triple_barrier(
    bars: pl.DataFrame, spec: BarrierSpec | None = None, side: int = 1,
) -> pl.DataFrame:
    """Label every bar. Returns labels with their determination times.

    `side` is +1 for long, -1 for short; with a primary model supplying direction
    this is where meta-labelling attaches (§7).
    """
    spec = spec if spec is not None else BarrierSpec()
    frame = bars.sort("event_time")

    # Exponentially weighted, not rolling. A rolling window has two edges: it
    # takes an observation and, `vol_period` bars later, drops it — and the drop
    # moves the estimate as much as the take did, on a bar where nothing
    # happened. Barrier width is a decision, so that echo mislabels: a bar is
    # recorded as having touched nothing because the barrier widened for a shock
    # a full window in the past. RESULT-004 measured what the same edge costs
    # when it drives position size, and tests/unit/test_estimator_echo.py holds
    # the line here. An EWM has no trailing edge, only decay.
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
        # When the outcome became knowable. This is what the purge must cover —
        # not the maximum horizon, and certainly not the entry time.
        #
        # Gathered rather than taken through numpy: a round trip through
        # `to_numpy` strips the timezone, and a naive timestamp here would
        # compare silently wrongly against the tz-aware archive (§5).
        determined_at=frame["event_time"].gather(exit_idx),
        exit_price=pl.Series(close[exit_idx]),
        vol=pl.Series(vol),
    ).filter(pl.col("bars_held") >= 0)
