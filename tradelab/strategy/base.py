"""The interface a strategy satisfies. Plan.md step 9, §12.4, §14.1.

Step 9 of the sequence is "the strategy asks", and until now nothing asked: the
execution path was driven by a hard-coded always-long reference and a size the
limits happened to allow. This is the shape of the thing that will drive it.

**Three decisions, and they are not equal.**

| | Decides | §12.4 |
|---|---|---|
| Entry | whether to open | **Not editable anywhere.** Changing it |
| | | creates a new strategy that re-enters the funnel |
| Exit | whether to close | Same |
| Size | how much | Tunable, and the operator's |

That split is the whole reason this is an interface rather than a function.
Entry and exit are signal parameters, and §12.4 makes changing one a new
strategy: `runs.params` is immutable in the schema precisely so an entry rule
cannot be adjusted under a result. Size is in the left-hand column and may be
set. Anything that lets the two be edited the same way has collapsed the
distinction the funnel rests on.

**It does not know which mode it is in.** Step 9's rule is that the decision
code is byte-identical across backtest, simulator, testnet and live, and only
the data feed and the venue adapter change. So nothing here takes a mode, and a
test asserts it: a strategy that behaves differently in simulation is one whose
simulation proved nothing.

**It asks; it does not get.** The return is a *target*, expressed as a signed
fraction of equity. What is actually placed is that target after the market
trend conditions it (step 10) and after the risk engine has had its veto (step
11). A strategy that sized itself to the cap would find the trend has no room
upward, which is exactly what the first simulation discovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True, slots=True)
class Context:
    """Everything knowable at the instant of the decision, and nothing else.

    `history` ends at the bar being decided on and does not include it, so a
    strategy cannot read the price it is about to trade at. That is enforced
    here rather than left to each strategy to remember — §9 step 3 applies the
    shift centrally for the same reason.
    """

    at: datetime
    asset: str
    #: Closes up to but not including the decision bar.
    history: np.ndarray
    #: The last close the strategy may see. Separate from `history` so the
    #: boundary is visible rather than implied by an index.
    last_close: Decimal
    #: Signed, in units of the asset. Negative is short.
    held: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class Target:
    """What the strategy wants, and why.

    `exposure` is a signed fraction of equity: +0.2 is a long worth a fifth of
    the book, -0.1 a short worth a tenth, 0 flat. A fraction and not a notional,
    so the same strategy behaves the same at any capital and the arithmetic of
    scaling lives in one place.

    `reason` is carried because a refused or surprising order has to be
    explicable afterwards, and "the strategy wanted it" is not an explanation.
    """

    exposure: Decimal
    reason: str = ""

    def __post_init__(self) -> None:
        if not -1 <= self.exposure <= 1:
            raise ValueError(
                f"exposure {self.exposure} is outside -1 to 1. A strategy asks "
                "for a fraction of equity; leverage is the risk engine's to "
                "grant through max_leverage, not the strategy's to assume.")


@runtime_checkable
class Strategy(Protocol):
    """Produces a target. Takes no mode and no venue.

    Deliberately one method. An interface with `on_entry` and `on_exit` invites
    a strategy that holds state between them, and state held across bars is
    where a lookahead hides — the entry remembers something the exit then acts
    on before it was knowable.
    """

    #: Stable across a strategy's life. A change to entry or exit is a new
    #: strategy under §12.4, and therefore a new name.
    name: str

    def decide(self, context: Context) -> Target:
        """The target exposure for this instant, from this instant's data."""
        ...
