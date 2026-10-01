"""Downtime recovery classification. Plan.md §4.1, §4.2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradelab.collectors.base import Severity
from tradelab.collectors.recovery import Recoverability, plan_recovery

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@pytest.mark.fast
def test_short_restart_is_not_an_outage() -> None:
    """A normal restart must not file an incident."""
    assert plan_recovery("ohlcv", NOW - timedelta(seconds=30), NOW) is None


@pytest.mark.fast
def test_first_ever_run_has_nothing_to_recover() -> None:
    assert plan_recovery("ohlcv", None, NOW) is None


@pytest.mark.fast
def test_klines_gap_is_fully_recoverable() -> None:
    plan = plan_recovery("ohlcv", NOW - timedelta(days=3), NOW)
    assert plan is not None
    assert plan.recoverability is Recoverability.FULL
    assert plan.needs_backfill
    assert plan.permanent_loss is None
    assert plan.issue is None


@pytest.mark.fast
def test_open_interest_recovers_only_inside_its_window() -> None:
    """REST history reaches ~30 days; anything older is gone regardless."""
    plan = plan_recovery("open_interest", NOW - timedelta(days=45), NOW)
    assert plan is not None
    assert plan.recoverability is Recoverability.WINDOW
    assert plan.needs_backfill
    assert plan.permanent_loss is not None
    lost_start, lost_end = plan.permanent_loss
    assert (lost_end - lost_start) == timedelta(days=15)


@pytest.mark.fast
def test_open_interest_gap_inside_the_window_loses_nothing() -> None:
    plan = plan_recovery("open_interest", NOW - timedelta(days=5), NOW)
    assert plan is not None
    assert plan.permanent_loss is None
    assert plan.issue is None


@pytest.mark.fast
def test_liquidation_downtime_is_permanent_and_blocking() -> None:
    """The case that matters most.

    There is no historical endpoint for liquidations, so an outage is data that
    will never exist. The issue is BLOCKING because §4.6 uses that to bar
    promotion of anything validated on the window — otherwise a strategy gets
    validated on data the system knows it never collected.
    """
    plan = plan_recovery("liquidations", NOW - timedelta(hours=6), NOW, asset="BTCUSDT")
    assert plan is not None
    assert plan.recoverability is Recoverability.NONE
    assert not plan.needs_backfill
    assert plan.permanent_loss == (NOW - timedelta(hours=6), NOW)
    assert plan.issue is not None
    assert plan.issue.severity is Severity.BLOCKING
    assert plan.issue.detail["cause"] == "collector_downtime"
    assert plan.issue.detail["unrecoverable_seconds"] == 6 * 3600


@pytest.mark.fast
def test_unknown_kinds_default_to_unrecoverable() -> None:
    """Assume the worst for a source nobody classified: a false blocking issue is
    cheap, a silently missing window is not."""
    plan = plan_recovery("something_new", NOW - timedelta(hours=1), NOW)
    assert plan is not None
    assert plan.recoverability is Recoverability.NONE
    assert plan.issue is not None
