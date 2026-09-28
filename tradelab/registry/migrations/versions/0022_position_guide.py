"""Every position says who guided it. research/PREREG-009-trend-book.md.

`guide` is operator (trend != 5) or algorithm (trend 5); PREREG-009 is decided by P&L
per guide. `trend`/`trend_source` snapshot the setting. All nullable for guide-less books.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('positions', sa.Column('guide', sa.String(16), nullable=True))
    op.add_column('positions', sa.Column('trend', sa.SmallInteger(), nullable=True))
    op.add_column('positions', sa.Column('trend_source', sa.Text(), nullable=True))
    op.create_check_constraint('ck_positions_guide', 'positions',
                               "guide IS NULL OR guide IN ('operator', 'algorithm')")
    op.create_check_constraint('ck_positions_trend', 'positions',
                               'trend IS NULL OR trend BETWEEN 1 AND 10')
    op.create_check_constraint(
        'ck_positions_trend_source', 'positions',
        "trend_source IS NULL OR trend_source IN ('manual', 'adopted', 'derived')")


def downgrade() -> None:
    for name in ('ck_positions_trend_source', 'ck_positions_trend', 'ck_positions_guide'):
        op.drop_constraint(name, 'positions')
    for column in ('trend_source', 'trend', 'guide'):
        op.drop_column('positions', column)
