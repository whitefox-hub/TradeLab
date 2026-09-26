"""Measured event reactions. Plan.md §11.3.

One row per event, asset and horizon. A reaction is not one number: a move that
is large at an hour and gone by a day is a different object from one that
persists, and that distinction is what §11.5 fits a decay to.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'event_reactions',
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('asset', sa.String(length=32), nullable=False),
        sa.Column('horizon_minutes', sa.Integer(), nullable=False),
        sa.Column('car', sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column('z', sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column('benchmark_kind', sa.String(length=20), nullable=False),
        sa.Column('beta_pre', sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column('sigma_pre', sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column('n_bars', sa.Integer(), nullable=False),
        sa.Column('leaked', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('measured_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('code_hash', sa.Text(), nullable=False),
        sa.CheckConstraint(
            "benchmark_kind IN ('cross_sectional','time_series')",
            name='ck_reaction_benchmark'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ),
        sa.PrimaryKeyConstraint('event_id', 'asset', 'horizon_minutes'),
    )
    op.create_index('ix_reactions_horizon', 'event_reactions',
                    ['horizon_minutes', 'z'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reactions_horizon', table_name='event_reactions')
    op.drop_table('event_reactions')
