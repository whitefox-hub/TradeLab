"""Folding routine events away. Plan.md §11.1, §20.5."""

from __future__ import annotations

import pytest

from tradelab.news.routine import ROUTINE_TYPES, is_routine


@pytest.mark.fast
def test_bulk_collector_output_is_routine() -> None:
    """A venue lists perpetuals most weeks. Those rows carry almost nothing an
    operator would read individually — what makes them valuable is that there
    are hundreds, which is a property of the set and not of any row."""
    for event_type in ("futures_listing", "spot_listing", "seed_tag"):
        assert is_routine(event_type)


@pytest.mark.fast
def test_the_events_worth_reading_are_not() -> None:
    """A hack is worth reading once; a listing is worth counting. A screen that
    shows both the same way buries the first under forty of the second."""
    for event_type in ("hack", "war", "regulation", "halving",
                       "exchange_collapse", "financial_crisis"):
        assert not is_routine(event_type)


@pytest.mark.fast
def test_it_keys_on_the_class_not_on_how_many_rows_exist() -> None:
    """Otherwise a quiet week promotes listings to interesting and a busy one
    demotes a war."""
    from pathlib import Path

    import tradelab.news.routine as module

    source = Path(module.__file__).read_text()
    assert "count" not in source.split('"""')[2], (
        "the flag depends on volume rather than on class"
    )
    assert isinstance(ROUTINE_TYPES, frozenset)


@pytest.mark.fast
def test_an_unknown_type_is_not_routine() -> None:
    """Default to visible. A hand-entered class nobody listed should surface,
    not vanish — the cost of showing one row too many is far below the cost of
    hiding one that mattered."""
    assert not is_routine("something_new")
    assert not is_routine("")
