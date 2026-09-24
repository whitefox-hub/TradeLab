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
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

import structlog

from tradelab.collectors import watermark
from tradelab.collectors.recovery import GapPlan, plan_recovery
from tradelab.registry.db import session as db_session
from tradelab.registry.models import DataQualityIssue, IngestionRun

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


async def _supervise_one(collector, kind: str, source_name: str, venue: str,
                         stop: asyncio.Event) -> None:
    """Reconcile, run until signalled, flush, then advance the watermark."""
    reconcile_on_start(kind, source_name, venue)

    with db_session() as session:
        source = watermark.source_id(session, source_name, kind, venue)
        run = watermark.start(session, source, "stream", datetime.now(UTC))
        run_id = run.id

    buffer = getattr(collector, "buffer", None)
    if buffer is not None:
        def record(rows: int, upto: datetime, _run_id=run_id) -> None:
            with db_session() as session:
                watermark.advance(session, session.get(IngestionRun, _run_id), upto, rows)

        buffer.on_flush = record

    try:
        await collector.run(stop)
    finally:
        # collector.run flushes on exit. Only after that may the watermark move:
        # write first, advance second. Reversing it means a crash between the two
        # leaves a watermark claiming data that was never written.
        with db_session() as session:
            run = session.get(IngestionRun, run_id)
            watermark.finish(session, run, "stopped" if stop.is_set() else "completed")
        log.info("collector_stopped", kind=kind,
                 rows=getattr(collector.buffer, "flushed_rows", 0), clean=stop.is_set())


def assert_writable(root: Path) -> None:
    """Refuse to start if the archive cannot be written.

    Without this the process logs `capture_starting`, connects, and looks healthy
    for a full flush interval before the first write raises — by which point it
    has silently discarded everything it received. For a source that cannot be
    backfilled, that window is permanent data loss dressed up as a running
    service.
    """
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".write_probe"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise PermissionError(
            f"Archive {root} is not writable by uid {os.getuid()}: {exc}. "
            "Nothing would reach disk until the first flush, which would then "
            "crash the collector. Refusing to start."
        ) from exc


async def run_capture(collectors: list[tuple[object, str, str]], venue: str,
                      root: Path | None = None) -> None:
    """Run several capture collectors concurrently under one shutdown signal.

    `collectors` is a list of (collector, kind, source_name).

    One signal stops all of them, and the function returns only once every buffer
    has been flushed — which is what `stop_grace_period` in compose must cover.
    """
    if root is not None:
        assert_writable(root)

    stop = asyncio.Event()
    install_shutdown_handler(stop)
    log.info("capture_starting", venue=venue, collectors=[k for _, k, _ in collectors])

    # return_exceptions keeps one failing collector from silently taking the
    # others down with it; the failure is logged and re-raised afterwards.
    results = await asyncio.gather(
        *(_supervise_one(c, kind, name, venue, stop) for c, kind, name in collectors),
        return_exceptions=True,
    )
    failures = [
        (collectors[i][1], r)
        for i, r in enumerate(results)
        if isinstance(r, BaseException)
    ]
    for kind, error in failures:
        log.error("collector_failed", kind=kind, error=str(error))
    if failures:
        raise failures[0][1]
    log.info("capture_stopped_cleanly")
