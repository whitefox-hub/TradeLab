"""What events touch an asset at a given instant. Plan.md §11.7, §14.1.

Conditioning only, never a signal: sign is an input to the expected-reaction model (§11.7).
Point in time: an event is visible at t only once a subscriber could have seen it
(`first_seen_at`; schedule revisions by `available_at`), never later news.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: How much a live regime shift cuts a new position. Not zero: the prior is worse (§11.7).
REGIME_SIZE_MULTIPLIER = 0.5

#: A pending scheduled event inside this many days damps size too (§11.6).
ANTICIPATION_DAYS = 3
ANTICIPATION_MULTIPLIER = 0.7


@dataclass(frozen=True, slots=True)
class EventContext:
    asset: str
    as_of: datetime
    regime_shifts: int
    pending_schedules: int
    active_shocks: int
    #: Net direction of everything live, in -1/0/+1. Reported, not acted on.
    net_direction: int

    @property
    def size_multiplier(self) -> float:
        """What to multiply an intended position by; the two conditions compound."""
        multiplier = 1.0
        if self.regime_shifts:
            multiplier *= REGIME_SIZE_MULTIPLIER
        if self.pending_schedules:
            multiplier *= ANTICIPATION_MULTIPLIER
        return multiplier

    @property
    def reasons(self) -> list[str]:
        """Why the size moved, in words the audit log can carry."""
        out = []
        if self.regime_shifts:
            out.append(
                f"{self.regime_shifts} regime shift(s) live: the baseline has "
                f"been replaced, so the prior is worse rather than absent"
            )
        if self.pending_schedules:
            out.append(
                f"{self.pending_schedules} scheduled event(s) within "
                f"{ANTICIPATION_DAYS} days: a position opened into the "
                f"anticipation window is a bet nobody sized"
            )
        return out
