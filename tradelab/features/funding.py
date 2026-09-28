"""Funding and positioning features. Plan.md §11.4, §6.

Lookbacks are in funding intervals (8h settlements, UTC; §5.7), not bars; the wrong
unit understates the purge by 8x on hourly data.
"""

from __future__ import annotations

import polars as pl

from tradelab.features.params import IntRange
from tradelab.features.registry import feature


@feature(family="funding", lookback=lambda p: p["period"] + 1,
         params={"period": IntRange(3, 90)}, requires=("funding_rate",))
def funding_z(period: int) -> pl.Expr:
    """Z-score of funding against its own recent history (crowding, normalised)."""
    rate = pl.col("funding_rate")
    return (rate - rate.rolling_mean(period)) / (rate.rolling_std(period) + 1e-12)


@feature(family="funding", lookback=lambda p: p["period"],
         params={"period": IntRange(3, 90)}, requires=("funding_rate",))
def funding_cumulative(period: int) -> pl.Expr:
    """Total funding paid over the window."""
    return pl.col("funding_rate").rolling_sum(period)


@feature(family="funding", lookback=lambda p: p["period"],
         params={"period": IntRange(3, 90)}, requires=("funding_rate",))
def funding_persistence(period: int) -> pl.Expr:
    """Mean sign of recent settlements: one extreme print vs sustained one-sided paying."""
    sign = pl.col("funding_rate").sign()
    return sign.rolling_mean(period)


@feature(family="open_interest", lookback=lambda p: p["period"] + 1,
         params={"period": IntRange(3, 200)}, requires=("open_interest",))
def oi_change(period: int) -> pl.Expr:
    """Relative change in open interest: positions opening or closing."""
    oi = pl.col("open_interest")
    return oi / (oi.shift(period) + 1e-12) - 1.0


@feature(family="open_interest", lookback=lambda p: p["period"] + 1,
         params={"period": IntRange(3, 200)},
         requires=("open_interest", "close"))
def oi_price_divergence(period: int) -> pl.Expr:
    """OI change signed by price direction: positions opened into vs closed out of a move."""
    oi_move = pl.col("open_interest") / (pl.col("open_interest").shift(period) + 1e-12) - 1.0
    price_move = pl.col("close") / (pl.col("close").shift(period) + 1e-12) - 1.0
    return oi_move * price_move.sign()
