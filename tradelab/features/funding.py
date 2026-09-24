"""Funding and positioning features. Plan.md §11.4, §6.

These are the ones the differentiation thesis rests on, and they differ from
price-derived indicators in a way that matters: funding is **not a function of
price**. It is what one side of the market is paying the other to hold its
position, settled every eight hours. An RSI tells you where price has been; a
funding extreme tells you who is crowded and what it is costing them.

That distinction is the entire reason for preferring them. RSI over close prices
has been mined by everyone with a terminal since the 1980s. Cross-venue funding
dispersion has not, because it is operationally awkward to collect and useless to
anyone not trading perpetuals.

Every lookback here is in **funding intervals**, not bars: settlement happens at
00:00, 08:00 and 16:00 UTC, which is also why the daily bar boundary must be UTC
(§5.7). A feature declaring its lookback in the wrong unit would understate the
purge by a factor of eight on hourly data.
"""

from __future__ import annotations

import polars as pl

from tradelab.features.params import IntRange
from tradelab.features.registry import feature


@feature(family="funding", lookback=lambda p: p["period"] + 1,
         params={"period": IntRange(3, 90)}, requires=("funding_rate",))
def funding_z(period: int) -> pl.Expr:
    """How unusual the current funding is against its own recent history.

    Crowding, normalised. A high positive z means longs are paying unusually
    dearly to stay long, which is a statement about positioning rather than
    about price.
    """
    rate = pl.col("funding_rate")
    return (rate - rate.rolling_mean(period)) / (rate.rolling_std(period) + 1e-12)


@feature(family="funding", lookback=lambda p: p["period"],
         params={"period": IntRange(3, 90)}, requires=("funding_rate",))
def funding_cumulative(period: int) -> pl.Expr:
    """Total funding paid over the window: the carrying cost of the crowd."""
    return pl.col("funding_rate").rolling_sum(period)


@feature(family="funding", lookback=lambda p: p["period"],
         params={"period": IntRange(3, 90)}, requires=("funding_rate",))
def funding_persistence(period: int) -> pl.Expr:
    """Share of recent settlements with the same sign.

    Distinguishes a single extreme print from a regime in which one side has
    been paying continuously — and the second is the interesting one, because it
    is a position that has to be unwound.
    """
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
    """Open interest rising while price falls, or the reverse.

    The mechanical reading: new positions being opened into a move is different
    from existing ones being closed out of it, and the two resolve differently.
    Price alone cannot tell them apart.
    """
    oi_move = pl.col("open_interest") / (pl.col("open_interest").shift(period) + 1e-12) - 1.0
    price_move = pl.col("close") / (pl.col("close").shift(period) + 1e-12) - 1.0
    return oi_move * price_move.sign()
