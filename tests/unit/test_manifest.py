from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradelab.storage.manifest import Manifest

START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 2, 1, tzinfo=UTC)


def _write(root: Path, name: str, payload: bytes) -> Path:
    path = root / name
    path.write_bytes(payload)
    return path


@pytest.mark.fast
def test_content_hash_is_stable_across_builds(archive_root: Path) -> None:
    files = [_write(archive_root, "a.parquet", b"aaa"), _write(archive_root, "b.parquet", b"bbb")]
    first = Manifest.build("ds", {"assets": ["BTCUSDT"]}, archive_root, files, START, END)
    second = Manifest.build("ds", {"assets": ["BTCUSDT"]}, archive_root, files, START, END)
    assert first.content_hash() == second.content_hash()


@pytest.mark.fast
def test_content_hash_ignores_file_order(archive_root: Path) -> None:
    a = _write(archive_root, "a.parquet", b"aaa")
    b = _write(archive_root, "b.parquet", b"bbb")
    forward = Manifest.build("ds", {}, archive_root, [a, b], START, END)
    reverse = Manifest.build("ds", {}, archive_root, [b, a], START, END)
    assert forward.content_hash() == reverse.content_hash()


@pytest.mark.fast
def test_content_hash_changes_when_any_byte_changes(archive_root: Path) -> None:
    path = _write(archive_root, "a.parquet", b"aaa")
    before = Manifest.build("ds", {}, archive_root, [path], START, END).content_hash()
    path.write_bytes(b"aab")
    after = Manifest.build("ds", {}, archive_root, [path], START, END).content_hash()
    assert before != after


@pytest.mark.fast
def test_content_hash_changes_when_the_spec_changes(archive_root: Path) -> None:
    path = _write(archive_root, "a.parquet", b"aaa")
    one = Manifest.build("ds", {"assets": ["BTCUSDT"]}, archive_root, [path], START, END)
    two = Manifest.build("ds", {"assets": ["ETHUSDT"]}, archive_root, [path], START, END)
    assert one.content_hash() != two.content_hash()
