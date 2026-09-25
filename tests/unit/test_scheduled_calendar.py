"""Known future events. Plan.md §11.6.

No announcement feed publishes a halving three years out — it is a property of
the protocol — and for a scheduled event the tradeable object is the approach
rather than the arrival. A date added after the fact can be studied but never
traded.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.seed_scheduled_events import CALENDAR


@pytest.mark.fast
def test_every_entry_lies_in_the_future() -> None:
    """An entry whose date has passed is history, and history arrives from the
    collectors rather than from a hard-coded list."""
    now = datetime.now(UTC)
    for entry in CALENDAR:
        assert entry.scheduled_for > now, f"{entry.key} is in the past"


@pytest.mark.fast
def test_a_block_height_date_is_recorded_as_an_estimate() -> None:
    """The halving is certain; its calendar date is not. It is block-height
    based, so the date moves with hash rate, and calling that `fixed` would
    make a later correction look like a postponement rather than a refinement.
    """
    halvings = [e for e in CALENDAR if e.event_type == "halving"]
    assert halvings, "no halving in the calendar"
    for entry in halvings:
        assert entry.confidence == "estimated"
        assert "block height" in entry.source


@pytest.mark.fast
def test_each_entry_names_its_assets() -> None:
    """A schedule with no asset cannot have its anticipation measured against
    anything (§11.3)."""
    for entry in CALENDAR:
        assert entry.assets, f"{entry.key} names no asset"
        assert all(a.endswith(("USDT", "USDC")) for a in entry.assets), entry.key


@pytest.mark.fast
def test_the_keys_are_unique() -> None:
    """They are the idempotency key; a duplicate would insert a second event for
    the same occurrence on every run."""
    keys = [entry.key for entry in CALENDAR]
    assert len(keys) == len(set(keys))
