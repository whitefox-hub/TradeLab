"""Momentum and trend features. Plan.md §6.

Lookbacks are exact: understated breaks purging silently (caught by §16.3), overstated
wastes training data.
"""

from __future__ import annotations

import polars as pl

from tradelab.features.params import Choice, IntRange
from tradelab.features.registry import ewm_lookback, feature


@feature(
    family="momentum",
    lookback=lambda p: p["period"] + 1,
    params={"period": IntRange(2, 60), "source": Choice("close", "vwap")},
)
def rsi(period: int, source: str) -> pl.Expr:
    delta = pl.col(source).diff()
    gain = delta.clip(lower_bound=0).rolling_mean(period)
    loss = (-delta).clip(lower_bound=0).rolling_mean(period)
    return 100 - 100 / (1 + gain / (loss + 1e-12))


@feature(
    family="momentum",
    lookback=lambda p: p["period"] + 1,
    params={"period": IntRange(1, 120)},
)
def log_return(period: int) -> pl.Expr:
    return (pl.col("close") / pl.col("close").shift(period)).log()


@feature(
    family="trend",
    lookback=lambda p: ewm_lookback(p["span"]),
    params={"span": IntRange(2, 200)},
)
def ema(span: int) -> pl.Expr:
    return pl.col("close").ewm_mean(span=span, adjust=False)


@feature(
    family="trend",
    lookback=lambda p: ewm_lookback(max(p["fast"], p["slow"])),
    params={"fast": IntRange(2, 60), "slow": IntRange(3, 200)},
)
def ema_cross(fast: int, slow: int) -> pl.Expr:
    """Normalised by the slow EMA so the value is comparable across assets."""
    quick = pl.col("close").ewm_mean(span=fast, adjust=False)
    slow_ema = pl.col("close").ewm_mean(span=slow, adjust=False)
    return (quick - slow_ema) / (slow_ema + 1e-12)


@feature(
    family="momentum",
    lookback=lambda p: p["period"] + 1,
    params={"period": IntRange(2, 120)},
)
def roc(period: int) -> pl.Expr:
    return pl.col("close").pct_change(period)
