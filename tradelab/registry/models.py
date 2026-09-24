"""Registry ORM. Plan.md §5.

Phase 0-1 subset: the tables needed to ingest data reproducibly. Later phases add
their own tables per §17; nothing here is speculative.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


TS = DateTime(timezone=True)  # every timestamp is timestamptz in UTC (§5)


class Venue(Base):
    __tablename__ = "venues"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # cex | dex
    supports_testnet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    clock_offset_ms: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("kind IN ('cex','dex')", name="ck_venues_kind"),)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    venue: Mapped[str | None] = mapped_column(ForeignKey("venues.code"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # §4.1
    sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # Point-in-time: a 2021 article cannot be scored with today's reputation (§11.2)
    credibility: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    valid_from: Mapped[datetime] = mapped_column(TS, nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (CheckConstraint("tier BETWEEN 1 AND 3", name="ck_sources_tier"),)


class AssetUniverse(Base):
    """Point-in-time universe. Blocking requirement: kills survivorship bias (§4.4)."""

    __tablename__ = "asset_universe"

    asset: Mapped[str] = mapped_column(String(32), primary_key=True)
    venue: Mapped[str] = mapped_column(ForeignKey("venues.code"), primary_key=True)
    valid_from: Mapped[datetime] = mapped_column(TS, primary_key=True)
    listed_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    delisted_at: Mapped[datetime | None] = mapped_column(TS)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    lot_size: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    min_notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fee_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(TS)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # backfill|stream|recapture
    period_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_end: Mapped[datetime | None] = mapped_column(TS)
    watermark: Mapped[datetime | None] = mapped_column(TS)
    rows_written: Mapped[int | None] = mapped_column(BigInteger)
    output_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("mode IN ('backfill','stream','recapture')", name="ck_ingestion_mode"),
        Index("ix_ingestion_runs_source", "source_id", "started_at"),
    )


class DataQualityIssue(Base):
    """Anomalies are recorded, never silently corrected (§4.2, §4.6)."""

    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    asset: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_end: Mapped[datetime] = mapped_column(TS, nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("severity IN ('info','warn','blocking')", name="ck_dqi_severity"),
        CheckConstraint(
            "kind IN ('gap','duplicate','out_of_range','cross_inconsistent','backfill_mismatch')",
            name="ck_dqi_kind",
        ),
        CheckConstraint("period_end >= period_start", name="ck_dqi_period"),
        # Blocking issues gate promotion (§4.6), so they are queried constantly.
        Index(
            "ix_dqi_open_blocking", "source_id", "period_start",
            postgresql_where=text("resolved_at IS NULL AND severity = 'blocking'"),
        ),
    )


class DatasetVersion(Base):
    """Exactly which data existed. `content_hash` is the dataset's identity (§5.1)."""

    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manifest_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    period_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_end: Mapped[datetime] = mapped_column(TS, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("content_hash", name="uq_dataset_content_hash"),)
