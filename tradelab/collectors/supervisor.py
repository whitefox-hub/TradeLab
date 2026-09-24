"""Collector startup and shutdown. Plan.md §4.2, §14.2.

Two symmetric rules, borrowed from the execution side because the reasoning is
identical:

**On start, reconcile before collecting.** §14.2 forbids the executor from
sending an order before it has reconciled its position with the venue. Ingestion
has the same hazard in a quieter form: a collector that resumes without checking
what it already has produces an archive with holes nobody can see.

**On stop, flush before exiting.** Buffered rows that never reach disk are, for
liquidations, gone forever. The flush budget is therefore an explicit choice
about how much unrecoverable data an unclean kill may cost.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime
from pathlib import Path

import structlog

from tradelab.collectors import watermark
from tradelab.collectors.recovery import GapPlan, plan_recovery
from tradelab.registry.db import session as db_session
from tradelab.registry.models import DataQualityIssue

log = structlog.get_logger(__name__)


def reconcile_on_start(kind: str, source_name: str, venue: str,
                       asset: str | None = None) -> GapPlan | None:
    """Measure the downtime gap, record what was lost, return what to backfill.

    Called before a collector resumes. Returns None when there is nothing to do.
    """
    with db_session() as session:
        source = watermark.source_id(session, source_name, kind, venue)
        last = watermark.read(session, source)
        plan = plan_recovery(kind, last, asset=asset)

        if plan is None:
            log.info("resume_clean", kind=kind, source=source_name,
                     watermark=str(last) if last else "never run")
            return None

        log.warning(
            "downtime_detected", kind=kind, source=source_name,
            gap_seconds=plan.duration.total_seconds(),
            recoverability=str(plan.recoverability),
        )

        if plan.issue is not None:
            # Recorded, never silently corrected (§4.2). This is what makes the
            # hole explainable months later instead of merely mysterious.
            session.add(DataQualityIssue(
                source_id=source,
                asset=plan.issue.asset,
                kind=str(plan.issue.kind),
                period_start=plan.issue.period_start,
                period_end=plan.issue.period_end,
                severity=str(plan.issue.severity),
                detail=plan.issue.detail,
            ))
            session.commit()

        return plan


def install_shutdown_handler(stop: asyncio.Event) -> None:
    """SIGTERM/SIGINT set the stop event so the run loop flushes and exits.

    Docker sends SIGTERM and then SIGKILL after the grace period, so the flush
    has to fit inside `stop_grace_period`. Killing without a flush loses the
    buffer, which for liquidations means losing it permanently.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            signal.signal(sig, lambda *_: stop.set())


async def supervise(collector, kind: str, source_name: str, venue: str,
                    root: Path, assets: list[str] | None = None) -> None:
    """Reconcile, then run until signalled, then flush."""
    reconcile_on_start(kind, source_name, venue)

    stop = asyncio.Event()
    install_shutdown_handler(stop)

    with db_session() as session:
        source = watermark.source_id(session, source_name, kind, venue)
        run = watermark.start(session, source, "stream", datetime.now(UTC))

    try:
        await collector.run(stop)
    finally:
        # The buffer is flushed by collector.run on exit; only after that is the
        # watermark allowed to move. Write first, advance second (§watermark).
        with db_session() as session:
            merged = session.merge(run)
            watermark.advance(session, merged, datetime.now(UTC),
                              rows=getattr(collector.buffer, "flushed_rows", 0))
            watermark.finish(session, merged, "stopped" if stop.is_set() else "completed")
        log.info("collector_stopped", kind=kind, clean=stop.is_set())
