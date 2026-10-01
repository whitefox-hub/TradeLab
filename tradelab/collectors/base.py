"""Collector contract and ingestion-time validation (Plan.md §4.2). Anomalies are recorded,
never silently corrected."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

import polars as pl
import pyarrow as pa


class IssueKind(StrEnum):
    """Mirrors the ck_dqi_kind constraint in the registry (§5.2)."""

    GAP = "gap"
    DUPLICATE = "duplicate"
    OUT_OF_RANGE = "out_of_range"
    CROSS_INCONSISTENT = "cross_inconsistent"
    BACKFILL_MISMATCH = "backfill_mismatch"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCKING = "blocking"


@dataclass(frozen=True, slots=True)
class Issue:
    kind: IssueKind
    severity: Severity
    period_start: datetime
    period_end: datetime
    detail: dict
    asset: str | None = None


@dataclass(slots=True)
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.BLOCKING]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True, slots=True)
class Batch:
    kind: str
    venue: str
    asset: str
    frame: pl.DataFrame
    schema: pa.Schema


@runtime_checkable
class Collector(Protocol):
    """One implementation per source (§4.2)."""

    kind: str
    venue: str

    def backfill(self, asset: str, start: datetime, end: datetime) -> Iterator[Batch]: ...

    def stream(self, assets: list[str]) -> AsyncIterator[Batch]: ...

    def validate(self, batch: Batch) -> ValidationReport: ...

    @property
    def schema(self) -> pa.Schema: ...


def check_schema(batch: Batch) -> list[Issue]:
    """Column presence and order. A silently reordered column is a silent bug."""
    expected = [f.name for f in batch.schema]
    actual = batch.frame.columns
    if missing := [c for c in expected if c not in actual]:
        return [_whole_batch(batch, IssueKind.OUT_OF_RANGE, Severity.BLOCKING,
                             {"missing_columns": missing})]
    return []


def check_timestamps(batch: Batch) -> list[Issue]:
    """The three-timestamp contract of §4.3: `available_at >= event_time` on every row."""
    frame = batch.frame
    issues: list[Issue] = []
    impossible = frame.filter(pl.col("available_at") < pl.col("event_time"))
    if impossible.height:
        issues.append(_whole_batch(batch, IssueKind.OUT_OF_RANGE, Severity.BLOCKING,
                                   {"rows_available_before_event": impossible.height}))
    if not frame["event_time"].is_sorted():
        issues.append(_whole_batch(batch, IssueKind.OUT_OF_RANGE, Severity.WARN,
                                   {"event_time": "not monotonically ordered"}))
    return issues


def check_duplicates(batch: Batch, keys: list[str]) -> list[Issue]:
    dupes = batch.frame.height - batch.frame.unique(subset=keys).height
    if dupes:
        return [_whole_batch(batch, IssueKind.DUPLICATE, Severity.BLOCKING,
                             {"duplicate_rows": dupes, "natural_key": keys})]
    return []


def check_ranges(batch: Batch, positive: list[str], non_negative: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    for column in positive:
        if column in batch.frame.columns and batch.frame.filter(pl.col(column) <= 0).height:
            issues.append(_whole_batch(batch, IssueKind.OUT_OF_RANGE, Severity.BLOCKING,
                                       {"column": column, "rule": "must be > 0"}))
    for column in non_negative:
        if column in batch.frame.columns and batch.frame.filter(pl.col(column) < 0).height:
            issues.append(_whole_batch(batch, IssueKind.OUT_OF_RANGE, Severity.BLOCKING,
                                       {"column": column, "rule": "must be >= 0"}))
    return issues


def check_gaps(batch: Batch, expected: timedelta, sla: timedelta | None = None,
               tolerance: float = 1.5) -> list[Issue]:
    """Gaps beyond the source SLA, reported, never interpolated.

    Threshold is `tolerance x expected`: venue timestamps jitter by milliseconds."""
    if batch.frame.height < 2:
        return []
    threshold = max(expected * tolerance, sla or timedelta(0))
    offending = (
        batch.frame.sort("event_time")
        .with_columns(pl.col("event_time").diff().alias("_d"))
        .filter(pl.col("_d") > threshold)
    )
    return [
        Issue(
            kind=IssueKind.GAP,
            severity=Severity.WARN,
            period_start=row["event_time"] - row["_d"],
            period_end=row["event_time"],
            asset=batch.asset,
            detail={"gap_seconds": row["_d"].total_seconds(),
                    "expected_seconds": expected.total_seconds()},
        )
        for row in offending.iter_rows(named=True)
    ]


def check_ohlc_coherence(batch: Batch) -> list[Issue]:
    """high >= max(open, close) >= min(open, close) >= low, and volume consistency."""
    bad = batch.frame.filter(
        (pl.col("high") < pl.max_horizontal("open", "close"))
        | (pl.col("low") > pl.min_horizontal("open", "close"))
        | (pl.col("high") < pl.col("low"))
        | (pl.col("taker_buy_base") > pl.col("volume"))
    )
    if bad.height:
        return [_whole_batch(batch, IssueKind.CROSS_INCONSISTENT, Severity.BLOCKING,
                             {"incoherent_bars": bad.height})]
    return []


def _whole_batch(batch: Batch, kind: IssueKind, severity: Severity, detail: dict) -> Issue:
    times = batch.frame["event_time"]
    return Issue(
        kind=kind,
        severity=severity,
        period_start=times.min(),  # type: ignore[arg-type]
        period_end=times.max(),  # type: ignore[arg-type]
        asset=batch.asset,
        detail=detail,
    )
