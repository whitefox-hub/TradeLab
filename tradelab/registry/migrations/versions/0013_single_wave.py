"""One wave, not one per asset, and NORMAL is where it sits. Plan.md §20.

Replaces per-asset `waves` (dropped, it held nothing) with a singleton market wave.
`NORMAL` is the default, and a CHECK ties NORMAL to magnitude 0.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('waves')
    op.create_table(
        'market_wave',
        sa.Column('id', sa.SmallInteger(), primary_key=True,
                  server_default=sa.text('1')),
        sa.Column('direction', sa.Text(), nullable=False),
        sa.Column('magnitude', sa.SmallInteger(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_market_wave_singleton'),
        sa.CheckConstraint("direction IN ('UP','NORMAL','DOWN')",
                           name='ck_market_wave_direction'),
        sa.CheckConstraint('magnitude BETWEEN 0 AND 10',
                           name='ck_market_wave_magnitude'),
        sa.CheckConstraint("(direction = 'NORMAL') = (magnitude = 0)",
                           name='ck_market_wave_normal_is_zero'),
    )
    op.execute("INSERT INTO market_wave (id, direction, magnitude) "
               "VALUES (1, 'NORMAL', 0)")


def downgrade() -> None:
    op.drop_table('market_wave')
    op.create_table(
        'waves',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('asset', sa.Text(), nullable=False),
        sa.Column('direction', sa.Text(), nullable=False),
        sa.Column('magnitude', sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("direction IN ('UP','DOWN')", name='ck_waves_direction'),
        sa.CheckConstraint('magnitude BETWEEN 1 AND 10', name='ck_waves_magnitude'),
        sa.CheckConstraint("asset = upper(asset) AND asset ~ '^[A-Z0-9]{1,20}$'",
                           name='ck_waves_asset'),
        sa.UniqueConstraint('asset', name='uq_waves_asset'),
    )
