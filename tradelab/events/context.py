"""What events touch an asset at a given instant. Plan.md §11.7, §14.1.

This is how news enters the decision path. It does not produce a signal, and the
distinction matters: §11.7 is explicit that sign is an **input to the
expected-reaction model**, never a signal in itself, because what is tradeable
is the residual — observed reaction minus expected. Trading the sign directly
trades the part everyone else has already priced.

What can be done before that model exists is **conditioning**, which §11.7
positively recommends for the one class it rules out as a signal: a regime shift
is a change of market state, used as a conditioning variable rather than as an
entry. So this module answers "what is going on with this asset right now", and
the risk engine and the sizer decide what to do about it.

**Point in time, and this is the part that would silently break everything.** An
event is visible at instant t only if `available_at <= t`. Reading by
`first_seen_at` would let a backtest act on an announcement at the moment it
occurred rather than the moment a subscriber could have seen it, and reading
without a cutoff at all would let it act on tomorrow's news. Both look entirely
reasonable in the output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa

#: How much a live regime shift cuts the size of a new position.
#:
#: Not zero. Refusing to trade an asset because a war is on would sit out the
#: whole period, and §11.7's point is that a regime shift replaces the baseline
#: rather than removing the market — what changes is that the prior is worse,
#: not that the asset stopped trading.
REGIME_SIZE_MULTIPLIER = 0.5

#: A pending scheduled event inside this many days damps size the same way. The
#: anticipation window of §11.6: for a scheduled event the tradeable object is
#: the approach, and a position opened into one is taking a bet nobody sized.
ANTICIPATION_DAYS = 3
ANTICIPATION_MULTIPLIER = 0.7


@dataclass(frozen=True, slots=True)
class EventContext:
    asset: str
    as_of: datetime
    regime_shifts: int
    pending_schedules: int
    active_shocks: int
    #: Net direction of everything live, in -1/0/+1. Reported, not acted on —
    #: see the module docstring.
    net_direction: int

    @property
    def size_multiplier(self) -> float:
        """What to multiply an intended position by.

        Multiplicative rather than a veto, and the two conditions compound: an
        asset under a regime shift *and* days from a scheduled event is a worse
        prior than either alone.
        """
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


def context_for(
    connection: sa.Connection, asset: str, as_of: datetime,
) -> EventContext:
    """Every event visible at `as_of` that touches `asset`.

    One query, and the `available_at` cutoff is in it rather than applied
    afterwards: a filter that runs after the rows are fetched is one somebody
    removes for speed.
    """
    row = connection.execute(sa.text("""
        SELECT
          count(*) FILTER (
            WHERE e.shape = 'regime_shift' AND e.resolved_at IS NULL) AS regimes,
          count(*) FILTER (
            WHERE sr.scheduled_for IS NOT NULL
              AND sr.scheduled_for > :as_of
              AND sr.scheduled_for <= :as_of + make_interval(days => :days)
          ) AS pending,
          count(*) FILTER (
            WHERE e.shape = 'shock' AND e.resolved_at IS NULL
              AND e.first_seen_at > :as_of - interval '72 hours') AS shocks,
          coalesce(sum(a.expected_direction), 0) AS direction
        FROM events e
        JOIN event_assets a ON a.event_id = e.id
        LEFT JOIN LATERAL (
          SELECT scheduled_for FROM schedule_revisions s
           WHERE s.event_id = e.id AND s.available_at <= :as_of
           ORDER BY s.revision DESC LIMIT 1
        ) sr ON true
        WHERE a.asset = :asset
          -- Point in time. An event is visible when a subscriber could have
          -- seen it, not when it happened.
          AND e.first_seen_at <= :as_of
    """), {"asset": asset, "as_of": as_of, "days": ANTICIPATION_DAYS}).mappings().first()

    direction = int(row["direction"] or 0)
    return EventContext(
        asset=asset, as_of=as_of,
        regime_shifts=int(row["regimes"] or 0),
        pending_schedules=int(row["pending"] or 0),
        active_shocks=int(row["shocks"] or 0),
        net_direction=(direction > 0) - (direction < 0),
    )
