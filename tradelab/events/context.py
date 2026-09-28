"""What events touch an asset at a given instant. Plan.md §11.7, §14.1.

Conditioning only, never a signal: sign is an input to the expected-reaction model (§11.7).
Point in time: an event is visible at t only once a subscriber could have seen it
(`first_seen_at`; schedule revisions by `available_at`), never later news.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa

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


def context_for(
    connection: sa.Connection, asset: str, as_of: datetime,
) -> EventContext:
    """Every event visible at `as_of` that touches `asset`; the cutoff stays in the SQL."""
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
