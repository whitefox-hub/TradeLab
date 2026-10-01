"""The BTC ladder: the operator's one strategy. Pure: no network, database or clock.

The budget is split into PARTS equal parts, each bought at its own price and sold on its own:

The numbers in `Config` are the operator's, set on the Parameters screen; the time-scaled
minimums are fixed here.

* **Entry.** The ladder adapts to the price both ways. Down: a new part each time the price is
  STEP below the cheapest part held. Up: a new part after each REENTRY correction from the high
  since the last buy (or the last full exit), as long as it is at least STEP from every part
  held. The very first part is bought at once on the first start.
* **Exit.** A part's minimum net gain grows with the days it has been held (TIERS). Once it
  reaches that minimum it follows its highest price and sells when the price falls back TRAIL
  from it, never below the minimum, and always at its cap (twice the minimum, at least 10%).
* **Never at a loss.** A part below its minimum waits, however long that takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Config:
    """The operator's numbers, set on the Parameters screen (setting `btc_ladder`)."""

    budget: float = 1_000.0      # the starting budget, USDT; realised profit adds to it
    parts: int = 10
    step: float = 0.015          # a new part this far below the cheapest part held
    reentry: float = 0.05        # a new part after this correction from the high
    trail: float = 0.02          # a part sells this far back from its peak

    @classmethod
    def from_setting(cls, value: dict | None) -> Config:
        if not value:
            return cls()
        return cls(float(value.get("budget", cls.budget)), int(value["parts"]),
                   value["step_pct"] / 100, value["correction_pct"] / 100,
                   value["trail_pct"] / 100)


DEFAULT = Config()
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


def exit_now(part: Part, price: float, now: datetime, fee: float, slip: float,
             cfg: Config = DEFAULT) -> bool:
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
    return price <= part.peak * (1 - cfg.trail) or (falling and gain <= floor + FLOOR_MARGIN)


def buy_now(parts: list[Part], price: float, armed: bool, cfg: Config = DEFAULT) -> bool:
    """Whether to buy a new part now. `armed`: the price has corrected REENTRY from its high
    since the last buy or full exit."""
    if not parts:
        return armed
    if len(parts) >= cfg.parts:
        return False
    if price <= min(p.price for p in parts) * (1 - cfg.step):
        return True                                       # down the ladder
    clear = min(abs(price / p.price - 1) for p in parts) >= cfg.step
    return armed and clear                                # up the ladder, on a correction


def part_size(cash: float, parts: list[Part], cfg: Config = DEFAULT) -> float:
    """One part: a `parts`-th of the budget (cash plus the cost of the parts held)."""
    return min(cash, (cash + sum(p.spent for p in parts)) / cfg.parts)
