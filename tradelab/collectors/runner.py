"""Backfill orchestration (Plan.md §4.2): nothing is written unvalidated; a batch with
blocking issues is refused (not repaired) and its issues are kept."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import structlog

from tradelab.collectors.base import Collector, Issue, Severity
from tradelab.storage.paths import DataKind
from tradelab.storage.writer import next_revision, write_partition

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class BackfillResult:
    written: list[Path] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    rows: int = 0
    refused_days: list[date] = field(default_factory=list)
    absent_days: list[date] = field(default_factory=list)

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.BLOCKING]


def run_backfill(
    collector: Collector,
    asset: str,
    start: datetime,
    end: datetime,
    root: Path,
    *,
    timeframe: str | None = None,
    allow_revision: bool = False,
) -> BackfillResult:
    result = BackfillResult()

    for batch in collector.backfill(asset, start, end):
        report = collector.validate(batch)
        result.issues.extend(report.issues)
        day = batch.frame["event_time"].min().date()  # type: ignore[union-attr]

        if not report.ok:
            result.refused_days.append(day)
            log.warning("batch_refused", asset=asset, day=str(day),
                        issues=[i.kind for i in report.blocking])
            continue

        revision = next_revision(root, _kind(collector), collector.venue, asset, day, timeframe) \
            if allow_revision else 0
        path = write_partition(
            batch.frame, batch.schema, root, _kind(collector), collector.venue,
            asset, day, timeframe=timeframe, revision=revision,
        )
        result.written.append(path)
        result.rows += batch.frame.height

    return result


def _kind(collector: Collector) -> DataKind:
    return collector.kind  # type: ignore[return-value]
