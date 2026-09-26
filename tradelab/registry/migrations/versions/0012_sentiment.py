"""Fear and Greed, stored with the date it was knowable on. Plan.md §11.4, §4.3.

§11.4 argues against a bought sentiment index and it is right about why: this one
is a black box, it is composed partly of inputs already held here, and nothing
about its method is auditable. So it is stored where it can be *looked at* and
not where a study would reach for it by accident — `macro_series` is the
point-in-time corpus and this deliberately does not join it.

What can be made honest is the stamp. The index publishes one value per UTC day
at 00:00 and does not revise it, so `available_at` is that midnight and a
backtest reading this table has no way to see a value before it existed. The
part that cannot be made honest is the method, which is why `source` is a column
and not an assumption.
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
        # When it became knowable, which is the only column a study may filter
        # on. Separate from ingest_time so a late backfill cannot make an old
        # value look like it arrived late (§4.3).
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
