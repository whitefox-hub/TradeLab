"""A broad market index, daily. Plan.md §20, PREREG-005.

PREREG-005 measures whether the operator's reading of the news predicts where
the market goes, and registered BTCUSDT as the market — noting in its own
confounds that it is "a poor proxy in an alt-led move". It was used because it
was the series already collected.

CMC100 is a hundred-constituent, capitalisation-weighted index, and what the
operator's trend is a claim about. Stored in its own table rather than in
`macro_series`, for the same reason the sentiment indices are: it is a vendor
construct, it rebalances, and an index return is not a return anyone earned.
It is a yardstick, not an instrument.

Keyed on (day, source) from the start. `sentiment_readings` was keyed on the day
alone and had to be migrated when a second vendor arrived; there is no reason to
repeat that.
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
        # The daily snapshot carries a midnight timestamp and is knowable then,
        # so availability is the timestamp itself rather than a guess.
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
