"""Who set the trend. Plan.md §20, PREREG-005.

The trend is about to become settable by something other than a person, and a
log that cannot say which is one PREREG-005 cannot read: that study measures
whether *the operator's* reading of the news predicts anything, and a hit
attributed to the operator that was actually computed from a trade count would
confirm the wrong hypothesis.

Added before anything can write to it, which is the only time this is free.
Defaulting to `manual` is correct for every row that exists, because until now
nothing else could.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None

SOURCES = "('manual','derived','adopted')"


def upgrade() -> None:
    for table in ('market_conditions', 'scenario_runs'):
        op.add_column(table, sa.Column(
            'source', sa.Text(), nullable=False, server_default='manual'))
        op.create_check_constraint(
            f'ck_{table}_source', table, f'source IN {SOURCES}')


def downgrade() -> None:
    for table in ('market_conditions', 'scenario_runs'):
        op.drop_constraint(f'ck_{table}_source', table)
        op.drop_column(table, 'source')
