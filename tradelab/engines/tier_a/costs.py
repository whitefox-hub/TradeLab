"""Parametric cost model for vectorised screening. Plan.md §9.5, §9.2.

Approximate but must never be optimistic; calibrated against the event-driven engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    """All rates are fractions of notional, per side unless stated."""

    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    half_spread: float = 0.00005
    slippage: float = 0.0001
    maker_fill_rate: float = 0.0
    funding_per_bar: float = 0.0

    @property
    def per_side(self) -> float:
        """Cost of changing one unit of notional, one direction."""
        fee = self.maker_fill_rate * self.maker_fee + (1 - self.maker_fill_rate) * self.taker_fee
        return fee + self.half_spread + (1 - self.maker_fill_rate) * self.slippage

    @property
    def round_trip(self) -> float:
        return 2 * self.per_side

    def annual_drag(self, bars_per_year: float, avg_holding_bars: float,
                    include_funding: bool = False) -> float:
        """Cost drag per year for a strategy always in the market (§0.1).

        `include_funding` adds a permanent long's carry; off by default since §0.1 is turnover.
        """
        round_trips = bars_per_year / max(avg_holding_bars, 1e-9)
        drag = round_trips * self.round_trip
        if include_funding:
            drag += self.funding_per_bar * bars_per_year
        return drag


BINANCE_RETAIL = CostModel()

PESSIMISTIC = CostModel(taker_fee=0.0005, half_spread=0.0002, slippage=0.0005)
