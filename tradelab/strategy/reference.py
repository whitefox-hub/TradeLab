"""The reference position, which is a benchmark and not a strategy. §15.

No entry or exit condition; used at step 9 while no strategy has cleared the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradelab.strategy.base import Context, Target


@dataclass(frozen=True, slots=True)
class BuyAndHold:
    """Always long at a fixed fraction of equity (a sizing choice, §12.4).

    Two thirds of the cap by default: at the cap an upward trend tilt is vetoed, not shrunk.
    """

    name: str = "reference:buy-and-hold"
    exposure: Decimal = Decimal(2) / Decimal(3) * Decimal("0.25")

    def decide(self, context: Context) -> Target:
        del context
        return Target(exposure=self.exposure,
                      reason="reference: always long, no condition")
