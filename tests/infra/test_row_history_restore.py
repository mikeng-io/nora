"""A data-only `pg_dump` restore of a table carrying `nora_row_history()`
succeeds against a freshly migrated database (migration `0072`).

Against two REAL Postgres databases in the shared testcontainer, never a
mock: the source is `pg_url` (already migrated, already exercising the
trigger the same way `test_row_history.py`'s upsert test does), the target
is a second database cloned from the SAME migrated `pg_template` so it
carries the identical table/function/trigger shape with no rows of its own.

`pg_dump` emits `SELECT pg_catalog.set_config('search_path', '', false);`
ahead of the data section -- `test_schema_parity.py`'s own `_normalise`
already strips that exact line out of its schema dumps. The restore then runs
every table's `COPY`, which fires the same AFTER INSERT/UPDATE/DELETE row
triggers a live write would, with an EMPTY search_path in force. Without
`0072` pinning `nora_row_history()`'s own path, that trigger's unqualified
`row_history` fails to resolve and the whole restore aborts (`ON_ERROR_STOP=1`
below is what turns that into a non-zero `psql` exit rather than a
partially-applied dump); with it, the function resolves `row_history`
regardless of the caller's path and the restore succeeds with the row's
history intact.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine


def _dump_data_only(pg_admin: Any, dbname: str, table: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed argv, trusted testcontainer id
        ["docker", "exec", "-e", f"PGPASSWORD={pg_admin.password}",
         pg_admin.container_id, "pg_dump", "-U", pg_admin.user, "-d", dbname,
         "--data-only", "--no-owner", "--no-privileges", "-t", table],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _restore(pg_admin: Any, dbname: str, dump: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, trusted testcontainer id
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={pg_admin.password}",
         pg_admin.container_id, "psql", "-v", "ON_ERROR_STOP=1", "-U", pg_admin.user,
         "-d", dbname],
        input=dump, capture_output=True, text=True,
    )


@pytest.fixture
def second_db(pg_admin: Any, pg_template: str) -> Iterator[str]:
    """A second database cloned from the SAME migrated template as `pg_url`
    -- the restore target: identical schema (tables, `nora_row_history()`,
    its triggers), no rows of its own to confuse the read-back with."""
    dbname = "t_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{dbname}" TEMPLATE "{pg_template}"')
    try:
        yield dbname
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.mark.asyncio
async def test_a_data_only_dump_restores_into_a_freshly_migrated_database(
    pg_url: str, pg_admin: Any, second_db: str,
) -> None:
    source_engine = create_sql_engine(pg_url)
    try:
        async with source_engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO tag_debounce_state (platform, conversation_id, "
                "last_allowed_at) VALUES ('signal', 'g1', 1)"
            ))
    finally:
        await source_engine.dispose()

    source_dbname = pg_url.rsplit("/", 1)[-1]
    dump = _dump_data_only(pg_admin, source_dbname, "tag_debounce_state")
    result = _restore(pg_admin, second_db, dump)
    assert result.returncode == 0, result.stderr

    target_url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{second_db}"
    )
    target_engine = create_sql_engine(target_url)
    try:
        async with target_engine.connect() as conn:
            live = (await conn.execute(sa.text(
                "SELECT last_allowed_at FROM tag_debounce_state "
                "WHERE platform = 'signal' AND conversation_id = 'g1'"
            ))).scalar_one()
            assert live == 1
            # the restore's COPY is what `nora_row_history()` fired on; a
            # history row is the only evidence the trigger ran at all rather
            # than the restore silently skipping the table.
            history = (await conn.execute(sa.text(
                "SELECT image, superseded_at FROM row_history "
                "WHERE table_name = 'tag_debounce_state'"
            ))).one()
            assert history.image["last_allowed_at"] == 1
            assert history.superseded_at is None
    finally:
        await target_engine.dispose()
