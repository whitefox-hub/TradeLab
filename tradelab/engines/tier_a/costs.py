"""Parametric cost model for vectorised screening. Plan.md §9.5, §9.2.

An approximation by design: tier A exists to discard 99.9% of candidates cheaply,
and it is calibrated against the event-driven engine rather than trusted on its
own. What it must not do is be optimistic, because an optimistic screen promotes
the wrong survivors and no amount of rigour downstream recovers from that.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    """All rates are fractions of notional, per side unless stated."""

    taker_fee: float = 0.0005          # Binance USD-M VIP0
    maker_fee: float = 0.0002
    half_spread: float = 0.00005
    slippage: float = 0.0001           # beyond the spread, taker
    maker_fill_rate: float = 0.0       # tier A assumes taker: the honest default
    funding_per_bar: float = 0.0

    @property
    def per_side(self) -> float:
        """Cost of changing one unit of notional, one direction."""
        fee = self.maker_fill_rate * self.maker_fee + (1 - self.maker_fill_rate) * self.taker_fee
        return fee + self.half_spread + (1 - self.maker_fill_rate) * self.slippage

    @property
    def round_trip(self) -> float:
        return 2 * self.per_side

    def annual_drag(self, bars_per_year: float, avg_holding_bars: float) -> float:
        """Cost drag per year for a strategy always in the market.

        This is the arithmetic behind §0.1: it is what rules out holding periods
        below roughly a day, and it is worth being able to compute rather than
        quote.
        """
        round_trips = bars_per_year / max(avg_holding_bars, 1e-9)
        return round_trips * self.round_trip


#: Retail Binance USD-M. Deliberately not tier 0: assuming fees you do not have
#: is the cheapest way to make a losing strategy look profitable.
BINANCE_RETAIL = CostModel()

#: Used in the promotion report alongside the realistic one (§9.5): a strategy
#: that only works under optimistic fills is a strategy that only works in the
#: backtest.
PESSIMISTIC = CostModel(taker_fee=0.0005, half_spread=0.0002, slippage=0.0005)
