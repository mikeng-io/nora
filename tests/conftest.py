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
from pathlib import Path

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from nora.agent.registry import ToolRegistry
from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics

# MUST track `docker-compose.yml`'s `db` image. Production runs PostgreSQL 18
# (`postgres:18-alpine`, musl); this was pinned to 16 while it did, which made
# `tests/infra/test_schema_parity.py` — the gate every migration passes through
# — validate against a major version behind the thing it guards, and made
# `scripts/gen_pg_baseline.py` generate its oracle there too. Found 2026-08-20
# by the fact-store workstream while measuring Apache AGE availability.
# `test_the_test_container_tracks_the_deployment` pins the two together.
PG_IMAGE = "postgres:18-alpine"

# The runtime_context values the shipped doctrine is read with in tests. They
# are module constants rather than inline literals because
# `tests/fixtures/tag_doctrine_bodies.txt` was captured from this same builder —
# `Doctrine.text` appends a `<runtime_context>` block built from exactly these
# three, so a mismatch would fail a body comparison for a reason that has
# nothing to do with doctrine.
DOCTRINE_CANARY = "test-canary"
DOCTRINE_DISPLAY_NAME = "Nora"
DOCTRINE_TRIGGER = "nora.000"


def build_test_doctrine_store() -> DoctrineStore:
    """The shipped doctrine, assembled the way the composition root assembles it.

    Module-level, not fixture-only, so a one-off script can build the identical
    store outside a pytest run — that is how
    `tests/fixtures/tag_doctrine_bodies.txt` was captured.
    """
    return DoctrineStore(
        Path("nora/doctrine"), strict=True, reload="startup",
        canary=DOCTRINE_CANARY, display_name=DOCTRINE_DISPLAY_NAME,
        trigger=DOCTRINE_TRIGGER, language_hint="zh-HK",
        tool_names=ToolRegistry().tool_names(),
        capability=ToolRegistry().capability(),
        metrics=Metrics(),
    )


@pytest.fixture
def doctrine_store() -> DoctrineStore:
    return build_test_doctrine_store()


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


@pytest.fixture(scope="session")
def pg_template(pg_admin: _PgAdmin) -> str:
    """One database with the WHOLE migration chain applied, built once per
    session and used as a `CREATE DATABASE ... TEMPLATE` source.

    **Why this exists, measured 2026-08-21.** `pg_url` below hands out a fresh
    database per test and ~54 call sites then run the full Alembic chain into
    it. At 27 revisions and rising that is the dominant cost of a full run, and
    it is not paid where it is spent: replaying migrations hammers the shared
    testcontainer, and the bill lands on
    `tests/integration/test_latency.py::test_overhead_stays_within_the_budget`,
    which writes audit rows to the same container and asserts a p95. That test
    passes standalone and in its own directory and fails in the full suite —
    the signature of a cost incurred by other files.

    Copying a template is a file-level clone inside Postgres, so a per-test
    database still costs a `CREATE DATABASE` and nothing more. Isolation is
    UNCHANGED: every test still gets its own database, and `run_migrations` at
    the call sites becomes a cheap no-op because the chain is already at head.

    Session-scoped on purpose. It is never written to — Postgres refuses to
    copy a template with other sessions connected, and the only connection it
    ever has is the one that migrates it here.
    """
    dbname = "t_template_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    from nora.infra.sql.migrations_runner import run_migrations

    run_migrations(_async_url(pg_admin, dbname))
    return dbname


@pytest.fixture
def pg_url(pg_admin: _PgAdmin, pg_template: str, monkeypatch) -> Iterator[str]:
    """A fresh PostgreSQL database at the migration head, and the asyncpg URL
    that reaches it.

    Also exported to the environment as `DATABASE_URL`, so any test that builds a
    full `Config` and runs the composition root (which reads
    `store.database_url_env`) is pointed at this same fresh database with no
    per-call-site threading. The database is dropped on teardown; open
    connections are terminated first so the DROP cannot hang on a pooled handle.

    **Cloned from `pg_template`, not created empty** — see that fixture. Callers
    that still call `run_migrations(url)` are correct and now cheap: the chain
    is already at head, so it costs one connection and a version check instead
    of 27 revisions. Callers that never migrated at all get a migrated database
    where they used to get an empty one, which is a strictly larger schema and
    breaks nothing that was passing.
    """
    dbname = "t_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        # Terminate stragglers on the TEMPLATE first: Postgres refuses to copy a
        # database that has any other session connected, and a pooled handle
        # from a previous clone is exactly the kind of thing that lingers.
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{dbname}" TEMPLATE "{pg_template}"')
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


@pytest.fixture
def no_build_stamp():
    """Force `nora/version.json` ABSENT for the duration of a test.

    `scripts/build-image.sh` writes that file into the working tree, and it is
    gitignored — so once anyone runs a real build (V1, 2026-08-11) it stays
    there silently, and every test that assumed "a source checkout has no
    stamp" quietly starts exercising the STAMPED path while claiming to prove
    the unstamped one. Two tests went red the first time a build ran; they were
    the honest ones. Any test asserting unstamped behaviour takes this rather
    than trusting the machine's mood.
    """
    stamp = Path(__file__).resolve().parents[1] / "nora" / "version.json"
    # RENAMED aside, not read-into-memory-and-deleted. The file is gitignored,
    # so if the process is SIGKILLed between the delete and the restore, git
    # cannot bring it back and the next build silently ships unstamped. A
    # sibling rename survives process death — the file is still there to move
    # back by hand (verification pass, 2026-08-11).
    parked = stamp.with_suffix(".json.parked-by-test")
    moved = stamp.exists()
    if moved:
        stamp.replace(parked)
    try:
        yield stamp
    finally:
        if moved:
            parked.replace(stamp)
