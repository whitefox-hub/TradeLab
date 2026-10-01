"""Materialised bar aggregates. Plan.md §5.7, §9.6.

A cache of bars derived from the 1m archive (still the source of truth, §5.7). Each
partition records a hash of its source files, so a changed source makes it detectably stale.
"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import structlog

from tradelab.storage.query import aggregate_bars

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Materialised:
    asset: str
    timeframe: str
    path: Path
    rows: int
    source_hash: str


def _source_hash(root: Path, asset: str, source_tf: str) -> str:
    """Hash of the source partition list: names and sizes, not contents.

    Sufficient because partitions are immutable (§3.2); contents would cost gigabytes of reads.
    """
    base = root / "ohlcv" / f"tf={source_tf}" / "venue=binance" / f"asset={asset}"
    if not base.exists():
        return ""
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*.parquet")):
        digest.update(path.name.encode())
        digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


def target_path(root: Path, asset: str, timeframe: str) -> Path:
    return root / "_bars" / f"tf={timeframe}" / f"asset={asset}.parquet"


def is_stale(root: Path, asset: str, timeframe: str, source_tf: str = "1m") -> bool:
    path = target_path(root, asset, timeframe)
    if not path.exists():
        return True
    try:
        stored = pl.read_parquet_schema(path)
        metadata = pl.scan_parquet(path).select("source_hash").head(1).collect()
    except Exception:
        return True
    if "source_hash" not in stored or not metadata.height:
        return True
    return metadata["source_hash"][0] != _source_hash(root, asset, source_tf)


def materialise_asset(
    root: Path, asset: str, timeframe: str, source_tf: str = "1m",
) -> Materialised | None:
    """Derive and persist one asset's bars, with funding attached."""
    from tradelab.orchestration.study import with_funding

    digest = _source_hash(root, asset, source_tf)
    if not digest:
        return None

    frame = aggregate_bars(root, asset, timeframe, source_tf)
    if not frame.height:
        return None
    frame = (
        frame.drop("event_time", "available_at")
        .rename({"bucket": "event_time"})
        .drop_nulls(subset=["open", "high", "low", "close"])
    )
    frame = with_funding(root, asset, frame).with_columns(
        source_hash=pl.lit(digest), asset=pl.lit(asset)
    )

    path = target_path(root, asset, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path, compression="zstd", compression_level=3)
    return Materialised(asset, timeframe, path, frame.height, digest)


def materialise_universe(
    root: Path, assets: list[str], timeframes: tuple[str, ...] = ("1h", "4h"),
    workers: int = 8, force: bool = False,
) -> list[Materialised]:
    done: list[Materialised] = []

    def work(item: tuple[str, str]) -> None:
        asset, timeframe = item
        if not force and not is_stale(root, asset, timeframe):
            return
        try:
            result = materialise_asset(root, asset, timeframe)
        except Exception as exc:
            log.warning("materialise_failed", asset=asset, tf=timeframe, error=str(exc))
            return
        if result is not None:
            done.append(result)

    jobs = [(a, tf) for a in assets for tf in timeframes]
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, jobs))
    return done


def load_materialised(root: Path, asset: str, timeframe: str) -> pl.DataFrame | None:
    path = target_path(root, asset, timeframe)
    if not path.exists():
        return None
    return pl.read_parquet(path).drop("source_hash")
