"""Which event classes are routine. Plan.md §11.1, §20.5.

A venue lists perpetuals most weeks. Individually those announcements carry
almost nothing an operator would read — what makes them valuable is that there
are hundreds of them, which is a property of the *set* and not of any row.

That is the distinction this module draws, and it is not "old" or "duplicate":
there are no duplicates here. A hack is worth reading once; a listing is worth
counting. A screen that shows both the same way buries the first under forty of
the second, and the operator stops reading the list at all — which costs more
than any row does.

Routine events are kept, measured and studied exactly as before. They are only
folded away in the default view, because attention is the scarce thing on this
screen and it should be spent on what is unusual.
"""

from __future__ import annotations

#: Classes a venue produces on a schedule. High volume, low information per row.
ROUTINE_TYPES: frozenset[str] = frozenset({
    "futures_listing",
    "spot_listing",
    "futures_delivery",
    "seed_tag",
    "margin_change",
})


def is_routine(event_type: str) -> bool:
    """Whether this class is produced in bulk by a collector.

    Keyed on the class rather than on how many rows happen to exist, so a quiet
    week does not promote listings to interesting and a busy one does not demote
    a war.
    """
    return event_type in ROUTINE_TYPES
