"""Phase 0-1 foundations: venues, sources, universe, ingestion, quality, datasets.

Plan.md §5.1-§5.2, §17 phase 0-1.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "venues",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("supports_testnet", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("clock_offset_ms", sa.Integer),
        sa.Column("notes", sa.Text),
        sa.CheckConstraint("kind IN ('cex','dex')", name="ck_venues_kind"),
    )

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("venue", sa.String(32), sa.ForeignKey("venues.code")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("sla_seconds", sa.Integer, nullable=False),
        sa.Column("credibility", sa.Numeric(5, 4)),
        sa.Column("valid_from", TS, nullable=False),
        sa.Column("valid_to", TS),
        sa.CheckConstraint("tier BETWEEN 1 AND 3", name="ck_sources_tier"),
    )

    op.create_table(
        "asset_universe",
        sa.Column("asset", sa.String(32), primary_key=True),
        sa.Column("venue", sa.String(32), sa.ForeignKey("venues.code"), primary_key=True),
        sa.Column("valid_from", TS, primary_key=True),
        sa.Column("listed_at", TS, nullable=False),
        sa.Column("delisted_at", TS),
        sa.Column("tick_size", sa.Numeric(38, 18), nullable=False),
        sa.Column("lot_size", sa.Numeric(38, 18), nullable=False),
        sa.Column("min_notional", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee_tier", sa.String(16), nullable=False),
        sa.Column("valid_to", TS),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"),
                  nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS),
        sa.Column("watermark", TS),
        sa.Column("rows_written", sa.BigInteger),
        sa.Column("output_uri", sa.Text),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", TS),
        sa.CheckConstraint("mode IN ('backfill','stream','recapture')", name="ck_ingestion_mode"),
    )
    op.create_index("ix_ingestion_runs_source", "ingestion_runs", ["source_id", "started_at"])

    op.create_table(
        "data_quality_issues",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"),
                  nullable=False),
        sa.Column("asset", sa.String(32)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("detail", postgresql.JSONB, nullable=False),
        sa.Column("resolved_at", TS),
        sa.CheckConstraint("severity IN ('info','warn','blocking')", name="ck_dqi_severity"),
        sa.CheckConstraint(
            "kind IN ('gap','duplicate','out_of_range','cross_inconsistent','backfill_mismatch')",
            name="ck_dqi_kind",
        ),
        sa.CheckConstraint("period_end >= period_start", name="ck_dqi_period"),
    )
    # Blocking issues gate promotion (§4.6), so they are queried constantly.
    op.create_index(
        "ix_dqi_open_blocking", "data_quality_issues", ["source_id", "period_start"],
        postgresql_where=sa.text("resolved_at IS NULL AND severity = 'blocking'"),
    )

    op.create_table(
        "dataset_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False),
        sa.Column("manifest_uri", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("row_count", sa.BigInteger),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_hash", name="uq_dataset_content_hash"),
    )


def downgrade() -> None:
    for table in (
        "dataset_versions", "data_quality_issues", "ingestion_runs",
        "asset_universe", "sources", "venues",
    ):
        op.drop_table(table)
