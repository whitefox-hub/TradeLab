"""Volatility features. Plan.md §6, §11.3.

Range estimators (Parkinson, Garman-Klass) are preferred: several times more efficient (§8.8).
"""

from __future__ import annotations

import polars as pl

from tradelab.features.params import IntRange
from tradelab.features.registry import feature


@feature(family="volatility", lookback=lambda p: p["period"] + 1,
         params={"period": IntRange(2, 200)})
def realised_vol(period: int) -> pl.Expr:
    """Close-to-close volatility; baseline for the range estimators."""
    return (pl.col("close") / pl.col("close").shift(1)).log().rolling_std(period)


@feature(family="volatility", lookback=lambda p: p["period"],
         params={"period": IntRange(2, 200)}, requires=("high", "low"))
def parkinson(period: int) -> pl.Expr:
    """Uses the bar range. ~5x more efficient than close-to-close."""
    hl = (pl.col("high") / pl.col("low")).log() ** 2
    return (hl.rolling_mean(period) / (4 * 0.6931471805599453)).sqrt()


@feature(family="volatility", lookback=lambda p: p["period"],
         params={"period": IntRange(2, 200)}, requires=("high", "low", "close"))
def garman_klass(period: int) -> pl.Expr:
    hl = 0.5 * (pl.col("high") / pl.col("low")).log() ** 2
    co = (2 * 0.6931471805599453 - 1) * (pl.col("close") / pl.col("open")).log() ** 2
    return (hl - co).rolling_mean(period).clip(lower_bound=0).sqrt()


@feature(family="volatility", lookback=lambda p: p["period"] + 1,
         params={"period": IntRange(2, 100)}, requires=("high", "low", "close"))
def atr(period: int) -> pl.Expr:
    previous = pl.col("close").shift(1)
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - previous).abs(),
        (pl.col("low") - previous).abs(),
    )
    return true_range.rolling_mean(period)


@feature(family="volatility", lookback=lambda p: p["slow"] + 1,
         params={"fast": IntRange(2, 60), "slow": IntRange(10, 400)})
def vol_ratio(fast: int, slow: int) -> pl.Expr:
    """Expanding or contracting volatility — a regime input (§10)."""
    returns = (pl.col("close") / pl.col("close").shift(1)).log()
    return returns.rolling_std(fast) / (returns.rolling_std(slow) + 1e-12)
