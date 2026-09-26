"""What the algorithm did under a set of conditions. Plan.md §20, §15.

Deliberately **not** the `runs` table. That one carries a strategy, a risk
limit config, a cost model and a mode that includes `live`, and it exists for
something that trades. This records a simulation of the decision sequence under
operator-set conditions, which is research — and the two must not be one table,
because the query that mistakes them is the one that reports a scenario as a
position.

Every row carries the step the sequence reached. Right now that is the gate,
because nothing has cleared §15, and a table that could not express "it stopped
here" would have to report a stop as a result.
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
        # The conditions as they were at the moment of the run, copied rather
        # than joined: market_conditions holds one mutable row, so a join would
        # report every past run as having used today's settings.
        sa.Column('wave_direction', sa.Text(), nullable=False),
        sa.Column('wave_magnitude', sa.SmallInteger(), nullable=False),
        sa.Column('sentiment', sa.SmallInteger(), nullable=False),
        sa.Column('sentiment_bucket', sa.Text(), nullable=False),
        # What the wave did, stored so a result can be read without re-deriving
        # the transform that produced it.
        sa.Column('long_multiplier', sa.Numeric(6, 4), nullable=False),
        sa.Column('short_multiplier', sa.Numeric(6, 4), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('bars', sa.Integer(), nullable=False),
        # Both arms. The conditioned result alone would be a number with nothing
        # to read it against, and the difference is the only thing the operator
        # actually changed.
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
        # A scenario run is a simulation. There is no column for a mode, so
        # there is nothing here for a later change to set to 'live'.
        sa.CheckConstraint('bars > 0', name='ck_scenario_runs_bars'),
    )
    op.create_index('ix_scenario_runs_ran_at', 'scenario_runs', ['ran_at'])


def downgrade() -> None:
    op.drop_index('ix_scenario_runs_ran_at', table_name='scenario_runs')
    op.drop_table('scenario_runs')
