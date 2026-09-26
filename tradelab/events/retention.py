"""How far back the event corpus is kept. Plan.md §11.8, §12.4.

One window, read by both the ingestor and the prune, because they have to agree:
the ingestor is idempotent by article id, so deleting rows without narrowing what
gets imported leaves the next run putting them straight back. A prune that undoes
itself on the next tick is worse than none, since it looks done.

The window is an operator setting rather than a constant, and it is the lever
that decides how much history the event-reaction work of §11.5 will have to fit
on. Shortening it makes the screens quiet; it also shortens the record that
hypothesis 5 of §0.4 depends on, which is a trade the operator makes knowingly
rather than one buried in a script.

Nothing here is irreversible. The announcement Parquet is the source of truth,
so raising the window and re-running the ingestor rebuilds exactly what was
removed. Human annotations are the one exception — they are not derived from
anything and cannot be rebuilt.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

SETTING = "event_retention_days"
DEFAULT_DAYS = 180


def retention_days(connection: sa.Connection) -> int:
    """The current window, or the default when none has been recorded."""
    stored = connection.execute(sa.text(
        "SELECT value FROM app_settings WHERE key = :k "
        "ORDER BY valid_from DESC LIMIT 1"), {"k": SETTING}).scalar()
    if stored is None:
        return DEFAULT_DAYS
    value = stored if isinstance(stored, dict) else json.loads(stored)
    return int(value.get("days", DEFAULT_DAYS))
