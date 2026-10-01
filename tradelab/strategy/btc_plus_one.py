"""BTC +1%: buy BTC, sell when the sale nets +1% after every cost, buy again. Never sell
at a loss; a position that falls waits as long as it takes.

The operator's one strategy. Pure: no network, database or clock.
"""

from __future__ import annotations

TARGET_NET = 0.01


def proceeds(quantity: float, price: float, fee: float, half_spread: float) -> float:
    """What a market sale of `quantity` at `price` would bring, after slippage and fee."""
    return quantity * price * (1 - half_spread) * (1 - fee)


def should_sell(spent: float, quantity: float, price: float, fee: float,
                half_spread: float) -> bool:
    """True once selling now returns at least `spent` * (1 + 1%)."""
    # A millionth of a cent of tolerance, so the target price itself counts despite rounding.
    wanted = spent * (1 + TARGET_NET) - 1e-8
    return quantity > 0 and proceeds(quantity, price, fee, half_spread) >= wanted


def target_price(spent: float, quantity: float, fee: float, half_spread: float) -> float:
    """The price at which `should_sell` turns true."""
    return spent * (1 + TARGET_NET) / (quantity * (1 - half_spread) * (1 - fee))
