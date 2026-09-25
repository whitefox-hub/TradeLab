"""The macro refresh script. Plan.md §4.7, §20.6, §20.7.

Cron runs this, not the interface. §20.7 requires the system to work headless,
and a collection job that only runs when someone opens a page is one that stops
the day nobody does — while the freshness column keeps reporting an age that
belongs to the scheduler rather than to the feed.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "refresh_macro.sh"


@pytest.mark.fast
def test_the_script_exists_and_is_executable() -> None:
    """A cron entry pointing at a non-executable file fails silently at 06:20."""
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


@pytest.mark.fast
def test_it_fails_loudly_rather_than_half_way() -> None:
    """Without `set -e` a failed ingest still writes "finished" to the log, and
    the log is the only thing anyone will read."""
    body = SCRIPT.read_text()
    assert "set -euo pipefail" in body


@pytest.mark.fast
def test_it_does_not_depend_on_the_working_directory() -> None:
    """cron starts in $HOME, so a relative venv path resolves to nothing."""
    body = SCRIPT.read_text()
    assert 'cd "$(dirname "$0")/.."' in body


@pytest.mark.fast
def test_it_overrides_the_compose_hostname() -> None:
    """`.env` carries `postgres`, the service name, which only resolves inside
    the network. From the host it must be localhost or every run dies in name
    resolution — the same trap `make test-integration` exists for."""
    body = SCRIPT.read_text()
    assert "POSTGRES_HOST" in body
    assert "localhost" in body


@pytest.mark.fast
def test_it_re_reads_a_window_rather_than_only_today() -> None:
    """A single missed night would otherwise leave a permanent hole.

    Revisions are the other reason: FRED restates the dollar index and CPI after
    publication, so re-reading a window picks up a corrected value that a
    today-only job would never see again.
    """
    body = SCRIPT.read_text()
    assert "--since" in body
    assert "days ago" in body


@pytest.mark.fast
def test_the_log_directory_is_not_committed() -> None:
    gitignore = (SCRIPT.parents[1] / ".gitignore").read_text()
    assert "logs/" in gitignore
