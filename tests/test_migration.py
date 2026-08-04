"""What replaced the PRAGMA user_version migration.

The v1→v2 rebuild this file used to test is gone: `nora/store/migrate.py` was
retired with the persistence phase. Alembic's baseline revision IS the schema,
and the live 0-row database was stamped rather than migrated. Rolling this
phase back is `git revert` plus `DROP TABLE alembic_version` — deliberately
NOT clearing `PRAGMA user_version`, which is still 1, so the retired migrate()
would short-circuit if it ever came back.
"""

from __future__ import annotations

import sqlite3

from nora.infra.sql.migrations_runner import current_revision, head_revision, run_migrations


async def test_startup_migrates_a_missing_database_into_existence(tmp_path):
    path = tmp_path / "nested" / "messages.db"
    run_migrations(path)
    assert path.exists()
    assert current_revision(path) == head_revision()


async def test_startup_is_idempotent_across_restarts(tmp_path):
    path = tmp_path / "messages.db"
    for _ in range(3):
        run_migrations(path)
    assert current_revision(path) == head_revision()


async def test_a_migrated_database_accepts_writes(tmp_path):
    from nora.domain.models import NormalizedMessage
    from nora.infra.sql.audit import SqlAuditStore
    from nora.infra.sql.engine import create_sql_engine

    path = tmp_path / "messages.db"
    run_migrations(path)
    store = SqlAuditStore(create_sql_engine(path))
    try:
        await store.append(message=NormalizedMessage(
            "signal", "g", "group", "+1", "A", 1, "x", {}))
        assert (await store.stats())["messages"] == 1
    finally:
        await store.aclose()


def test_the_alembic_version_table_is_the_only_new_object(tmp_path):
    """Rollback depends on this: dropping one table undoes the phase."""
    path = tmp_path / "messages.db"
    run_migrations(path)
    with sqlite3.connect(path) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "alembic_version" in names
    assert names - {"alembic_version", "sqlite_sequence"} == {"messages"}
