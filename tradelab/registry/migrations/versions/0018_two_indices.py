"""Two fear-and-greed indices, one per row. Plan.md §11.4, §4.3.

The operator is going to record CoinMarketCap's index by hand each day, beside
the alternative.me series this already collects. They are **different indices**
— different methods, different numbers, and on 2026-09-26 they printed 72 and 74
for the same day — so they are two series and not two opinions about one.

The primary key was the day alone, which made them collide: the second one
written would have silently replaced the first, and the table would have held a
mixture nobody could unpick. It becomes (day, source).

`source` was already a column, describing where a row came from. It is now part
of the key, which is what it always meant.
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
    # Reads are "the latest of one series", so the index that serves them leads
    # with the source. The old one led with availability and served a question
    # nobody asks now that there is more than one series.
    op.create_index('ix_sentiment_source_day', 'sentiment_readings',
                    ['source', 'day'])


def downgrade() -> None:
    op.drop_index('ix_sentiment_source_day', table_name='sentiment_readings')
    op.drop_constraint('sentiment_readings_pkey', 'sentiment_readings',
                       type_='primary')
    op.create_primary_key('sentiment_readings_pkey', 'sentiment_readings', ['day'])
