"""Operator settings, appended never updated. Plan.md §12.4, §20.4.

Holds the research universe and history window, never a strategy's own universe (§12.4).

Revision ID: e144a2d9df88
Revises: 0006
Create Date: 2026-09-25 18:17:48.179738+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(length=48), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('changed_by', sa.UUID(), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True),
              server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_app_settings_key_time', 'app_settings', ['key', 'valid_from'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_app_settings_key_time', table_name='app_settings')
    op.drop_table('app_settings')
