"""Startup reconciliation and clean shutdown. Plan.md §4.2, §14.2."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tradelab.collectors import watermark
from tradelab.collectors.supervisor import install_shutdown_handler, reconcile_on_start
from tradelab.registry.db import session as db_session
from tradelab.registry.models import DataQualityIssue, Venue


@pytest.mark.fast
def test_sigterm_sets_the_stop_event() -> None:
    """A signal must stop the loop so it can flush, not kill it mid-buffer."""
    import signal

    async def scenario() -> bool:
        stop = asyncio.Event()
        install_shutdown_handler(stop)
        signal.raise_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            return False
        return True

    assert asyncio.run(scenario())


@pytest.mark.integration
def test_downtime_on_a_non_backfillable_source_files_a_blocking_issue() -> None:
    """The end-to-end behaviour that matters after an outage.

    A six-hour liquidation-capture outage must leave a BLOCKING row behind, since
    that window's data will never exist and §4.6 uses the row to bar promotion of
    anything validated on it.
    """
    with db_session() as session:
        if session.get(Venue, "binance") is None:
            session.add(Venue(code="binance", name="Binance", kind="cex",
                              supports_testnet=True))
            session.commit()

        source = watermark.source_id(session, "test-liq-outage", "liquidations", "binance")
        run = watermark.start(session, source, "stream", datetime.now(UTC) - timedelta(hours=7))
        watermark.advance(session, run, datetime.now(UTC) - timedelta(hours=6), rows=10)
        before = session.query(DataQualityIssue).filter_by(source_id=source).count()

    plan = reconcile_on_start("liquidations", "test-liq-outage", "binance", asset="BTCUSDT")
    assert plan is not None
    assert not plan.needs_backfill
    assert plan.permanent_loss is not None

    with db_session() as session:
        issues = session.query(DataQualityIssue).filter_by(source_id=source).all()
        assert len(issues) == before + 1
        assert issues[-1].severity == "blocking"
        assert issues[-1].detail["cause"] == "collector_downtime"


@pytest.mark.integration
def test_watermark_survives_a_restart() -> None:
    with db_session() as session:
        if session.get(Venue, "binance") is None:
            session.add(Venue(code="binance", name="Binance", kind="cex",
                              supports_testnet=True))
            session.commit()
        source = watermark.source_id(session, "test-resume", "ohlcv", "binance")
        mark = datetime.now(UTC) - timedelta(minutes=10)
        run = watermark.start(session, source, "backfill", mark - timedelta(hours=1))
        watermark.advance(session, run, mark, rows=1440)
        watermark.finish(session, run)

    with db_session() as session:
        source = watermark.source_id(session, "test-resume", "ohlcv", "binance")
        assert watermark.read(session, source) is not None


@pytest.mark.fast
def test_refuses_to_start_when_the_archive_is_not_writable(tmp_path) -> None:
    """Fail at startup, not at the first flush.

    Without this the process logs `capture_starting`, connects, and looks healthy
    for a whole flush interval before the first write raises — having silently
    discarded everything it received in the meantime. For a source that cannot be
    backfilled, that is permanent loss wearing the costume of a running service.
    """
    import os

    from tradelab.collectors.supervisor import assert_writable

    archive = tmp_path / "archive"
    archive.mkdir()
    assert_writable(archive)

    if os.getuid() == 0:
        pytest.skip("running as root; permissions cannot be exercised")
    archive.chmod(0o500)
    try:
        with pytest.raises(PermissionError, match="not writable"):
            assert_writable(archive)
    finally:
        archive.chmod(0o700)
