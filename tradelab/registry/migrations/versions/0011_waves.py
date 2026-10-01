"""Waves: market pressure as a variable, not as an event. Plan.md §20.

An undated condition to simulate under, kept apart from dated `events`. One row per
asset (unique); range CHECKs in the schema, not only the API.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'waves',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('asset', sa.Text(), nullable=False),
        sa.Column('direction', sa.Text(), nullable=False),
        sa.Column('magnitude', sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("direction IN ('UP','DOWN')", name='ck_waves_direction'),
        sa.CheckConstraint('magnitude BETWEEN 1 AND 10', name='ck_waves_magnitude'),
        sa.CheckConstraint(
            "asset = upper(asset) AND asset ~ '^[A-Z0-9]{1,20}$'",
            name='ck_waves_asset'),
        sa.UniqueConstraint('asset', name='uq_waves_asset'),
    )


def downgrade() -> None:
    op.drop_table('waves')
