"""One wave, not one per asset, and NORMAL is where it sits. Plan.md §20.

The per-asset table was the wrong shape. A wave is the market's pressure, so
there is one of it, and the question it answers is "what is the book leaning
into right now" rather than "what do I believe about ADA".

`NORMAL` is the default and it is a real third state, not the absence of one. A
wave that has to be either UP or DOWN forces a lean the operator may not hold,
and an operator who must pick a side will pick one.

Magnitude 0 and NORMAL are the same fact, so the table refuses to hold them
apart: a NORMAL wave of magnitude 7 would be read downstream as pressure with no
direction, and a DOWN wave of magnitude 0 as direction with no pressure. Both
are states the engine would have to invent a meaning for.

The old table is dropped rather than migrated. It was created in 0011, in this
same cycle, and holds nothing.
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
        # A fixed primary key is the singleton: a second row has to claim id 1
        # and collides. Enforced in the schema rather than by the API always
        # remembering to UPDATE instead of INSERT.
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
    # Seeded, so the screen and the engine always have a wave to read. An absent
    # row would make "no pressure" and "nobody has set it" the same query
    # result, and they are different facts.
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
