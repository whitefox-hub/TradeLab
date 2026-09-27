"""Every position says who guided it. research/PREREG-009-trend-book.md.

The trend book holds two guides in one book: the operator's reading when the
trend is not 5, and the algorithm's rule when it is. The operator asked for a
field saying which one sized each position, so the two can be judged apart on
the same screen, and P&L by guide is how PREREG-009 is decided.

`trend` and `trend_source` record the setting in force when the position was
sized, so a position can be traced to the reading that caused it after the
slider has moved on. All three are nullable: a position from a book with no
notion of a guide, such as Flash Strike's registered book, has none, and an
empty field says so better than an invented one.
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
