"""Durable resume points (Plan.md §4.2, §5.2).

Write the data, then advance the watermark, never the reverse: advancing first lets a crash
hide a hole; advancing last only causes a harmless deduplicated re-read."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradelab.registry.models import IngestionRun, Source


def source_id(session: Session, name: str, kind: str, venue: str,
              sla_seconds: int = 300, tier: int = 1) -> uuid.UUID:
    """Fetch or create the source row. Idempotent on (name, venue)."""
    existing = session.execute(
        select(Source).where(Source.name == name, Source.venue == venue)
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    source = Source(
        id=uuid.uuid4(), name=name, venue=venue, kind=kind, tier=tier,
        sla_seconds=sla_seconds, valid_from=datetime.now(UTC),
    )
    session.add(source)
    session.commit()
    return source.id


def read(session: Session, source: uuid.UUID) -> datetime | None:
    """Highest durable watermark for a source, or None if it has never run."""
    return session.execute(
        select(IngestionRun.watermark)
        .where(IngestionRun.source_id == source, IngestionRun.watermark.is_not(None))
        .order_by(IngestionRun.watermark.desc())
        .limit(1)
    ).scalar_one_or_none()


def start(session: Session, source: uuid.UUID, mode: str, period_start: datetime) -> IngestionRun:
    run = IngestionRun(
        source_id=source, mode=mode, period_start=period_start,
        status="running", started_at=datetime.now(UTC),
    )
    session.add(run)
    session.commit()
    return run


def advance(session: Session, run: IngestionRun, upto: datetime, rows: int,
            output_uri: str | None = None) -> None:
    """Record progress. Call ONLY after the data is durably on disk; `rows` accumulates."""
    run.watermark = upto
    run.rows_written = (run.rows_written or 0) + rows
    if output_uri:
        run.output_uri = output_uri
    session.commit()


def finish(session: Session, run: IngestionRun, status: str = "completed") -> None:
    run.status = status
    run.ended_at = datetime.now(UTC)
    session.commit()
