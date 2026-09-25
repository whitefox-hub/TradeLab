"""Event intelligence invariants. Plan.md §11, §20.5.

Both of these live in CHECK constraints rather than in application code, so the
tests go through the database. The point of putting them there is that nothing
which writes to these tables — the API, a collector, a notebook — can bypass
them, and a test that only exercised one writer would not show that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from tradelab.config import Settings

NOW = datetime.now(UTC)


@pytest.fixture
def engine():
    return sa.create_engine(Settings().database_url)


@pytest.fixture
def event(engine):
    """An event and an operator to hang annotations from, removed afterwards."""
    event_id, user_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (id, email, role) VALUES (:i, :e, 'admin')"),
            {"i": user_id, "e": f"probe-{user_id.hex[:8]}@example.com"})
        connection.execute(sa.text(
            "INSERT INTO events (id, event_type, shape, first_seen_at, cluster_method, "
            "code_hash) VALUES (:i, 'listing', 'shock', :t, 'exact', 'probe')"),
            {"i": event_id, "t": NOW})
    yield event_id, user_id
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM event_annotations WHERE event_id = :i"),
                           {"i": event_id})
        connection.execute(sa.text("DELETE FROM schedule_revisions WHERE event_id = :i"),
                           {"i": event_id})
        connection.execute(sa.text("DELETE FROM events WHERE id = :i"), {"i": event_id})
        connection.execute(sa.text("DELETE FROM users WHERE id = :i"), {"i": user_id})


def _annotate(engine, event_id, user_id, kind, available_at, rating=None):
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO event_annotations (id, event_id, kind, available_at, payload, "
            "rating, author) VALUES (:i, :e, :k, :a, '{}', :r, :u)"),
            {"i": uuid.uuid4(), "e": event_id, "k": kind, "a": available_at,
             "r": rating, "u": user_id})


@pytest.mark.integration
def test_a_judgement_cannot_be_backdated(engine, event) -> None:
    """A judgement dated before it was written is hindsight with a form around it.

    This is the constraint that makes the ratings worth collecting at all: a
    rating whose availability can be moved earlier scores perfectly against every
    event, and the annotator's calibration becomes unknowable.
    """
    event_id, user_id = event
    with pytest.raises(sa.exc.IntegrityError, match="ck_judgement_is_not_backdated"):
        _annotate(engine, event_id, user_id, "judgement",
                  NOW - timedelta(days=3), rating=2.0)


@pytest.mark.integration
def test_a_judgement_made_now_is_accepted(engine, event) -> None:
    """The control. Without it the test above passes on any rejection at all."""
    event_id, user_id = event
    _annotate(engine, event_id, user_id, "judgement", datetime.now(UTC), rating=2.0)
    with engine.connect() as connection:
        count = connection.execute(sa.text(
            "SELECT count(*) FROM event_annotations WHERE event_id = :i"),
            {"i": event_id}).scalar()
    assert count == 1


@pytest.mark.integration
def test_a_correction_may_be_backdated_because_the_value_was_always_there(
    engine, event
) -> None:
    """The asymmetry is the design. A correction repairs a parse failure, so it
    inherits the event's own availability; a judgement creates information at the
    moment it is made."""
    event_id, user_id = event
    _annotate(engine, event_id, user_id, "correction", NOW - timedelta(days=3))
    with engine.connect() as connection:
        kinds = connection.execute(sa.text(
            "SELECT kind FROM event_annotations WHERE event_id = :i"),
            {"i": event_id}).scalars().all()
    assert kinds == ["correction"]


@pytest.mark.integration
def test_confidence_stays_a_probability(engine, event) -> None:
    event_id, user_id = event
    with (
        pytest.raises(sa.exc.IntegrityError, match="ck_annotation_confidence"),
        engine.begin() as connection,
    ):
            connection.execute(sa.text(
                "INSERT INTO event_annotations (id, event_id, kind, available_at, "
                "payload, confidence, author) "
                "VALUES (:i, :e, 'judgement', now(), '{}', 1.5, :u)"),
                {"i": uuid.uuid4(), "e": event_id, "u": user_id})


@pytest.mark.integration
def test_a_schedule_revision_records_the_date_believed_at_the_time(engine, event) -> None:
    """§11.6: computing "days until the event" from the date it eventually
    happened on assumes the market knew a date it did not."""
    event_id, _ = event
    with engine.begin() as connection:
        for revision, (available, scheduled, confidence) in enumerate([
            (NOW - timedelta(days=30), NOW + timedelta(days=10), "rumoured"),
            (NOW - timedelta(days=5), NOW + timedelta(days=45), "fixed"),
        ], start=1):
            connection.execute(sa.text(
                "INSERT INTO schedule_revisions (event_id, revision, available_at, "
                "scheduled_for, confidence) VALUES (:e, :r, :a, :s, :c)"),
                {"e": event_id, "r": revision, "a": available, "s": scheduled,
                 "c": confidence})

    with engine.connect() as connection:
        rows = connection.execute(sa.text(
            "SELECT scheduled_for FROM schedule_revisions WHERE event_id = :e "
            "AND available_at <= :t ORDER BY revision"), {"e": event_id,
            "t": NOW - timedelta(days=10)}).scalars().all()
    # As of ten days ago only the first revision existed, so the date the market
    # could have known is the rumoured one — not the postponed one it became.
    assert len(rows) == 1


@pytest.mark.integration
def test_an_unknown_shape_is_refused(engine) -> None:
    """Shape decides which machinery applies, so a typo must not create a fourth
    kind that nothing handles."""
    with (
        pytest.raises(sa.exc.IntegrityError, match="ck_events_shape"),
        engine.begin() as connection,
    ):
            connection.execute(sa.text(
                "INSERT INTO events (id, event_type, shape, first_seen_at, "
                "cluster_method, code_hash) "
                "VALUES (:i, 'listing', 'sudden', now(), 'exact', 'probe')"),
                {"i": uuid.uuid4()})
