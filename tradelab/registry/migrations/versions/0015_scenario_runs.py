"""What the algorithm did under a set of conditions. Plan.md §20, §15.

Deliberately not `runs`: a simulation must never read as a position. Each row
records the step reached and why it stopped (§15).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scenario_runs',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('ran_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('actor', sa.Text(), nullable=False),
        # Conditions copied, not joined: market_conditions is one mutable row.
        sa.Column('wave_direction', sa.Text(), nullable=False),
        sa.Column('wave_magnitude', sa.SmallInteger(), nullable=False),
        sa.Column('sentiment', sa.SmallInteger(), nullable=False),
        sa.Column('sentiment_bucket', sa.Text(), nullable=False),
        # Stored so results read without re-deriving the transform.
        sa.Column('long_multiplier', sa.Numeric(6, 4), nullable=False),
        sa.Column('short_multiplier', sa.Numeric(6, 4), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('bars', sa.Integer(), nullable=False),
        # Both arms; the difference is the operator's effect.
        sa.Column('baseline', sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column('conditioned', sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column('reached_step', sa.SmallInteger(), nullable=False),
        sa.Column('stopped_because', sa.Text(), nullable=False),
        sa.CheckConstraint("wave_direction IN ('UP','NORMAL','DOWN')",
                           name='ck_scenario_runs_direction'),
        sa.CheckConstraint('wave_magnitude BETWEEN 0 AND 10',
                           name='ck_scenario_runs_magnitude'),
        sa.CheckConstraint('sentiment BETWEEN 1 AND 100',
                           name='ck_scenario_runs_sentiment'),
        # No mode column by design: a scenario can never be 'live'.
        sa.CheckConstraint('bars > 0', name='ck_scenario_runs_bars'),
    )
    op.create_index('ix_scenario_runs_ran_at', 'scenario_runs', ['ran_at'])


def downgrade() -> None:
    op.drop_index('ix_scenario_runs_ran_at', table_name='scenario_runs')
    op.drop_table('scenario_runs')
