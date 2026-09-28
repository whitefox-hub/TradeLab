"""Two fear-and-greed indices, one per row. Plan.md §11.4, §4.3.

PK becomes (day, source): CoinMarketCap and alternative.me are different series
and would otherwise overwrite each other.
"""

from __future__ import annotations

from alembic import op

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('sentiment_readings_pkey', 'sentiment_readings',
                       type_='primary')
    op.create_primary_key('sentiment_readings_pkey', 'sentiment_readings',
                          ['day', 'source'])
    # Reads are "latest of one series", so the index leads with source.
    op.create_index('ix_sentiment_source_day', 'sentiment_readings',
                    ['source', 'day'])


def downgrade() -> None:
    op.drop_index('ix_sentiment_source_day', table_name='sentiment_readings')
    op.drop_constraint('sentiment_readings_pkey', 'sentiment_readings',
                       type_='primary')
    op.create_primary_key('sentiment_readings_pkey', 'sentiment_readings', ['day'])
