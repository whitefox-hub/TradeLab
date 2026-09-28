"""Participation and order-flow features from 1m kline fields. Plan.md §11.4, §0.2.

Trade count and average trade size are preferred over volume, which wash trading inflates.
"""

from __future__ import annotations

import polars as pl

from tradelab.features.params import IntRange
from tradelab.features.registry import feature


@feature(family="orderflow", lookback=lambda p: p["period"],
         params={"period": IntRange(2, 200)},
         requires=("taker_buy_base", "volume"))
def taker_imbalance(period: int) -> pl.Expr:
    """Taker buy fraction minus a half: positive is aggressive buying."""
    buys = pl.col("taker_buy_base").rolling_sum(period)
    total = pl.col("volume").rolling_sum(period)
    return buys / (total + 1e-12) - 0.5


@feature(family="orderflow", lookback=lambda p: p["slow"],
         params={"fast": IntRange(2, 60), "slow": IntRange(10, 400)},
         requires=("trade_count",))
def trade_count_z(fast: int, slow: int) -> pl.Expr:
    """Participation surge. Rising count is more people, not merely more size."""
    counts = pl.col("trade_count").cast(pl.Float64)
    return (counts.rolling_mean(fast) - counts.rolling_mean(slow)) / (
        counts.rolling_std(slow) + 1e-12
    )


@feature(family="orderflow", lookback=lambda p: p["slow"],
         params={"fast": IntRange(2, 60), "slow": IntRange(10, 400)},
         requires=("quote_volume", "trade_count"))
def avg_trade_size_z(fast: int, slow: int) -> pl.Expr:
    """Falling average size with rising count is retail inflow (§11.4)."""
    size = pl.col("quote_volume") / (pl.col("trade_count").cast(pl.Float64) + 1e-12)
    return (size.rolling_mean(fast) - size.rolling_mean(slow)) / (
        size.rolling_std(slow) + 1e-12
    )
