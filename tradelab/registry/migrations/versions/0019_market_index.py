"""A broad market index, daily. Plan.md §20, PREREG-005.

CMC100 as the market yardstick (never traded), outside `macro_series` as a vendor
construct. Keyed on (day, source).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'market_index_readings',
        sa.Column('day', sa.Date(), primary_key=True),
        sa.Column('source', sa.Text(), primary_key=True),
        sa.Column('value', sa.Numeric(18, 6), nullable=False),
        # The snapshot's own midnight timestamp, when it became knowable.
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ingest_time', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.CheckConstraint('value > 0', name='ck_market_index_value'),
    )
    op.create_index('ix_market_index_source_day', 'market_index_readings',
                    ['source', 'day'])


def downgrade() -> None:
    op.drop_index('ix_market_index_source_day', table_name='market_index_readings')
    op.drop_table('market_index_readings')
