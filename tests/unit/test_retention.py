"""The event retention window. Plan.md §11.8, §12.4.

One window read by two scripts, because they have to agree: the ingestor is
idempotent by article id, so deleting rows without narrowing what gets imported
leaves the next run putting them straight back.
"""

from __future__ import annotations

import pytest

from tradelab.api.settings_rules import Invalid, validate
from tradelab.events.retention import DEFAULT_DAYS, SETTING


@pytest.mark.fast
def test_both_scripts_read_the_same_window() -> None:
    """A prune that undoes itself on the next cron tick is worse than none,
    because it looks done."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "scripts"
    for name in ("prune_events.py", "ingest_events.py"):
        body = (root / name).read_text()
        assert "from tradelab.events.retention import retention_days" in body, name


@pytest.mark.fast
def test_the_window_is_an_operator_setting_with_bounds() -> None:
    validate(SETTING, {"days": 180})
    with pytest.raises(Invalid, match="at least 30"):
        validate(SETTING, {"days": 7})
    with pytest.raises(Invalid):
        validate(SETTING, {"days": "muchos"})


@pytest.mark.fast
def test_a_month_is_the_floor_and_the_reason_is_stated() -> None:
    """Below a month the corpus cannot carry an event class through a cycle, and
    §11.5 needs history to fit an impulse response on. The refusal says so rather
    than quoting a number."""
    with pytest.raises(Invalid) as raised:
        validate(SETTING, {"days": 10})
    assert "11.5" in str(raised.value) or "impulse response" in str(raised.value)


@pytest.mark.fast
def test_the_prune_spares_anything_carrying_a_judgement() -> None:
    """Events are derived from the Parquet archive and can be rebuilt exactly.
    A human judgement is derived from nothing and cannot.

    Kept although the screen that recorded judgements is gone: the ones already
    in the table are still the only rows here no re-run can reconstruct, and a
    prune that takes them is the one deletion in this script that is final.
    """
    from pathlib import Path

    body = (Path(__file__).resolve().parents[2] / "scripts" / "prune_events.py").read_text()
    assert "kind = 'judgement'" in body
    assert "force-annotated" in body


@pytest.mark.fast
def test_age_is_the_whole_test_now_that_no_screen_shows_an_expiry() -> None:
    """The prune used to spare an event the news screen still considered live,
    on top of sparing anything inside the retention window. That second guard
    only meant something while a page was displaying the first, so it went with
    the page — and this asserts it went, rather than leaving a clock nothing
    winds."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[2] / "scripts" / "prune_events.py").read_text()
    assert "expiry" not in body.split('"""')[2], "the expiry clock survives in the code"
    assert "past_retention" in body


@pytest.mark.fast
def test_the_default_is_stated_once() -> None:
    assert DEFAULT_DAYS == 180
