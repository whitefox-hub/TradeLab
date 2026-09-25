"""The fourth event shape. Plan.md §11.7.

A war, a venue collapse or an outright ban does not deviate from a baseline and
return to it — it replaces the baseline. Fitting a decay to one estimates a
reversion that never happens, so it needs its own shape rather than being filed
as a long shock.

Alembic does not diff CHECK constraints, so this is written by hand.
"""

from __future__ import annotations

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS ck_events_shape")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_shape "
        "CHECK (shape IN ('shock','schedule','process','regime_shift'))"
    )


def downgrade() -> None:
    # Anything already filed as a regime shift has no home in the old set, so it
    # would block the constraint. Named rather than silently reclassified.
    op.execute("DELETE FROM events WHERE shape = 'regime_shift'")
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS ck_events_shape")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_shape "
        "CHECK (shape IN ('shock','schedule','process'))"
    )
