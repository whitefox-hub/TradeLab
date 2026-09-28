"""Operator interface tables. Plan.md §20.

The 24-hour cooling-off on gate changes is a trigger: a CHECK cannot subquery another table.

Revision ID: 55f9fe8ce067
Revises: 0003
Create Date: 2026-09-25 16:46:01.157906+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('subject_kind', sa.Text(), nullable=False),
    sa.Column('subject_id', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True),
              server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_time', 'audit_log', ['occurred_at'], unique=False)
    op.create_table('approvals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('subject_kind', sa.String(length=24), nullable=False),
    sa.Column('subject_id', sa.UUID(), nullable=False),
    sa.Column('approved_by', sa.UUID(), nullable=False),
    sa.Column('decision', sa.String(length=8), nullable=False),
    sa.Column('gate_config_id', sa.UUID(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True),
              server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("decision IN ('approve','reject')", name='ck_approvals_decision'),
    sa.CheckConstraint(
        "subject_kind = 'gate_change' OR gate_config_id IS NOT NULL",
        name='ck_approvals_cites_a_gate'),
    sa.CheckConstraint(
        "subject_kind IN ('strategy','capital_tier','gate_change')",
        name='ck_approvals_subject'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['gate_config_id'], ['gate_configs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_approvals_subject', 'approvals',
                    ['subject_kind', 'subject_id'], unique=False)
    # §20.3 cooling-off: an approval may not cite a gate config younger than 24h.
    # In the DB, not the API, so it cannot be bypassed.
    op.execute("""
        CREATE FUNCTION approvals_respect_cooling_off() RETURNS trigger AS $$
        DECLARE gate_age interval;
        BEGIN
            IF NEW.gate_config_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT now() - valid_from INTO gate_age
              FROM gate_configs WHERE id = NEW.gate_config_id;
            IF gate_age < interval '24 hours' THEN
                RAISE EXCEPTION
                    'approval cites a gate config changed % ago; §20.3 requires 24 hours',
                    gate_age;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_approvals_cooling_off
        BEFORE INSERT ON approvals
        FOR EACH ROW EXECUTE FUNCTION approvals_respect_cooling_off();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_approvals_cooling_off ON approvals")
    op.execute("DROP FUNCTION IF EXISTS approvals_respect_cooling_off()")
    op.drop_index('ix_approvals_subject', table_name='approvals')
    op.drop_table('approvals')
    op.drop_index('ix_audit_time', table_name='audit_log')
    op.drop_table('audit_log')
