"""Waves: market pressure as a variable, not as an event. Plan.md §20.

A wave says only that an asset is under pressure, which way, and how hard. It
carries no date, no duration and no source, because it is not a record of
something that happened — it is a *condition to test under*. `events` is the
other thing: individual, dated, point-in-time, and the corpus a study reads. The
two are kept apart so that neither can be mistaken for the other.

One row per asset, enforced. Two waves on BTC pointing opposite ways is not a
richer description, it is a contradiction, and with no dates there is nothing to
order them by and no way for a simulation to pick. The unique constraint makes a
second one an edit rather than a silent ambiguity.

The checks are here and not only in the API. A magnitude of 70 reaching the
engine would be read as a pressure seven times the defined extreme, which is the
class of input the operator screen was asked to refuse.
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
        # Stored upper-case so BTC and btc cannot become two waves on one asset,
        # which the unique constraint alone would allow.
        sa.CheckConstraint(
            "asset = upper(asset) AND asset ~ '^[A-Z0-9]{1,20}$'",
            name='ck_waves_asset'),
        sa.UniqueConstraint('asset', name='uq_waves_asset'),
    )


def downgrade() -> None:
    op.drop_table('waves')
