"""When a news item stops being information. Plan.md §11.8.

"How long does it last" has three distinct answers, and a single decay constant
cannot carry them. Conflating them is how a model keeps trading information that
stopped being information.

**Decay** — the residual reaction shrinks with time. Continuous, a *rate*, and
the only one an impulse response models.

**Supersession** — a later event about the same subject makes the earlier one
stale. A rumoured partnership that is denied does not decay, it is cancelled:
the feature must go to zero at the revision rather than fade from it.

**Resolution** — a scheduled event happens, and the anticipation it supported
ends discontinuously whatever the outcome. The thing being anticipated no longer
lies ahead.

The practical test that separates them: decay is a rate, the other two are dates.
A model carrying only a rate keeps a position open on an item cancelled yesterday
because its clock says half-life remains.

**On the numbers below.** They are assumptions, not measurements. §11.5 fits
`τ_slow` per event class from local projections, and until that runs there is no
measured horizon — so these are defaults chosen from §0.1's observation that news
reaction residuals decay over hours to days, and every surface that uses them
says they are provisional. A default presented as a finding is worse than no
default at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class EndedBy(StrEnum):
    DECAY = "decay"
    SUPERSEDED = "superseded"
    RESOLVED = "resolved"
    OPEN = "open"


#: Provisional decay horizons per event class, in hours. Replaced by the fitted
#: `τ_slow` of §11.5 once the impulse response has been estimated; until then
#: every screen that shows them labels them as assumed.
ASSUMED_DECAY_HOURS: dict[str, float] = {
    "futures_listing": 72.0,
    "spot_listing": 72.0,
    "seed_tag": 48.0,
    "futures_delivery": 24.0,
    "spot_delisting": 168.0,
    "futures_delisting": 168.0,
    "margin_change": 24.0,
    "trading_halt": 12.0,
    "trading_resume": 12.0,
    "hack": 240.0,
    "regulation": 336.0,
    "macro_release": 48.0,
    "unlock": 120.0,
}

DEFAULT_DECAY_HOURS = 72.0

#: Process states that end a signal. A withdrawn proposal is not a slow fade.
TERMINAL_STATES = frozenset({"decided", "implemented", "withdrawn"})


@dataclass(frozen=True, slots=True)
class Expiry:
    ended_by: EndedBy
    expires_at: datetime | None
    #: True while the item is still information. Not "recent" — a schedule three
    #: months out is old news and still entirely live, and a shock from this
    #: morning can already be spent.
    active: bool
    #: Why, in a sentence the screen can show. An expiry the operator cannot
    #: account for is one they will override by feel.
    reason: str
    assumed: bool = False


def resolve(
    *,
    event_type: str,
    shape: str,
    first_seen_at: datetime,
    resolved_at: datetime | None = None,
    scheduled_for: datetime | None = None,
    latest_state: str | None = None,
    now: datetime | None = None,
) -> Expiry:
    """Which of §11.8's three endings applies, and whether it has happened yet.

    Checked in order of precedence rather than by shape, because the orderings
    differ: an explicit resolution beats a schedule, and a schedule that has
    passed beats a decay clock that still has time on it.
    """
    now = now or datetime.now(UTC)

    # 1. Recorded outright. Someone said this ended, and a judgement about the
    #    world beats a clock.
    if resolved_at is not None:
        return Expiry(
            EndedBy.RESOLVED, resolved_at, active=resolved_at > now,
            reason=f"marked resolved on {resolved_at.date()}",
        )

    # 2. Superseded by a terminal process state. Goes to zero at the transition
    #    rather than fading from it (§11.8).
    if latest_state in TERMINAL_STATES:
        return Expiry(
            EndedBy.SUPERSEDED, None, active=False,
            reason=f"process reached {latest_state!r}; the signal ends at the "
                   f"transition rather than fading from it",
        )

    # 3. Scheduled, and the anticipation ends when the date arrives — whatever
    #    the outcome. Before the date it is live no matter how old it is.
    if shape == "schedule" and scheduled_for is not None:
        return Expiry(
            EndedBy.RESOLVED, scheduled_for, active=scheduled_for > now,
            reason=(
                f"anticipation runs to {scheduled_for.date()}"
                if scheduled_for > now
                else f"the scheduled date passed on {scheduled_for.date()}; "
                     f"the thing anticipated no longer lies ahead"
            ),
        )

    # 4. Otherwise it decays, on an assumed horizon until §11.5 measures one.
    hours = ASSUMED_DECAY_HOURS.get(event_type, DEFAULT_DECAY_HOURS)
    expires = first_seen_at + timedelta(hours=hours)
    return Expiry(
        EndedBy.DECAY if expires <= now else EndedBy.OPEN,
        expires, active=expires > now,
        reason=f"assumed {hours:.0f}h decay for {event_type}, not yet measured",
        assumed=True,
    )
