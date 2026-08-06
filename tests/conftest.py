"""Shared fixtures.

The record store is PostgreSQL (design/20), so DB-touching tests run against a
REAL Postgres in a testcontainer — never a mock, never an in-memory shim that
would answer every query the same way (CLAUDE.md). One container is shared for
the whole session; **each test that asks for `pg_url` gets its own freshly
CREATEd database**, dropped on teardown, so isolation is a real separate catalog
rather than a shared mutable one.

The container starts lazily on the first `pg_url` request, so the pure-unit
majority of the suite (which uses `FakeRecordStore`) pays nothing for it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

PG_IMAGE = "postgres:16-alpine"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass(frozen=True)
class _PgAdmin:
    host: str
    port: int
    user: str
    password: str
    maintenance_db: str
    container_id: str

    def dsn(self, dbname: str) -> str:
        return (
            f"host={self.host} port={self.port} user={self.user} "
            f"password={self.password} dbname={dbname}"
        )


@pytest.fixture(scope="session")
def _pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(PG_IMAGE, driver="psycopg") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_admin(_pg_container: PostgresContainer) -> _PgAdmin:
    return _PgAdmin(
        host=_pg_container.get_container_host_ip(),
        port=int(_pg_container.get_exposed_port(5432)),
        user=_pg_container.username,
        password=_pg_container.password,
        maintenance_db=_pg_container.dbname,
        container_id=_pg_container.get_wrapped_container().id,
    )


def _async_url(admin: _PgAdmin, dbname: str) -> str:
    return (
        f"postgresql+asyncpg://{admin.user}:{admin.password}"
        f"@{admin.host}:{admin.port}/{dbname}"
    )


@pytest.fixture
def pg_url(pg_admin: _PgAdmin, monkeypatch) -> Iterator[str]:
    """A fresh, empty PostgreSQL database, and the asyncpg URL that reaches it.

    Also exported to the environment as `DATABASE_URL`, so any test that builds a
    full `Config` and runs the composition root (which reads
    `store.database_url_env`) is pointed at this same fresh database with no
    per-call-site threading. The database is dropped on teardown; open
    connections are terminated first so the DROP cannot hang on a pooled handle.
    """
    dbname = "t_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = _async_url(pg_admin, dbname)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        yield url
    finally:
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
