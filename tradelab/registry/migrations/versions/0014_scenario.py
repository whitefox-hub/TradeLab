"""The scenario: the wave and the sentiment a simulation runs under. §20.

Renames `market_wave` to `market_conditions`, prefixes wave columns, adds an assumed
`sentiment` (default 50), distinct from measured `sentiment_readings`.
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
