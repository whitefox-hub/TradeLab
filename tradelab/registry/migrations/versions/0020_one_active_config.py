"""Exactly one risk configuration is in force. Plan.md §14.1.

The one in force is the newest by `valid_from`; unique `valid_from` rules out a tie.
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
