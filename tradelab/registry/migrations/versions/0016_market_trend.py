"""The wave is called the market trend. Plan.md §20.

A rename, and only a rename. "Wave" was a word this project invented; "market
trend" is the one the operator uses, and one thing with two names is one thing
nobody can grep for.

The columns go with it rather than staying behind. A schema that says
`wave_direction` under a screen that says Market Trend is the drift this
codebase keeps finding the hard way — the name nobody reads is the one that
stops matching, and the next person looks for a concept that is not there.

Migrations 0011 to 0015 keep the old word, because they record what happened and
did not happen under this name.
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
    # The CHECK bodies still name the old columns; Postgres rewrites those on a
    # column rename, so only the constraint *names* need touching. Asserted by
    # the schema-drift comparison rather than assumed here.


def downgrade() -> None:
    for table, old, new in CONSTRAINTS:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT {new} TO {old}')
    for table, old, new in COLUMNS:
        op.alter_column(table, new, new_column_name=old)
