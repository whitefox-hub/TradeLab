"""Who set the trend. Plan.md §20, PREREG-005.

Adds `source` (manual/derived/adopted): PREREG-005 measures the operator only.
Default `manual` is correct for all existing rows.
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
