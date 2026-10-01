"""The interface a strategy satisfies. Plan.md step 9, §12.4, §14.1.

Entry and exit are signal parameters: changing one makes a new strategy (§12.4; `runs.params`
is immutable). Size is tunable by the operator. Nothing takes a mode (a test asserts it).
The return is a target fraction of equity, then conditioned by the trend (step 10) and
vetoed by risk (step 11).
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

    `history` ends at the bar being decided on and does not include it (§9 step 3).
    """

    at: datetime
    asset: str
    history: np.ndarray
    last_close: Decimal
    held: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class Target:
    """What the strategy wants, and why. `exposure` is a signed fraction of equity in [-1, 1]."""

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

    One method on purpose: state held between entry/exit hooks is where lookahead hides.
    """

    name: str

    def decide(self, context: Context) -> Target:
        """The target exposure for this instant, from this instant's data."""
        ...
