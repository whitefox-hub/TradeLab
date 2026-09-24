"""Guard against models and migrations drifting apart.

The cheap structural check runs everywhere. The authoritative comparison needs a
live database and is marked `integration` (Plan.md §16.8).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradelab.registry.models import Base

MIGRATIONS = Path(__file__).resolve().parents[2] / "tradelab/registry/migrations/versions"


@pytest.mark.fast
def test_every_model_table_appears_in_a_migration() -> None:
    ddl = "\n".join(p.read_text() for p in MIGRATIONS.glob("*.py"))
    missing = [name for name in Base.metadata.tables if f'"{name}"' not in ddl]
    assert not missing, (
        f"Models define {missing} with no migration. Adding a model without a "
        "migration is how a schema silently diverges from the code that reads it."
    )


@pytest.mark.fast
def test_all_timestamps_are_timezone_aware() -> None:
    """Plan.md §5: every timestamp is timestamptz in UTC. Naive ones are bugs."""
    naive = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.type.__class__.__name__ == "DateTime" and not column.type.timezone
    ]
    assert not naive, f"Naive timestamps: {naive}"


@pytest.mark.fast
def test_migration_generates_valid_ddl() -> None:
    """Offline SQL generation catches malformed migrations without a database."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=MIGRATIONS.parents[3], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table}" in sql, f"{table} missing from generated DDL"


@pytest.mark.integration
def test_migrated_schema_matches_models() -> None:
    """Authoritative check: run the migration, then diff against the metadata."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from tradelab.registry.db import engine

    with engine().connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert not diff, f"Schema drift detected: {diff}"
