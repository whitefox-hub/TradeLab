"""The reaction measurement job. Plan.md §11.3."""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_reactions.py"


@pytest.mark.fast
def test_it_only_claims_horizons_a_daily_bar_can_carry() -> None:
    """The §11 grid starts at five minutes. Read off a daily close, a
    five-minute reaction is not a five-minute reaction, so the short horizons
    are left unmeasured rather than approximated."""
    body = SCRIPT.read_text()
    assert "DAILY_HORIZONS = (-1440, 1440, 4320, 10080)" in body
    assert "horizons=DAILY_HORIZONS" in body


@pytest.mark.fast
def test_skips_are_counted_by_reason() -> None:
    """"skipped 60" says nothing about whether the measurement is broken or the
    events are unmeasurable, and those want opposite responses."""
    body = SCRIPT.read_text()
    assert "def skip(reason: str)" in body
    assert "no pre-event history" in body


@pytest.mark.fast
def test_a_future_event_is_not_measured() -> None:
    """It has no reaction yet, and measuring one reads a window that does not
    exist."""
    assert 'if row["first_seen_at"] > datetime.now(UTC):' in SCRIPT.read_text()


@pytest.mark.fast
def test_days_are_joined_never_filled() -> None:
    """A missing bar is an absence; pairing it with a zero invents a quiet day
    and pulls every statistic toward calm."""
    body = SCRIPT.read_text()
    assert "np.intersect1d" in body
    assert "fill_null" not in body and "forward_fill" not in body


@pytest.mark.fast
def test_the_estimator_version_travels_with_every_row() -> None:
    """A corpus that cannot say what measured it cannot be compared across a
    change to the estimator."""
    body = SCRIPT.read_text()
    assert "def code_hash()" in body
    assert "code_hash = EXCLUDED.code_hash" in body
