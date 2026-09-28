"""The fourth event shape. Plan.md §11.7.

Adds `regime_shift`: it replaces the baseline, so no decay is fit. Hand-written: Alembic
does not diff CHECK constraints.
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
    # Rows with regime_shift would violate the old constraint; deleted, not reclassified.
    op.execute("DELETE FROM events WHERE shape = 'regime_shift'")
    op.execute("ALTER TABLE events DROP CONSTRAINT IF EXISTS ck_events_shape")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_shape "
        "CHECK (shape IN ('shock','schedule','process'))"
    )
