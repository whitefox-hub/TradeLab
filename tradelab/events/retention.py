"""How far back the event corpus is kept. Plan.md §11.8, §12.4.

One operator-set window, read by both ingestor and prune: the ingestor is idempotent, so a
prune alone would be undone next run. Pruned rows rebuild from the Parquet, except human
annotations.
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
