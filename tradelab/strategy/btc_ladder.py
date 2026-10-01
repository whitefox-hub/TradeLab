"""The BTC ladder: the operator's one strategy. Pure: no network, database or clock.

The budget is split into PARTS equal parts, each bought at its own price and sold on its own:

* **Entry.** With nothing held, the first part is bought at once on the very first start, and
  after a full exit only once the price has corrected REENTRY from its high since that exit.
  With parts held, a new part is bought each time the price is STEP below the cheapest one.
* **Exit.** A part's minimum net gain grows with the days it has been held (TIERS). Once it
  reaches that minimum it follows its highest price and sells when the price falls back TRAIL
  from it, never below the minimum, and always at its cap (twice the minimum, at least 10%).
* **Never at a loss.** A part below its minimum waits, however long that takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PARTS = 10
STEP = 0.10
REENTRY = 0.02
TRAIL = 0.02
#: (days held, minimum net gain), longest first.
TIERS = ((90, 0.20), (60, 0.12), (45, 0.08), (30, 0.05), (15, 0.03), (0, 0.01))
#: A part this close above its minimum sells rather than risk slipping under it.
FLOOR_MARGIN = 0.001


@dataclass
class Part:
    price: float          # fill price
    quantity: float       # BTC
    spent: float          # USDT paid, fee included
    opened: datetime
    peak: float | None = None   # highest price since the part reached its minimum


def minimum(days: float) -> float:
    return next(gain for held, gain in TIERS if days >= held)


def cap(days: float) -> float:
    return max(0.10, 2 * minimum(days))


def net(part: Part, price: float, fee: float, slip: float) -> float:
    """The net gain a market sale now would realise."""
    return part.quantity * price * (1 - slip) * (1 - fee) / part.spent - 1


def exit_now(part: Part, price: float, now: datetime, fee: float, slip: float) -> bool:
    """Whether to sell this part now. Updates `part.peak` as a side effect."""
    days = (now - part.opened).total_seconds() / 86_400
    gain, floor = net(part, price, fee, slip), minimum(days)
    if gain < floor:
        return False                                 # below the minimum: never sell
    if gain >= cap(days):
        return True
    part.peak = price if part.peak is None else max(part.peak, price)
    falling = price < part.peak
    # Back TRAIL from the peak, or falling onto the minimum: sell before it slips under.
    return price <= part.peak * (1 - TRAIL) or (falling and gain <= floor + FLOOR_MARGIN)


def buy_now(parts: list[Part], price: float, armed: bool) -> bool:
    """Whether to buy a new part now."""
    if not parts:
        return armed
    return len(parts) < PARTS and price <= min(p.price for p in parts) * (1 - STEP)


def part_size(cash: float, parts: list[Part]) -> float:
    """One part: a PARTS-th of the budget (cash plus the cost of the parts held)."""
    return min(cash, (cash + sum(p.spent for p in parts)) / PARTS)
