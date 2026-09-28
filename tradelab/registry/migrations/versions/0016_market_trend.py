"""The wave is called the market trend. Plan.md §20.

Pure rename of wave columns and constraints to trend. Migrations 0011-0015 keep "wave".
"""

from __future__ import annotations

from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

#: (table, old column, new column)
COLUMNS = (
    ('market_conditions', 'wave_direction', 'trend_direction'),
    ('market_conditions', 'wave_magnitude', 'trend_magnitude'),
    ('scenario_runs', 'wave_direction', 'trend_direction'),
    ('scenario_runs', 'wave_magnitude', 'trend_magnitude'),
)

#: (table, old constraint, new constraint)
CONSTRAINTS = (
    ('market_conditions', 'ck_market_conditions_wave_direction',
     'ck_market_conditions_trend_direction'),
    ('market_conditions', 'ck_market_conditions_wave_magnitude',
     'ck_market_conditions_trend_magnitude'),
    ('scenario_runs', 'ck_scenario_runs_direction',
     'ck_scenario_runs_trend_direction'),
    ('scenario_runs', 'ck_scenario_runs_magnitude',
     'ck_scenario_runs_trend_magnitude'),
)


def upgrade() -> None:
    for table, old, new in COLUMNS:
        op.alter_column(table, old, new_column_name=new)
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}')
    # Postgres rewrites CHECK bodies on column rename; only names need touching.


def downgrade() -> None:
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}')
    for table, old, new in COLUMNS:
        op.alter_column(table, new, new_column_name=old)
