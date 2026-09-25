"""The event's own text. Plan.md §11.1, §20.5.

A column rather than an annotation: `event_annotations` holds what a person
asserted about an event, and a collected headline is the event's own words.
Filing it there would also make every collected event look annotated, which is
the flag the news screen uses to find the ones nobody has read.
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
    # Lift the titles already recorded as hand-entry corrections, so the three
    # seeded calendar events do not have to be re-entered.
    op.execute("""
        UPDATE events e SET headline = a.payload->>'title'
          FROM event_annotations a
         WHERE a.event_id = e.id
           AND a.payload ? 'title'
           AND e.headline IS NULL
    """)


def downgrade() -> None:
    op.drop_column('events', 'headline')
