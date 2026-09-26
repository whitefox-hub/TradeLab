"""Exactly one risk configuration is in force. Plan.md §14.1.

The one in force is the newest by `valid_from`, so "only one is active" holds by
construction rather than by a flag somebody has to keep correct. A flag can be
set on two rows; a maximum cannot be reached twice.

What could go wrong is a tie. Two configurations sharing a timestamp make
"newest" ambiguous, and the engine would take whichever the planner returned —
the same limits screen showing ACTIVE against a row that is not the one a run
would pick up. Unique on `valid_from` removes the case rather than ordering it
by a second column nobody would think to look at.
"""

from __future__ import annotations

from alembic import op

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        'uq_risk_limit_configs_valid_from', 'risk_limit_configs', ['valid_from'])


def downgrade() -> None:
    op.drop_constraint('uq_risk_limit_configs_valid_from', 'risk_limit_configs')
