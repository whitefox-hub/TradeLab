"""Shared fixtures. Plan.md §16.8."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

for _key, _value in {
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "POLARS_MAX_THREADS": "4",
    "NUMBA_NUM_THREADS": "4",
    "TZ": "UTC",
}.items():
    os.environ.setdefault(_key, _value)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def golden_path() -> Path:
    return REPO_ROOT / "tests" / "golden" / "expected.json"


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    root = tmp_path / "parquet"
    root.mkdir()
    return root
