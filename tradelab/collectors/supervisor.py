"""Collector startup and shutdown (Plan.md §4.2, §14.2): reconcile the gap before collecting;
flush before exiting (unflushed liquidations are lost forever)."""

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
    """Measure the downtime gap, record what was lost, return what to backfill (or None)."""
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
    """SIGTERM/SIGINT set the stop event; the flush must fit in compose `stop_grace_period`."""
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
        with db_session() as session:
            run = session.get(IngestionRun, run_id)
            watermark.finish(session, run, "stopped" if stop.is_set() else "completed")
        log.info("collector_stopped", kind=kind,
                 rows=getattr(collector.buffer, "flushed_rows", 0), clean=stop.is_set())


def assert_writable(root: Path) -> None:
    """Refuse to start if the archive is unwritable; otherwise the first flush fails after
    unrecoverable data was already received."""
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
    """Run (collector, kind, source_name) captures under one shutdown signal; returns after
    every buffer has flushed."""
    if root is not None:
        assert_writable(root)

    stop = asyncio.Event()
    install_shutdown_handler(stop)
    log.info("capture_starting", venue=venue, collectors=[k for _, k, _ in collectors])

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
