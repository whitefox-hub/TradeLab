"""Parquet archive layout. Plan.md §3.2.

Hive-partitioned, immutable files. A correction is a new file with a higher
`revision`, never an UPDATE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

DataKind = Literal[
    "trades", "ohlcv", "funding", "open_interest", "liquidations", "book_features"
]

# Data that CANNOT be backfilled: no historical REST endpoint, or a very short
# window. Capture must start before it is needed (Plan.md §4.1 note).
NON_BACKFILLABLE: frozenset[str] = frozenset({"liquidations", "open_interest", "book_features"})


@dataclass(frozen=True, slots=True)
class Partition:
    kind: DataKind
    venue: str
    asset: str
    day: date
    revision: int = 0
    timeframe: str | None = None  # ohlcv only
    # Streams flush repeatedly within a day, so a capture partition is written as
    # numbered parts. Backfilled partitions leave this None and stay one file.
    part: int | None = None

    def relative_path(self) -> Path:
        parts = [self.kind]
        if self.timeframe:
            parts.append(f"tf={self.timeframe}")
        parts += [
            f"venue={self.venue}",
            f"asset={self.asset}",
            f"year={self.day.year:04d}",
            f"month={self.day.month:02d}",
        ]
        suffix = f"_p{self.part:04d}" if self.part is not None else ""
        filename = f"{self.day.isoformat()}_r{self.revision:03d}{suffix}.parquet"
        return Path(*parts) / filename

    def absolute_path(self, root: Path) -> Path:
        return root / self.relative_path()


def glob_for(root: Path, kind: DataKind) -> str:
    """A DuckDB-friendly glob.

    One `**` only: DuckDB rejects multiple recursive wildcards in a path. Asset
    selection is a WHERE predicate on the Hive partition column instead, which
    prunes files just as effectively and keeps the pattern valid.
    """
    return str(root / kind / "**" / "*.parquet")
