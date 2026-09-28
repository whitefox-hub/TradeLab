"""Dataset quality report. Plan.md §17 phase 1, §4.2.

Counts present vs expected days per asset and kind; phase 1 exits on zero undocumented gaps.
"""

from __future__ import annotations

import concurrent.futures as futures
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

_DAY = re.compile(r"(\d{4}-\d{2}-\d{2})_r\d+")


@dataclass
class AssetCoverage:
    asset: str
    kind: str
    first: date
    last: date
    days_present: int
    days_expected: int
    missing: list[date] = field(default_factory=list)

    @property
    def completeness(self) -> float:
        return self.days_present / max(self.days_expected, 1)

    @property
    def longest_gap(self) -> int:
        """Longest run of consecutive missing days."""
        if not self.missing:
            return 0
        longest = run = 1
        for previous, current in zip(self.missing, self.missing[1:], strict=False):
            run = run + 1 if (current - previous).days == 1 else 1
            longest = max(longest, run)
        return longest


@dataclass
class QualityReport:
    coverage: list[AssetCoverage]
    kinds: dict[str, int]

    @property
    def total_days(self) -> int:
        return sum(c.days_present for c in self.coverage)

    @property
    def total_missing(self) -> int:
        return sum(len(c.missing) for c in self.coverage)

    def worst(self, n: int = 10) -> list[AssetCoverage]:
        return sorted(self.coverage, key=lambda c: c.completeness)[:n]

    def summary(self) -> str:
        lines = [
            f"{len(self.coverage)} asset/kind series, {self.total_days:,} days present, "
            f"{self.total_missing:,} missing",
        ]
        for kind, count in sorted(self.kinds.items()):
            series = [c for c in self.coverage if c.kind == kind]
            mean = sum(c.completeness for c in series) / max(len(series), 1)
            lines.append(f"  {kind:<16} {count:>8,} files  {len(series):>4} assets  "
                         f"mean completeness {mean:.1%}")
        return "\n".join(lines)


def _days_for(paths: list[Path]) -> set[date]:
    out = set()
    for path in paths:
        match = _DAY.search(path.name)
        if match:
            out.add(date.fromisoformat(match.group(1)))
    return out


def scan_coverage(root: Path, kinds: tuple[str, ...] = ("ohlcv", "funding")) -> QualityReport:
    """Coverage per asset and kind, from partition file names alone (not contents)."""
    by_series: dict[tuple[str, str], list[Path]] = defaultdict(list)
    file_counts: dict[str, int] = {}

    for kind in kinds:
        base = root / kind
        if not base.exists():
            continue
        paths = list(base.rglob("*.parquet"))
        file_counts[kind] = len(paths)
        for path in paths:
            asset = next(
                (p.split("=", 1)[1] for p in path.parts if p.startswith("asset=")), "unknown"
            )
            by_series[(asset, kind)].append(path)

    def build(item: tuple[tuple[str, str], list[Path]]) -> AssetCoverage:
        (asset, kind), paths = item
        days = _days_for(paths)
        first, last = min(days), max(days)
        expected = {first + timedelta(days=i) for i in range((last - first).days + 1)}
        return AssetCoverage(
            asset=asset, kind=kind, first=first, last=last,
            days_present=len(days), days_expected=len(expected),
            missing=sorted(expected - days),
        )

    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        coverage = list(pool.map(build, by_series.items()))

    return QualityReport(coverage=coverage, kinds=file_counts)


def record_gaps(
    report: QualityReport, session, source_name: str = "binance-archive",
    venue: str = "binance", shared_threshold: int = 3,
) -> int:
    """Persist gaps as `data_quality_issues` (§4.6 bars promotion on them). Plan.md §4.2.

    A day missing across >= `shared_threshold` series is a venue gap (`info`); otherwise `warn`.
    """
    from collections import Counter

    from tradelab.collectors.watermark import source_id
    from tradelab.registry.models import DataQualityIssue

    shared = Counter(d for c in report.coverage for d in c.missing)
    source = source_id(session, source_name, "ohlcv", venue, sla_seconds=86_400)
    existing = {
        (i.asset, i.period_start.date(), i.period_end.date())
        for i in session.query(DataQualityIssue).filter_by(source_id=source).all()
    }

    written = 0
    for series in report.coverage:
        for run in _runs(series.missing):
            key = (series.asset, run[0], run[-1])
            if key in existing:
                continue
            venue_wide = shared[run[0]] >= shared_threshold
            session.add(DataQualityIssue(
                source_id=source, asset=series.asset, kind="gap",
                period_start=_as_utc(run[0]), period_end=_as_utc(run[-1]),
                severity="info" if venue_wide else "warn",
                detail={
                    "kind": series.kind, "days": len(run),
                    "series_sharing_first_day": shared[run[0]],
                    "cause": "venue_archive" if venue_wide else "unexplained",
                },
            ))
            written += 1
    session.commit()
    return written


def _runs(days: list[date]) -> list[list[date]]:
    """Group consecutive missing days, so one outage is one issue."""
    out: list[list[date]] = []
    for day in days:
        if out and (day - out[-1][-1]).days == 1:
            out[-1].append(day)
        else:
            out.append([day])
    return out


def _as_utc(day: date):
    from datetime import UTC, datetime

    return datetime(day.year, day.month, day.day, tzinfo=UTC)
