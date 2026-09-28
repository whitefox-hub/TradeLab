"""The market trend is one scale, 1 to 10, with 5 meaning nothing. Plan.md §20.

Replaces direction + magnitude with one `trend` column; direction is the side of 5.
Existing rows are mapped proportionally, not reset.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

TABLES = ("market_conditions", "scenario_runs")

#: NORMAL -> 5. UP m -> 5 + ceil(m/2), so 10 reaches 10. DOWN m -> 5 - ceil(2m/5),
#: so 10 reaches 1. Both clamped to the scale.
MAP = """
    CASE trend_direction
      WHEN 'UP'   THEN least(10, 5 + CAST(ceil(trend_magnitude / 2.0) AS smallint))
      WHEN 'DOWN' THEN greatest(1, 5 - CAST(ceil(trend_magnitude * 2.0 / 5) AS smallint))
      ELSE 5
    END
"""


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('trend', sa.SmallInteger(), nullable=True))
        op.execute(f"UPDATE {table} SET trend = {MAP}")
        op.alter_column(table, 'trend', nullable=False,
                        server_default=sa.text('5'))
        op.create_check_constraint(
            f'ck_{table}_trend', table, 'trend BETWEEN 1 AND 10')

        for constraint in (f'ck_{table}_trend_direction',
                           f'ck_{table}_trend_magnitude',
                           f'ck_{table}_normal_is_zero'):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.drop_column(table, 'trend_direction')
        op.drop_column(table, 'trend_magnitude')


def downgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('trend_direction', sa.Text(), nullable=True))
        op.add_column(table, sa.Column('trend_magnitude', sa.SmallInteger(),
                                       nullable=True))
        op.execute(f"""
            UPDATE {table} SET
              trend_direction = CASE WHEN trend > 5 THEN 'UP'
                                     WHEN trend < 5 THEN 'DOWN' ELSE 'NORMAL' END,
              trend_magnitude = CASE WHEN trend > 5 THEN (trend - 5) * 2
                                     WHEN trend < 5 THEN CAST(
                                       round((5 - trend) * 5.0 / 2) AS smallint)
                                     ELSE 0 END
        """)
        op.alter_column(table, 'trend_direction', nullable=False)
        op.alter_column(table, 'trend_magnitude', nullable=False)
        op.drop_constraint(f'ck_{table}_trend', table)
        op.drop_column(table, 'trend')
