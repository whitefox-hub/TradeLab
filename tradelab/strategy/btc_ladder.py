"""The BTC ladder: the operator's one strategy. Pure: no network, database or clock.

The budget is split into PARTS equal parts, each bought at its own price and sold on its own:

The numbers in `Config` are the operator's, set on the Parameters screen; the time-scaled
minimums are fixed here. SIZES, when set, makes the parts unequal: the n-th part held is
bought with the n-th size's share of the budget, so the deeper rungs can be bigger. Parts sell
from the cheapest up, so a rung bought again gets its own size back. STEPS, when set, makes
the gaps unequal: with n parts held the next is bought the n-th step below the cheapest, so the
deep rungs can be wider and the ladder reach further before it is full.

* **Entry.** The ladder adapts to the price both ways. Down: a new part each time the price is
  STEP below the cheapest part held. Up: a new part after each REENTRY correction from the high
  since the last buy (or the last full exit), as long as it is at least STEP from every part
  held. The very first part is bought at once on the first start. The price of the last sale
  counts as a part held for both: a part sold below the next rung up is not bought back at
  the price it just sold at, only STEP under it.
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

    budget: float = 1_000.0
    parts: int = 10
    step: float = 0.015
    reentry: float = 0.05
    trail: float = 0.02
    sizes: tuple[float, ...] = ()
    steps: tuple[float, ...] = ()

    @classmethod
    def from_setting(cls, value: dict | None) -> Config:
        if not value:
            return cls()
        return cls(float(value.get("budget", cls.budget)), int(value["parts"]),
                   value["step_pct"] / 100, value["correction_pct"] / 100,
                   value["trail_pct"] / 100, tuple(float(s) for s in value.get("sizes", ())),
                   tuple(s / 100 for s in value.get("steps_pct", ())))

    @classmethod
    def from_saved(cls, value: dict) -> Config:
        """The config as a book's state file saved it (lists come back from JSON as lists)."""
        return cls(**{**value, "sizes": tuple(value.get("sizes", ())),
                      "steps": tuple(value.get("steps", ()))})


DEFAULT = Config()
TIERS = ((90, 0.20), (60, 0.12), (45, 0.08), (30, 0.05), (15, 0.03), (0, 0.01))
FLOOR_MARGIN = 0.001


@dataclass
class Part:
    price: float
    quantity: float
    spent: float
    opened: datetime
    peak: float | None = None


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
        return False
    if gain >= cap(days):
        return True
    part.peak = price if part.peak is None else max(part.peak, price)
    falling = price < part.peak
    return price <= part.peak * (1 - cfg.trail) or (falling and gain <= floor + FLOOR_MARGIN)


def down_step(held: int, cfg: Config = DEFAULT) -> float:
    """The drop under the cheapest rung that buys the next part when HELD parts are held."""
    return cfg.steps[min(held, len(cfg.steps)) - 1] if cfg.steps and held else cfg.step


def buy_now(parts: list[Part], price: float, armed: bool, cfg: Config = DEFAULT,
            last_sale: float | None = None) -> bool:
    """Whether to buy a new part now. `armed`: the price has corrected REENTRY from its high
    since the last buy or full exit. `last_sale`: the price the last part sold at."""
    if not parts:
        return armed
    if len(parts) >= cfg.parts:
        return False
    rungs = [p.price for p in parts] + ([last_sale] if last_sale else [])
    if price <= min(rungs) * (1 - down_step(len(parts), cfg)):
        return True
    clear = min(abs(price / r - 1) for r in rungs) >= cfg.step
    return armed and clear


def part_size(cash: float, parts: list[Part], cfg: Config = DEFAULT) -> float:
    """One part: a `parts`-th of the budget (cash plus the cost of the parts held), or with
    SIZES the next rung's share of it."""
    budget = cash + sum(p.spent for p in parts)
    if cfg.sizes:
        return min(cash, budget * cfg.sizes[min(len(parts), len(cfg.sizes) - 1)]
                   / sum(cfg.sizes))
    return min(cash, budget / cfg.parts)
