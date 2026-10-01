"""Parquet writer. Plan.md §3.2.

Files are immutable. A correction is a new file with a higher revision, never an
overwrite — so a dataset built yesterday can still be rebuilt byte for byte.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from tradelab.storage.paths import DataKind, Partition


class ImmutabilityError(RuntimeError):
    """Raised on any attempt to overwrite an existing partition file."""


def write_partition(
    frame: pl.DataFrame,
    schema: pa.Schema,
    root: Path,
    kind: DataKind,
    venue: str,
    asset: str,
    day: date,
    *,
    timeframe: str | None = None,
    revision: int = 0,
    part: int | None = None,
) -> Path:
    """Write one day of one asset, or one flush of a capture stream."""
    partition = Partition(
        kind=kind, venue=venue, asset=asset, day=day, revision=revision,
        timeframe=timeframe, part=part,
    )
    target = partition.absolute_path(root)
    if target.exists():
        raise ImmutabilityError(
            f"{target} already exists. Corrections are written as revision "
            f"{revision + 1}, never by overwriting — see Plan.md §3.2."
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    table = frame.select([f.name for f in schema]).to_arrow().cast(schema)
    pq.write_table(
        table, target, compression="zstd", compression_level=3,
        row_group_size=200_000, use_dictionary=["venue", "asset"],
    )
    return target


def next_revision(root: Path, kind: DataKind, venue: str, asset: str, day: date,
                  timeframe: str | None = None) -> int:
    """Lowest unused revision for a partition."""
    directory = _directory(root, kind, venue, asset, day, timeframe)
    if not directory.exists():
        return 0
    revisions = [
        int(p.stem.split("_r")[-1].split("_p")[0])
        for p in directory.glob(f"{day.isoformat()}_r*.parquet")
    ]
    return max(revisions, default=-1) + 1


def next_part(root: Path, kind: DataKind, venue: str, asset: str, day: date,
              timeframe: str | None = None, revision: int = 0) -> int:
    """Next flush index for a capture stream within a day."""
    directory = _directory(root, kind, venue, asset, day, timeframe)
    if not directory.exists():
        return 0
    pattern = f"{day.isoformat()}_r{revision:03d}_p*.parquet"
    parts = [int(p.stem.split("_p")[-1]) for p in directory.glob(pattern)]
    return max(parts, default=-1) + 1


def _directory(root: Path, kind: DataKind, venue: str, asset: str, day: date,
               timeframe: str | None) -> Path:
    probe = Partition(kind=kind, venue=venue, asset=asset, day=day, timeframe=timeframe)
    return probe.absolute_path(root).parent
