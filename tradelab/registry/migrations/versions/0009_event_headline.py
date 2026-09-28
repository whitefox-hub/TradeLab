"""The event's own text. Plan.md §11.1, §20.5.

A column, not an annotation: annotations are human assertions, and the news screen
uses "has annotation" to find unread events.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('events', sa.Column('headline', sa.Text(), nullable=True))
    # Backfill from titles recorded as hand-entry corrections.
    op.execute("""
        UPDATE events e SET headline = a.payload->>'title'
          FROM event_annotations a
         WHERE a.event_id = e.id
           AND a.payload ? 'title'
           AND e.headline IS NULL
    """)


def downgrade() -> None:
    op.drop_column('events', 'headline')
