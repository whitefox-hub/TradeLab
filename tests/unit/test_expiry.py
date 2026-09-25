"""When a news item stops being information. Plan.md §11.8.

Three distinct endings, and the tests keep them distinct — conflating them is
how a model keeps trading information that stopped being information.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradelab.news.expiry import ASSUMED_DECAY_HOURS, EndedBy, resolve

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.mark.fast
def test_a_shock_decays_on_its_own_clock() -> None:
    fresh = resolve(event_type="futures_listing", shape="shock",
                    first_seen_at=NOW - timedelta(hours=10), now=NOW)
    spent = resolve(event_type="futures_listing", shape="shock",
                    first_seen_at=NOW - timedelta(days=10), now=NOW)
    assert fresh.active and fresh.ended_by is EndedBy.OPEN
    assert not spent.active and spent.ended_by is EndedBy.DECAY


@pytest.mark.fast
def test_old_is_not_the_same_as_expired() -> None:
    """The distinction the tabs exist for.

    A delisting scheduled three months out was announced ninety days ago and is
    entirely live; a listing from this morning can already be spent. Sorting by
    age puts them the wrong way round.
    """
    old_but_live = resolve(
        event_type="spot_delisting", shape="schedule",
        first_seen_at=NOW - timedelta(days=90),
        scheduled_for=NOW + timedelta(days=40), now=NOW)
    recent_but_done = resolve(
        event_type="trading_halt", shape="shock",
        first_seen_at=NOW - timedelta(hours=20), now=NOW)

    assert old_but_live.active, "a schedule ahead of us is still information"
    assert not recent_but_done.active, "a 12h decay does not survive 20 hours"


@pytest.mark.fast
def test_a_schedule_ends_discontinuously_when_its_date_arrives() -> None:
    """Not decay: the thing being anticipated no longer lies ahead, whatever the
    outcome was."""
    before = resolve(event_type="unlock", shape="schedule", first_seen_at=NOW,
                     scheduled_for=NOW + timedelta(minutes=1), now=NOW)
    after = resolve(event_type="unlock", shape="schedule", first_seen_at=NOW,
                    scheduled_for=NOW - timedelta(minutes=1), now=NOW)
    assert before.active and after.active is False
    assert after.ended_by is EndedBy.RESOLVED
    assert "no longer lies ahead" in after.reason


@pytest.mark.fast
def test_a_withdrawn_process_goes_to_zero_rather_than_fading() -> None:
    """A rumoured partnership that is denied does not decay, it is cancelled."""
    result = resolve(event_type="regulation", shape="process",
                     first_seen_at=NOW - timedelta(hours=1),
                     latest_state="withdrawn", now=NOW)
    assert not result.active
    assert result.ended_by is EndedBy.SUPERSEDED
    # And the decay clock had plenty of time left on it, which is the point.
    still_ticking = resolve(event_type="regulation", shape="process",
                            first_seen_at=NOW - timedelta(hours=1), now=NOW)
    assert still_ticking.active


@pytest.mark.fast
def test_a_recorded_resolution_beats_every_clock() -> None:
    """Someone said this ended. A judgement about the world beats a timer."""
    result = resolve(event_type="hack", shape="shock", first_seen_at=NOW,
                     resolved_at=NOW - timedelta(minutes=5), now=NOW)
    assert not result.active
    assert result.ended_by is EndedBy.RESOLVED


@pytest.mark.fast
def test_the_decay_horizon_is_flagged_as_assumed() -> None:
    """§11.5 fits τ_slow from local projections and has not run. Until it does
    these are defaults, and a default presented as a finding is worse than no
    default at all."""
    decayed = resolve(event_type="futures_listing", shape="shock",
                      first_seen_at=NOW, now=NOW)
    assert decayed.assumed is True
    assert "not yet measured" in decayed.reason

    # The other two endings are dates, not rates, so nothing is assumed there.
    scheduled = resolve(event_type="unlock", shape="schedule", first_seen_at=NOW,
                        scheduled_for=NOW + timedelta(days=5), now=NOW)
    assert scheduled.assumed is False


@pytest.mark.fast
def test_an_unknown_event_type_still_gets_a_horizon() -> None:
    """Hand-entered types are arbitrary strings, and returning None here would
    make every one of them permanently active."""
    result = resolve(event_type="something_nobody_listed", shape="shock",
                     first_seen_at=NOW - timedelta(days=30), now=NOW)
    assert not result.active
    assert result.expires_at is not None


@pytest.mark.fast
def test_the_horizons_sit_inside_the_tradeable_band() -> None:
    """§0.2 puts the viable holding band at 2-7 days, and §11 observes that news
    residuals decay over hours to days. A horizon of weeks would keep positions
    open on information the cost model says cannot be monetised."""
    for event_type, hours in ASSUMED_DECAY_HOURS.items():
        assert 1 <= hours <= 336, f"{event_type} at {hours}h is outside hours-to-days"


@pytest.mark.fast
def test_only_the_endings_the_system_asserted_can_be_taken_back() -> None:
    """The difference is whether the system was asserting or observing.

    A decay horizon is an assumption §11.5 has not measured, so an operator
    disagreeing with it is the better of two estimates. A date that arrived is
    not a model output to disagree with.
    """
    decayed = resolve(event_type="futures_listing", shape="shock",
                      first_seen_at=NOW - timedelta(days=10), now=NOW)
    manual = resolve(event_type="hack", shape="shock", first_seen_at=NOW,
                     resolved_at=NOW - timedelta(hours=1), now=NOW)
    passed = resolve(event_type="unlock", shape="schedule",
                     first_seen_at=NOW - timedelta(days=30),
                     scheduled_for=NOW - timedelta(days=1), now=NOW)
    withdrawn = resolve(event_type="regulation", shape="process",
                        first_seen_at=NOW, latest_state="withdrawn", now=NOW)

    assert decayed.reopenable and manual.reopenable
    assert not passed.reopenable and not withdrawn.reopenable
    # And each refusal says why, because a disabled control with no reason is
    # one the operator works around.
    assert "date arrived" in passed.not_reopenable_because
    assert "transition" in withdrawn.not_reopenable_because


@pytest.mark.fast
def test_a_reopened_item_is_active_over_its_assumed_horizon() -> None:
    reopened = resolve(event_type="futures_listing", shape="shock",
                       first_seen_at=NOW - timedelta(days=30),
                       reactivated_until=NOW + timedelta(days=3), now=NOW)
    assert reopened.active
    assert "reopened by hand" in reopened.reason
    # An expired reactivation does not keep it alive for ever.
    lapsed = resolve(event_type="futures_listing", shape="shock",
                     first_seen_at=NOW - timedelta(days=30),
                     reactivated_until=NOW - timedelta(days=1), now=NOW)
    assert not lapsed.active
