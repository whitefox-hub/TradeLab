"""Universe-wide historical backfill. Plan.md §4.1, §17 phase 1.

Designed around the fact that this runs for days: it must be interruptible at any
moment, resume without re-downloading, and show progress while it works.

Resumability is by file existence rather than by bookkeeping. Partitions are
immutable (§3.2), so a partition that exists is a partition that is complete, and
that makes resume correct even after a hard kill — no state to reconcile, nothing
to trust. The watermark is for observability, not for correctness.
"""

from __future__ import annotations

import concurrent.futures as futures
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import structlog

from tradelab.collectors.base import Batch, Issue, Severity
from tradelab.collectors.binance.bulk import BinanceKlineBulk
from tradelab.storage.paths import Partition
from tradelab.storage.writer import write_partition

log = structlog.get_logger(__name__)


@dataclass
class Progress:
    assets_done: int = 0
    assets_total: int = 0
    days_written: int = 0
    days_skipped: int = 0     # already on disk
    days_absent: int = 0      # not listed that day: expected, not an error
    rows: int = 0
    bytes_written: int = 0
    issues: list[Issue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def merge(self, other: Progress) -> None:
        with self._lock:
            self.days_written += other.days_written
            self.days_skipped += other.days_skipped
            self.days_absent += other.days_absent
            self.rows += other.rows
            self.bytes_written += other.bytes_written
            self.issues.extend(other.issues)
            self.assets_done += 1


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _days_in(year: int, month: int, start: date, end: date) -> list[date]:
    day = max(start, date(year, month, 1))
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    stop = min(end, last)
    out = []
    while day <= stop:
        out.append(day)
        day += timedelta(days=1)
    return out


def backfill_asset(
    asset: str, start: date, end: date, root: Path, timeframe: str = "1m",
    client: httpx.Client | None = None,
) -> Progress:
    """Backfill one asset, a month per request wherever possible.

    The monthly archive holds the same bytes as its thirty daily files but costs
    one request instead of thirty, and the archive is latency-bound rather than
    bandwidth-bound. The trailing month falls back to daily files, because the
    monthly archive for an unfinished month does not exist yet.
    """
    collector = BinanceKlineBulk(timeframe, client=client)
    result = Progress()
    current_month = (end.year, end.month)

    for year, month in _months(start, end):
        wanted = [
            day for day in _days_in(year, month, start, end)
            if not _partition(root, asset, day, timeframe).exists()
        ]
        result.days_skipped += len(_days_in(year, month, start, end)) - len(wanted)
        if not wanted:
            continue

        frame = None
        if (year, month) != current_month:
            try:
                frame = collector.fetch_month(asset, year, month)
            except Exception as exc:
                log.warning("month_fetch_failed", asset=asset, month=f"{year}-{month:02d}",
                            error=str(exc))

        if frame is not None and frame.height:
            _write_days(collector, result, frame, root, asset, timeframe, set(wanted))
            continue

        # No monthly archive: either the trailing partial month, or a month
        # before the contract was listed. Fall through to per-day requests,
        # which distinguish the two.
        for day in wanted:
            _fetch_one_day(collector, result, root, asset, day, timeframe)

    return result


def _partition(root: Path, asset: str, day: date, timeframe: str) -> Path:
    return Partition(kind="ohlcv", venue="binance", asset=asset, day=day,
                     timeframe=timeframe).absolute_path(root)


def _write_days(collector, result: Progress, frame, root: Path, asset: str,
                timeframe: str, wanted: set[date]) -> None:
    import polars as pl

    for (day,), chunk in frame.with_columns(
        pl.col("event_time").dt.date().alias("_day")
    ).group_by(["_day"], maintain_order=True):
        if day not in wanted:
            continue
        chunk = chunk.drop("_day")
        report = collector.validate(Batch("ohlcv", "binance", asset, chunk, collector.schema))
        result.issues.extend(report.issues)
        if not report.ok:
            log.warning("day_refused", asset=asset, day=str(day),
                        issues=[str(i.kind) for i in report.blocking])
            continue
        path = write_partition(chunk, collector.schema, root, "ohlcv", "binance",
                               asset, day, timeframe=timeframe)
        result.days_written += 1
        result.rows += chunk.height
        result.bytes_written += path.stat().st_size


def _fetch_one_day(collector, result: Progress, root: Path, asset: str,
                   day: date, timeframe: str) -> None:
    try:
        frame = collector.fetch_day(asset, day)
    except Exception as exc:
        # One bad day must not abandon six years of an asset. Logged and
        # skipped; the missing partition is visible in the manifest later.
        log.warning("fetch_failed", asset=asset, day=str(day), error=str(exc))
        return

    if frame is None or not frame.height:
        # No archive for that date: the contract was not listed yet. This is
        # the point-in-time universe doing its job, not a gap (§4.4).
        result.days_absent += 1
        return

    report = collector.validate(Batch("ohlcv", "binance", asset, frame, collector.schema))
    result.issues.extend(report.issues)
    if not report.ok:
        log.warning("day_refused", asset=asset, day=str(day),
                    issues=[str(i.kind) for i in report.blocking])
        return

    path = write_partition(frame, collector.schema, root, "ohlcv", "binance",
                           asset, day, timeframe=timeframe)
    result.days_written += 1
    result.rows += frame.height
    result.bytes_written += path.stat().st_size


def backfill_universe(
    assets: dict[str, date], end: date, root: Path, timeframe: str = "1m",
    workers: int = 6, on_asset: object = None,
) -> Progress:
    """Backfill every asset from its listing date.

    `assets` maps symbol to listing date, so each contract is fetched only from
    when it existed. Concurrency is modest on purpose: the archive is a CDN and
    the bottleneck is bandwidth, not request slots.
    """
    total = Progress(assets_total=len(assets))
    local = threading.local()

    def client() -> httpx.Client:
        if not hasattr(local, "client"):
            local.client = httpx.Client(timeout=120.0, follow_redirects=True)
        return local.client

    def work(item: tuple[str, date]) -> None:
        asset, listed = item
        result = backfill_asset(asset, listed, end, root, timeframe, client())
        total.merge(result)
        log.info(
            "asset_done", asset=asset, progress=f"{total.assets_done}/{total.assets_total}",
            written=result.days_written, skipped=result.days_skipped,
            absent=result.days_absent, gb=round(total.bytes_written / 2**30, 2),
        )
        if callable(on_asset):
            on_asset(asset, result, total)

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, sorted(assets.items())))

    blocking = [i for i in total.issues if i.severity is Severity.BLOCKING]
    log.info("backfill_complete", assets=total.assets_done, days=total.days_written,
             rows=total.rows, gb=round(total.bytes_written / 2**30, 2),
             issues=len(total.issues), blocking=len(blocking))
    return total


def backfill_funding(
    asset: str, start: date, end: date, root: Path, client: httpx.Client | None = None,
) -> Progress:
    """Funding history for one asset, a month per request.

    Funding is the positioning component of the state index (§11.4) and the
    clearest expression of the differentiation thesis: it settles every eight
    hours, it is not a price derivative, and it says who is crowded. Resumable by
    file existence like everything else.
    """
    from tradelab.collectors.binance.bulk import BinanceFundingBulk

    collector = BinanceFundingBulk(client=client)
    result = Progress()

    for year, month in _months(start, end):
        days = _days_in(year, month, start, end)
        if all(_funding_partition(root, asset, d).exists() for d in days):
            result.days_skipped += len(days)
            continue
        try:
            frame = collector.fetch_month(asset, year, month)
        except Exception as exc:
            log.warning("funding_month_failed", asset=asset,
                        month=f"{year}-{month:02d}", error=str(exc))
            continue
        if frame is None or not frame.height:
            result.days_absent += len(days)
            continue

        report = collector.validate(Batch("funding", "binance", asset, frame, collector.schema))
        result.issues.extend(report.issues)
        if not report.ok:
            log.warning("funding_month_refused", asset=asset, month=f"{year}-{month:02d}")
            continue

        import polars as pl

        tagged = frame.with_columns(pl.col("event_time").dt.date().alias("_day"))
        for (day,), chunk in tagged.group_by(["_day"], maintain_order=True):
            target = _funding_partition(root, asset, day)
            if target.exists():
                result.days_skipped += 1
                continue
            path = write_partition(chunk.drop("_day"), collector.schema, root,
                                   "funding", "binance", asset, day)
            result.days_written += 1
            result.rows += chunk.height
            result.bytes_written += path.stat().st_size

    return result


def _funding_partition(root: Path, asset: str, day: date) -> Path:
    return Partition(kind="funding", venue="binance", asset=asset, day=day).absolute_path(root)


def backfill_funding_universe(
    assets: dict[str, date], end: date, root: Path, workers: int = 12,
) -> Progress:
    total = Progress(assets_total=len(assets))
    local = threading.local()

    def client() -> httpx.Client:
        if not hasattr(local, "client"):
            local.client = httpx.Client(timeout=120.0, follow_redirects=True)
        return local.client

    def work(item: tuple[str, date]) -> None:
        asset, listed = item
        total.merge(backfill_funding(asset, listed, end, root, client()))
        log.info("funding_asset_done", asset=asset,
                 progress=f"{total.assets_done}/{total.assets_total}",
                 days=total.days_written, rows=total.rows)

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, sorted(assets.items())))
    return total


def listing_dates(limit: int | None = None, min_history_days: int = 365) -> dict[str, date]:
    """Universe with its listing dates, filtered to contracts old enough to matter.

    Ordered by listing date rather than by current volume: ranking by today's
    liquidity would select the winners of the last six years, which is
    survivorship bias introduced while building the universe (§4.4).
    """
    from tradelab.collectors.binance.universe import snapshot

    cutoff = datetime.now(UTC) - timedelta(days=min_history_days)
    rows = [
        r for r in snapshot()
        if r["quote_asset"] == "USDT" and r["status"] == "TRADING" and r["listed_at"] <= cutoff
    ]
    rows.sort(key=lambda r: (r["listed_at"], r["asset"]))
    selected = rows[:limit] if limit else rows
    return {r["asset"]: r["listed_at"].date() for r in selected}
