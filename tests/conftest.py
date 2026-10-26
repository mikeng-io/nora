"""Shared fixtures.

The record store is PostgreSQL, so DB-touching tests run against a
REAL Postgres in a testcontainer — never a mock, never an in-memory shim that
would answer every query the same way (CLAUDE.md). One container is shared for
the whole session; **each test that asks for `pg_url` gets its own freshly
CREATEd database**, dropped on teardown, so isolation is a real separate catalog
rather than a shared mutable one.

The container starts lazily on the first `pg_url` request, so the pure-unit
majority of the suite (which uses `FakeRecordStore`) pays nothing for it.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import psycopg
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer

from nora.agent.registry import ToolRegistry
from nora.config import Config
from nora.config import load as load_config
from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics
from tests.builders import copy_example_config

# MUST track `docker-compose.yml`'s `db` image, and
# `test_the_test_container_tracks_the_deployment` is what pins the two
# together. A test container on a different major version than production
# makes `tests/infra/test_schema_parity.py` — the gate every migration passes
# through — validate against a version other than the one it guards, and makes
# `scripts/gen_pg_baseline.py` generate its oracle there too.
#
# The image is Debian/glibc, not Alpine/musl: the two sort `en_US.utf8`
# DIFFERENTLY, so collation-dependent indexes built under one give silently
# wrong range scans when read under the other.
#
# The test container tracking the deployment is also what makes the `vector`
# extension available here at all: `postgres:18-alpine` does not carry it, so
# every migration touching a vector column would fail in CI while working in
# production.
PG_IMAGE = "pgvector/pgvector:pg18"

# The runtime_context values the shipped doctrine is read with in tests. They
# are module constants rather than inline literals because
# `tests/fixtures/tag_doctrine_bodies.txt` is captured from this same builder —
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
    `tests/fixtures/tag_doctrine_bodies.txt` is captured.
    """
    return DoctrineStore(
        Path("nora/doctrine"), strict=True, reload="startup",
        canary=DOCTRINE_CANARY, display_name=DOCTRINE_DISPLAY_NAME,
        trigger=DOCTRINE_TRIGGER, language_hint="zh-HK",
        tool_names=ToolRegistry().tool_names(),
        metrics=Metrics(),
    )


@pytest.fixture
def doctrine_store() -> DoctrineStore:
    return build_test_doctrine_store()


@pytest.fixture
def example_config_path(tmp_path: Path) -> Path:
    """R-B: a test's config file is a copy of `config.example.toml` in
    `tmp_path`. Take THIS when the test edits the TOML before loading it
    (`tests/test_config.py`'s probes, the tomllib-and-rewrite fixtures); take
    `example_config` when it only needs the loaded `Config`. The template is
    the contract `nora/config.py` is written against, so a test that loads it
    tests the code — a test that loaded the deployment's `config.toml` would
    test the operator's choices, and go red when they changed. Only a test
    whose SUBJECT is the deployment reads the real file; those are pinned in
    `tests/architecture/test_config_comes_from_the_template.py`."""
    return copy_example_config(tmp_path)


@pytest.fixture
def example_config(example_config_path: Path, monkeypatch) -> Config:
    """`example_config_path`, loaded through the production loader. The load
    is fail-closed on the LLM key exactly as the daemon's startup is
    (`nora/config.py`: «refusing to start»), so a placeholder is set here —
    nothing loading the template reaches a provider. A test asserting the
    refusal itself takes `example_config_path` and clears the variable."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    return load_config(example_config_path)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def exported_spans(monkeypatch) -> Iterator[Any]:
    """Every span `nora.obs.tracing.span` opens during this test, as EXPORTED
    spans — names, attributes, parents and TRACE IDS.

    Trace ids are the point. A rootless span IS a trace, so «is this one trace
    or three» is a question only the exported ids can answer; asserting that a
    helper was called cannot see the defect at all.

    **It is built with `build_sampler()` — production's own rule.** A test
    provider that sampled everything would export spans the deployment drops,
    so every assertion about «what reaches the collector» would be reading a
    provider no deployment has: the silent-match failure, inside the instrument
    that exists to find silent matches. R-76's ingest half is invisible without
    this, and so is the proof that the tag turn survives it.

    **It patches `get_tracer` rather than installing a global
    `TracerProvider`**, and that is not a style preference.
    `trace.set_tracer_provider` is ONE-SHOT per process — a second call is
    ignored with a warning — so a provider-based fixture silently hands every
    file after the first the FIRST file's exporter, and a provider that has been
    shut down exports nothing at all for the rest of the run. Patching the
    lookup keeps the capture local to one test and leaves the global provider
    untouched.
    """
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from nora.obs import tracing as _tracing

    exporter = InMemorySpanExporter()
    provider = _TracerProvider(sampler=_tracing.build_sampler())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(_tracing.trace, "get_tracer", provider.get_tracer)
    try:
        yield exporter
    finally:
        provider.shutdown()


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


# MUST track docker-compose.yml's rustfs image (the same reason PG_IMAGE
# tracks its `db` image, above).
RUSTFS_IMAGE = "rustfs/rustfs:1.0.0-rc.1"
# Test-only credentials for an ephemeral, throwaway container — never the
# real deployment's RUSTFS_ROOT_USER/PASSWORD, which this fixture never reads.
RUSTFS_TEST_USER = "contract-test-root"
RUSTFS_TEST_PASSWORD = "contract-test-password"  # noqa: S105 — a throwaway test credential


def _wait_until_rustfs_is_serving(host: str, port: int) -> None:
    """The same readiness signal `docker-compose.yml`'s own healthcheck
    settled for: ANY HTTP response means the process is up, because RustFS's
    IAM subsystem answers every route — health path included — with an
    unsigned 403 until it finishes initializing (that healthcheck's own
    comment). A connection-level failure means "not
    listening yet" and is retried; an HTTP error status is still "up"."""
    deadline = time.monotonic() + 30
    url = f"http://{host}:{port}/"
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"rustfs did not start serving on {url} within 30s")


@pytest.fixture(scope="session")
def rustfs_container() -> Iterator[DockerContainer]:
    """A REAL `rustfs/rustfs` container, not a mock or a MinIO stand-in — the
    same "make the fake behave like the real server" posture this file
    already takes for Postgres. Starts lazily, on the first test that asks
    for it (or for `rustfs_client` below), exactly like `pg_url` above."""
    container = (
        DockerContainer(RUSTFS_IMAGE)
        .with_env("RUSTFS_ROOT_USER", RUSTFS_TEST_USER)
        .with_env("RUSTFS_ROOT_PASSWORD", RUSTFS_TEST_PASSWORD)
        .with_exposed_ports(9000)
    )
    with container as c:
        _wait_until_rustfs_is_serving(
            c.get_container_host_ip(), int(c.get_exposed_port(9000))
        )
        yield c


@pytest.fixture(scope="session")
def rustfs_client(rustfs_container: DockerContainer) -> Any:
    """A `boto3` S3 client against the session's `rustfs_container` — the same
    client shape `nora.app.composition._build_blob_store` constructs, so a
    test using this fixture is exercising the real wire protocol, not an
    approximation of it."""
    return boto3.client(
        "s3",
        endpoint_url=f"http://{rustfs_container.get_container_host_ip()}:"
                     f"{rustfs_container.get_exposed_port(9000)}",
        aws_access_key_id=RUSTFS_TEST_USER,
        aws_secret_access_key=RUSTFS_TEST_PASSWORD,
    )


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

    **Why this exists.** `pg_url` below hands out a fresh database per test, and
    running the full Alembic chain into each is the dominant cost of a full run.
    It is not paid where it is spent: replaying migrations hammers the shared
    testcontainer, and the bill lands on
    `tests/integration/test_latency.py::test_overhead_stays_within_the_budget`,
    which writes audit rows to the same container and asserts a p95.

    Copying a template is a file-level clone inside Postgres, so a per-test
    database still costs a `CREATE DATABASE` and nothing more. Isolation is
    unchanged: every test gets its own database, and `run_migrations` at the
    call sites is a cheap no-op because the chain is already at head.

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
    that call `run_migrations(url)` are correct and cheap: the chain is already
    at head, so it costs one connection and a version check. Callers that never
    migrate get a migrated database.
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
    gitignored — so once anyone runs a real build it stays there silently, and
    every test that assumed "a source checkout has no stamp" quietly starts
    exercising the STAMPED path while claiming to prove the unstamped one. Any
    test asserting unstamped behaviour takes this rather than trusting the
    machine's mood.
    """
    stamp = Path(__file__).resolve().parents[1] / "nora" / "version.json"
    # RENAMED aside, not read-into-memory-and-deleted. The file is gitignored,
    # so if the process is SIGKILLed between the delete and the restore, git
    # cannot bring it back and the next build silently ships unstamped. A
    # sibling rename survives process death — the file is still there to move
    # back by hand.
    parked = stamp.with_suffix(".json.parked-by-test")
    moved = stamp.exists()
    if moved:
        stamp.replace(parked)
    try:
        yield stamp
    finally:
        if moved:
            parked.replace(stamp)


#: A deployment-shaped config for tests that COMPOSE A SUBJECT.
#:
#: `nora/subject/obtain.py` points every subject at `config.evaluation.toml`,
#: which is gitignored and written ONLY by `python -m evals`. So on a fresh
#: clone, in CI, or in a new worktree the file does not exist and every such
#: test dies with `FileNotFoundError`. Where it DOES exist, those tests would
#: compose whatever a previous run left behind, from whatever deployment that
#: run pointed at, with no freshness check — the staleness `materialise()`'s
#: own docstring says must never happen.
#:
#: NOT a second composition path. It is the SAME generator, fed a STATED
#: deployment instead of a discovered one — which is what a fixture is. A test
#: run always uses public stated inputs; the live eval CLI explicitly materialises
#: the requested deployment separately.
_STATED_DEPLOYMENT = """\
[llm]
model = "nora"
reasoning_effort = "none"
[dreamer]
enabled = true
model = "nora-dreamer"
reasoning_effort = "low"
[relevance_gate]
enabled = true
shadow = false
[participation]
enabled = true
[standing]
enabled = true
[culture]
enabled = true
[notes]
enabled = true
[store]
prune_enabled = false
"""


@pytest.fixture(autouse=True)
def eval_host_lock_is_this_test_s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m evals` takes the deploy host's eval lock. A test must never
    contend with a real eval on the host, or with another test worker, so each
    test's lock is its own file."""
    from nora.subject import sandbox

    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / sandbox.EVAL_HOST_LOCK.name)


@pytest.fixture(scope="session", autouse=True)
def evaluation_config(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Public stated input, generated once in each pytest worker's own directory.

    Ordinary tests never discover a deployment through HOME or the environment.
    The eval CLI and explicit deployment-subject checks keep their own inputs.
    """
    import importlib

    from evals.subject_config import build

    root = Path(__file__).resolve().parents[1]
    target = tmp_path_factory.mktemp("evaluation") / "config.evaluation.toml"
    example = root / "config.example.toml"
    target.write_text(
        build(example.read_text(encoding="utf-8"), _STATED_DEPLOYMENT), encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(importlib.import_module("nora.subject.obtain"), "_TEMPLATE", target)
        yield target
