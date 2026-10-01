"""Fear and Greed, stored with the date it was knowable on. Plan.md §11.4, §4.3.

Display only, deliberately outside `macro_series` (§11.4: unauditable method).
`available_at` is the UTC midnight of the day: published once daily, never revised.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sentiment_readings',
        sa.Column('day', sa.Date(), primary_key=True),
        sa.Column('value', sa.SmallInteger(), nullable=False),
        sa.Column('classification', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ingest_time', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.CheckConstraint('value BETWEEN 0 AND 100', name='ck_sentiment_value'),
        sa.CheckConstraint(
            "classification IN ('Extreme Fear','Fear','Neutral','Greed','Extreme Greed')",
            name='ck_sentiment_classification'),
    )
    op.create_index('ix_sentiment_available', 'sentiment_readings', ['available_at'])


def downgrade() -> None:
    op.drop_index('ix_sentiment_available', table_name='sentiment_readings')
    op.drop_table('sentiment_readings')
