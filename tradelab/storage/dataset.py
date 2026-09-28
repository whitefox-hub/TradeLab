"""Dataset version construction and registration. Plan.md §5.1, §16.1.

A dataset version is the hash of its manifest; every experiment records that hash.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from tradelab.registry.models import DatasetVersion
from tradelab.storage.manifest import FileEntry, Manifest, file_hash
from tradelab.storage.paths import DataKind


def build_manifest(name: str, spec: dict, root: Path, kinds: list[DataKind]) -> Manifest:
    """Manifest over every partition of the given kinds currently in the archive."""
    paths = sorted(p for kind in kinds for p in (root / kind).rglob("*.parquet"))
    if not paths:
        raise ValueError(f"No partitions under {root} for kinds {kinds}")

    entries: list[FileEntry] = []
    period_start = datetime.max.replace(tzinfo=UTC)
    period_end = datetime.min.replace(tzinfo=UTC)
    for path in paths:
        bounds = pl.scan_parquet(path).select(
            pl.col("event_time").min().alias("lo"),
            pl.col("event_time").max().alias("hi"),
            pl.len().alias("n"),
        ).collect()
        period_start = min(period_start, bounds["lo"][0])
        period_end = max(period_end, bounds["hi"][0])
        entries.append(
            FileEntry(
                path=str(path.relative_to(root)),
                sha256=file_hash(path),
                size_bytes=path.stat().st_size,
                rows=int(bounds["n"][0]),
            )
        )

    return Manifest(name, spec, period_start, period_end, tuple(entries))


def register(manifest: Manifest, root: Path, session) -> DatasetVersion:
    """Persist the manifest and record the version. Idempotent by content hash."""
    content_hash = manifest.content_hash()
    existing = (
        session.query(DatasetVersion).filter_by(content_hash=content_hash).one_or_none()
    )
    if existing is not None:
        # Same data, same spec -> same version. Rebuilding must not create a second.
        return existing

    manifest_path = root / "_manifests" / f"{content_hash}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.to_json())

    version = DatasetVersion(
        id=uuid.uuid4(),
        name=manifest.name,
        spec=manifest.spec,
        manifest_uri=str(manifest_path),
        content_hash=content_hash,
        row_count=manifest.row_count(),
        period_start=manifest.period_start,
        period_end=manifest.period_end,
    )
    session.add(version)
    session.commit()
    return version
