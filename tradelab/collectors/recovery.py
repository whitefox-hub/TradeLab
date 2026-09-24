"""Downtime recovery. Plan.md §4.1, §4.2, §14.2.

What happens after the app stops is not one question but three, because the
sources differ in whether their history can be recovered at all:

    klines / trades / funding   fully recoverable   bulk archive + REST
    open interest               ~30 days            REST history window
    liquidations / book / news  NEVER               no historical endpoint exists

So recovery is: read the watermark, measure the gap, and then do the one thing
that source permits. A gap that cannot be closed is not closed — it is recorded
as a blocking data quality issue, which under §4.6 bars anything validated on
that window from promotion. The archive stays honest about what it never saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import structlog

from tradelab.collectors.base import Issue, IssueKind, Severity

log = structlog.get_logger(__name__)


class Recoverability(StrEnum):
    FULL = "full"       # bulk archive or unlimited REST history
    WINDOW = "window"   # REST history, bounded lookback
    NONE = "none"       # no historical endpoint: downtime is permanent data loss


#: How far back each kind can be recovered. `None` means unbounded.
RECOVERY: dict[str, tuple[Recoverability, timedelta | None]] = {
    "ohlcv": (Recoverability.FULL, None),
    "trades": (Recoverability.FULL, None),
    "funding": (Recoverability.FULL, None),
    "open_interest": (Recoverability.WINDOW, timedelta(days=30)),
    "liquidations": (Recoverability.NONE, None),
    "book_features": (Recoverability.NONE, None),
    "news": (Recoverability.NONE, None),
}


@dataclass(frozen=True, slots=True)
class GapPlan:
    """What a restart should do about the time since the last durable write."""

    kind: str
    asset: str | None
    gap_start: datetime
    gap_end: datetime
    recoverability: Recoverability
    recoverable_from: datetime | None   # backfill this range before resuming
    permanent_loss: tuple[datetime, datetime] | None
    issue: Issue | None

    @property
    def duration(self) -> timedelta:
        return self.gap_end - self.gap_start

    @property
    def needs_backfill(self) -> bool:
        return self.recoverable_from is not None


def plan_recovery(
    kind: str,
    watermark: datetime | None,
    now: datetime | None = None,
    asset: str | None = None,
    tolerance: timedelta = timedelta(minutes=2),
) -> GapPlan | None:
    """Classify the downtime gap and say what can be done about it.

    Returns None when there is nothing to recover: either no prior run, or a gap
    inside the tolerance (a normal restart, not an outage).
    """
    now = now or datetime.now(UTC)
    if watermark is None:
        return None
    gap = now - watermark
    if gap <= tolerance:
        return None

    recoverability, lookback = RECOVERY.get(kind, (Recoverability.NONE, None))

    if recoverability is Recoverability.FULL:
        recoverable_from, permanent = watermark, None
    elif recoverability is Recoverability.WINDOW:
        horizon = now - (lookback or timedelta(0))
        recoverable_from = max(watermark, horizon)
        # Anything older than the REST window is gone even though the source
        # nominally has history.
        permanent = (watermark, horizon) if watermark < horizon else None
    else:
        recoverable_from, permanent = None, (watermark, now)

    issue = None
    if permanent is not None:
        start, end = permanent
        issue = Issue(
            kind=IssueKind.GAP,
            # Blocking, not warn: under §4.6 this bars promotion of anything
            # validated on the window. Downgrading it would let a strategy be
            # validated on data the system knows it never collected.
            severity=Severity.BLOCKING,
            period_start=start,
            period_end=end,
            asset=asset,
            detail={
                "cause": "collector_downtime",
                "kind": kind,
                "recoverability": str(recoverability),
                "unrecoverable_seconds": (end - start).total_seconds(),
            },
        )
        log.error("permanent_data_loss", kind=kind, asset=asset,
                  seconds=(end - start).total_seconds())

    return GapPlan(
        kind=kind, asset=asset, gap_start=watermark, gap_end=now,
        recoverability=recoverability, recoverable_from=recoverable_from,
        permanent_loss=permanent, issue=issue,
    )
