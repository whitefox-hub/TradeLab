"""Macro calendar. Plan.md §4.7, §20.6.

The CHECK on consensus_at forbids a consensus recorded after the release (look-ahead).

Revision ID: ac787504fd10
Revises: 0005
Create Date: 2026-09-25 17:20:52.707845+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('macro_series',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('has_vintages', sa.Boolean(), nullable=False),
    sa.Column('is_revised', sa.Boolean(), nullable=False),
    sa.Column('session_close', sa.Text(), nullable=True),
    sa.Column('publish_lag_s', sa.Integer(), server_default='0', nullable=False),
    sa.CheckConstraint("kind IN ('rate','yield','price','index','activity')", name='ck_macro_kind'),
    sa.CheckConstraint(
        "source IN ('fred','alfred','stooq','yahoo','manual')",
        name='ck_macro_source'),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('macro_releases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('series', sa.String(length=32), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consensus', sa.Numeric(precision=20, scale=8), nullable=True),
    sa.Column('consensus_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('actual', sa.Numeric(precision=20, scale=8), nullable=True),
    sa.Column('actual_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('surprise_sigma', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('vintage_of', sa.UUID(), nullable=True),
    sa.CheckConstraint(
        'consensus IS NULL OR consensus_at IS NULL OR consensus_at < scheduled_for',
        name='ck_consensus_precedes_the_release'),
    sa.ForeignKeyConstraint(['series'], ['macro_series.code'], ),
    sa.ForeignKeyConstraint(['vintage_of'], ['macro_releases.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_macro_releases_series_time', 'macro_releases', ['series', 'scheduled_for'],
                    unique=False)


def downgrade() -> None:
    op.drop_index('ix_macro_releases_series_time', table_name='macro_releases')
    op.drop_table('macro_releases')
    op.drop_table('macro_series')
