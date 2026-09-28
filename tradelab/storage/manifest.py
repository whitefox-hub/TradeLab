"""Dataset manifests and content hashing. Plan.md §5.1, §16.1.

A dataset version is the hash of its manifest; every experiment records that hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

CHUNK = 1 << 20


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str  # relative to the archive root
    sha256: str
    size_bytes: int
    rows: int | None = None


@dataclass(frozen=True, slots=True)
class Manifest:
    name: str
    spec: dict
    period_start: datetime
    period_end: datetime
    files: tuple[FileEntry, ...]

    def content_hash(self) -> str:
        """Identity of the dataset: spec and file hashes only (no build time, paths, order)."""
        payload = {
            "name": self.name,
            "spec": self.spec,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "files": sorted(
                ({"path": f.path, "sha256": f.sha256} for f in self.files),
                key=lambda entry: entry["path"],
            ),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def row_count(self) -> int | None:
        counts = [f.rows for f in self.files]
        if any(c is None for c in counts):
            return None
        return sum(c for c in counts if c is not None)

    def to_json(self) -> str:
        return json.dumps(
            {
                **asdict(self),
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "content_hash": self.content_hash(),
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def build(cls, name: str, spec: dict, root: Path, paths: list[Path],
              period_start: datetime, period_end: datetime) -> Manifest:
        entries = tuple(
            FileEntry(
                path=str(p.relative_to(root)),
                sha256=file_hash(p),
                size_bytes=p.stat().st_size,
            )
            for p in sorted(paths)
        )
        return cls(name, spec, period_start, period_end, entries)
