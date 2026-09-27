"""The reference position, which is a benchmark and not a strategy. §15.

§15 measures a strategy *against* buy-and-hold, so it is the one position series
that exists without anything having cleared the gate. It is here so the
execution path has something to call at step 9 while the funnel is empty, and it
is named so that nothing mistakes it for a result.

It has no entry condition and no exit condition. That is the point: every figure
it produces is the asset's move, scaled, and a comparison against it is the
smallest thing a real strategy has to beat.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradelab.strategy.base import Context, Target


@dataclass(frozen=True, slots=True)
class BuyAndHold:
    """Always long at a fixed fraction of equity.

    The fraction is a **sizing** choice and therefore the operator's under
    §12.4. It is not a signal parameter, because there is no signal: this asks
    for the same thing on every bar regardless of what the data says.

    Two thirds by default, not the whole allowance. A position sized at the cap
    leaves the market trend no room upward, and the answer to "more than the
    limits allow" is a veto rather than a smaller order — measured, at a trend
    of 10 giving 168 refusals and no position at all.
    """

    name: str = "reference:buy-and-hold"
    exposure: Decimal = Decimal(2) / Decimal(3) * Decimal("0.25")

    def decide(self, context: Context) -> Target:
        # `context` is read for nothing, deliberately. A benchmark that looked
        # at the data would be a strategy, and a poor one nobody registered.
        del context
        return Target(exposure=self.exposure,
                      reason="reference: always long, no condition")
