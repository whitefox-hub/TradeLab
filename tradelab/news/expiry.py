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


#: Endings the operator may reverse, and the ones they may not.
#:
#: A decayed item can be reopened because its horizon is an *assumption* — §11.5
#: has not measured one yet, so an operator saying "this still matters" is real
#: information rather than an override of a finding. A manual resolution can be
#: reopened because a person made it and a person can be wrong.
#:
#: A passed schedule cannot. The date arrived; that is not a model output to
#: disagree with. Nor can a terminal process state, which ends at a transition
#: that would have to be superseded by another transition rather than by a
#: button.
REOPENABLE: frozenset[str] = frozenset({"decay", "manual"})


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
    #: Whether this ending is one a person may take back, and why not when not.
    reopenable: bool = False
    not_reopenable_because: str = ""


def resolve(
    *,
    event_type: str,
    shape: str,
    first_seen_at: datetime,
    resolved_at: datetime | None = None,
    scheduled_for: datetime | None = None,
    latest_state: str | None = None,
    reactivated_until: datetime | None = None,
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
            reopenable=True,
        )

    # 2. Superseded by a terminal process state. Goes to zero at the transition
    #    rather than fading from it (§11.8).
    if latest_state in TERMINAL_STATES:
        return Expiry(
            EndedBy.SUPERSEDED, None, active=False,
            reason=f"process reached {latest_state!r}; the signal ends at the "
                   f"transition rather than fading from it",
            not_reopenable_because=(
                "a process ends at a transition, so reversing it needs another "
                "transition rather than a button"
            ),
        )

    # 3. A regime shift does not decay, because it did not deviate from a
    #    baseline — it replaced one. Putting a clock on it would estimate a
    #    reversion that never happens, so it stays live until a person says the
    #    regime ended, which is itself a judgement no timer can make.
    if shape == "regime_shift":
        return Expiry(
            EndedBy.OPEN, None, active=True,
            reason="a regime shift replaces the baseline rather than deviating "
                   "from it; it ends when someone says the regime did",
            reopenable=False,
            not_reopenable_because="",
        )

    # 4. Scheduled, and the anticipation ends when the date arrives — whatever
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
            not_reopenable_because=(
                "" if scheduled_for > now else
                "the date arrived, which is not a model output to disagree with"
            ),
        )

    # 5. Otherwise it decays, on an assumed horizon until §11.5 measures one.
    hours = ASSUMED_DECAY_HOURS.get(event_type, DEFAULT_DECAY_HOURS)
    expires = first_seen_at + timedelta(hours=hours)

    # An operator can push the clock out, and doing so is information rather
    # than an override: the horizon above is assumed, not measured, so a person
    # saying the item still matters is the better estimate of the two.
    if reactivated_until is not None and reactivated_until > now:
        return Expiry(
            EndedBy.OPEN, reactivated_until, active=True,
            reason=f"reopened by hand until {reactivated_until.date()}, over an "
                   f"assumed {hours:.0f}h horizon",
            assumed=True, reopenable=True,
        )

    return Expiry(
        EndedBy.DECAY if expires <= now else EndedBy.OPEN,
        expires, active=expires > now,
        reason=f"assumed {hours:.0f}h decay for {event_type}, not yet measured",
        assumed=True, reopenable=True,
    )
