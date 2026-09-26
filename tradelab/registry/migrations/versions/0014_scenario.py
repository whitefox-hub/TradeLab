"""The scenario: the wave and the sentiment a simulation runs under. §20.

`market_wave` held one dial. There are two, so the table is renamed for what it
actually is — the market conditions the operator is testing under — and the wave
columns are prefixed so that `direction` and `magnitude` cannot be read as
belonging to the sentiment, which has neither.

**Sentiment here is not the Fear and Greed reading.** That one is a measurement:
dated, published once a day, and never revised, and it lives in
`sentiment_readings` where a study could in principle read it as of a moment.
This one is an assumption with no date at all. Holding them in one column would
make a measurement and an opinion indistinguishable, which is the failure the
hand-entered event corpus was kept separate from the collected one to avoid, and
the reason that separation is worth repeating here rather than economising on a
column.

The default is 50 and not today's printed value. Seeding an assumption from a
measurement would make "nobody has set this" and "someone chose the market's
own number" the same row, and only one of them is a decision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table('market_wave', 'market_conditions')
    op.alter_column('market_conditions', 'direction', new_column_name='wave_direction')
    op.alter_column('market_conditions', 'magnitude', new_column_name='wave_magnitude')

    # Constraints carry the old table's name in theirs, which reads as a lie
    # once the table is renamed and would send the next person looking for a
    # table that is gone.
    for old, new in (
        ('ck_market_wave_singleton', 'ck_market_conditions_singleton'),
        ('ck_market_wave_direction', 'ck_market_conditions_wave_direction'),
        ('ck_market_wave_magnitude', 'ck_market_conditions_wave_magnitude'),
        ('ck_market_wave_normal_is_zero', 'ck_market_conditions_normal_is_zero'),
        ('market_wave_pkey', 'market_conditions_pkey'),
    ):
        op.execute(f'ALTER TABLE market_conditions RENAME CONSTRAINT {old} TO {new}')

    op.add_column('market_conditions', sa.Column(
        'sentiment', sa.SmallInteger(), nullable=False, server_default=sa.text('50')))
    op.create_check_constraint(
        'ck_market_conditions_sentiment', 'market_conditions',
        'sentiment BETWEEN 1 AND 100')


def downgrade() -> None:
    op.drop_constraint('ck_market_conditions_sentiment', 'market_conditions')
    op.drop_column('market_conditions', 'sentiment')
    for new, old in (
        ('ck_market_conditions_singleton', 'ck_market_wave_singleton'),
        ('ck_market_conditions_wave_direction', 'ck_market_wave_direction'),
        ('ck_market_conditions_wave_magnitude', 'ck_market_wave_magnitude'),
        ('ck_market_conditions_normal_is_zero', 'ck_market_wave_normal_is_zero'),
        ('market_conditions_pkey', 'market_wave_pkey'),
    ):
        op.execute(f'ALTER TABLE market_conditions RENAME CONSTRAINT {new} TO {old}')
    op.alter_column('market_conditions', 'wave_direction', new_column_name='direction')
    op.alter_column('market_conditions', 'wave_magnitude', new_column_name='magnitude')
    op.rename_table('market_conditions', 'market_wave')
