"""The shared-database hazard, against a real Postgres: a run whose migration
head is AHEAD of the shared `<source>_blank` template must never move that
shared database's own alembic version — it must build and migrate a
PRIVATE copy instead, and drop it when the run ends.

Two throwaway databases stand in for the two real shared names
(`nora_eval`/`nora_eval_blank`): a `<prefix>_blank` created here and brought
to an OLD revision by hand — standing in for "what every OTHER session on
the host already depends on" — then `nora.subject.sandbox._blank_template`
is called against it with THIS checkout's real, current migration head
(never mocked: `head_revision()` reads the actual `nora/infra/sql/
migrations/versions/` directory, and the point of an integration test is
that nothing about Alembic itself is faked).

**Revert-proved, not merely observed.** A shared database that happens not
to have moved proves nothing on its own — unguarded code would also leave it
unmoved if nobody had touched `nora_eval_blank` in between. The test therefore
also calls `nora.infra.sql.run_migrations` directly against the SAME shared
database afterwards and shows THAT does move its version — the thing
`_blank_template` is built never to do on its own, reproduced on demand rather
than assumed.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from alembic import command

from nora.infra.sql.migrations_runner import (
    alembic_config,
    current_revision,
    head_revision,
    run_migrations,
)
from nora.subject import sandbox
from tests.conftest import _async_url

#: The very first migration (`nora/infra/sql/migrations/versions/
#: 0001_pg_baseline.py`) — a real, stable revision id nowhere near this
#: checkout's actual head, which is what makes bringing a throwaway
#: database here (rather than to `head`) a faithful stand-in for "a shared
#: template another, older session already established".
_OLD_REVISION = "0001_pg_baseline"


def _create_database(pg_admin: Any, name: str) -> None:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')


def _drop_database(pg_admin: Any, name: str) -> None:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _databases(pg_admin: Any) -> set[str]:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        return {row[0] for row in conn.execute("SELECT datname FROM pg_database")}


@pytest.fixture
def shared_names(pg_admin: Any):
    """`(source_url, blank_name, blank_url)` for one test's throwaway
    "shared" template, dropped afterwards, with `sandbox`'s own
    process-lifetime caches reset around it — otherwise a previous test's
    divergence decision for a different source would leak into this one
    (`nora.subject.sandbox`'s module docstring: "once per process per
    source")."""
    import uuid

    prefix = f"t_evaldb_{uuid.uuid4().hex[:10]}"
    # `source_url` IS created (unmigrated — no `alembic_version` row at all,
    # which `current_revision` reads back as `None`) so `shared_database_
    # versions` can read it. `_blank_template` itself never opens it —
    # only ever derives sibling names from it (the real harness's source,
    # `nora_eval`, is likewise never touched by that function; see
    # `nora/subject/sandbox.py`'s module docstring) — the first test below
    # does not migrate it and never asserts anything about its revision.
    source_url = _async_url(pg_admin, prefix)
    blank_name = f"{prefix}_blank"
    blank_url = _async_url(pg_admin, blank_name)
    sandbox._migrated.clear()
    sandbox._private_blanks.clear()
    _create_database(pg_admin, prefix)
    _create_database(pg_admin, blank_name)
    try:
        yield source_url, blank_name, blank_url
    finally:
        sandbox._migrated.clear()
        for name in [n for n in _databases(pg_admin) if n.startswith(prefix)]:
            _drop_database(pg_admin, name)
        sandbox._private_blanks.clear()


async def test_a_branch_ahead_run_never_migrates_the_shared_template(
    pg_admin: Any, shared_names,
) -> None:
    source_url, blank_name, blank_url = shared_names

    # Establish the SHARED template at an OLD revision — standing in for
    # "already established by another session", and deliberately NOT this
    # checkout's real head, so this process is genuinely "ahead" of it.
    command.upgrade(alembic_config(blank_url), _OLD_REVISION)
    before = current_revision(blank_url)
    assert before == _OLD_REVISION
    local_head = head_revision()
    assert before != local_head, (
        "the scenario needs a real gap between the shared template and this "
        "checkout's head — if this fires, 0001_pg_baseline IS head and the "
        "test is not exercising divergence at all"
    )

    private_name = await sandbox._blank_template(source_url)

    # THE CRUX: the shared database's own alembic version did not move.
    assert current_revision(blank_url) == before

    # A private copy was built instead, and IT is at this process's head.
    assert private_name != blank_name
    assert private_name in _databases(pg_admin)
    private_url = _async_url(pg_admin, private_name)
    assert current_revision(private_url) == local_head

    # Cleanup drops the private copy and only the private copy.
    await sandbox.close_private_blank_templates()
    assert private_name not in _databases(pg_admin)
    assert blank_name in _databases(pg_admin)
    assert current_revision(blank_url) == before  # still unmoved after cleanup

    # REVERT-PROVE: the same shared database, migrated directly (the unguarded
    # call, `run_migrations(url)` unconditionally) DOES move —
    # proving the guard is what held it still above, not some unrelated
    # reason Alembic left it alone.
    run_migrations(blank_url)
    assert current_revision(blank_url) != before
    assert current_revision(blank_url) == local_head


async def test_assert_shared_databases_unchanged_catches_a_real_mutation(
    pg_admin: Any, shared_names,
) -> None:
    """The run-start/run-end guard `evals.__main__.main` wraps every run in,
    against real reads of a real database — not the mocked
    `shared_database_versions` `tests/unit/test_sandbox_shared_database_guard.py`
    uses to pin the pure orchestration."""
    source_url, _blank_name, blank_url = shared_names
    command.upgrade(alembic_config(source_url), _OLD_REVISION)
    command.upgrade(alembic_config(blank_url), _OLD_REVISION)

    before = sandbox.shared_database_versions(source_url)
    assert before.source_rev == _OLD_REVISION
    assert before.blank_rev == _OLD_REVISION

    sandbox.assert_shared_databases_unchanged(source_url, before)  # stable: no raise

    # A real mutation — the exact one this whole module exists to prevent —
    # is caught, loudly, by the same function `main()` calls in its `finally`.
    run_migrations(blank_url)
    with pytest.raises(sandbox.SharedDatabaseMutatedError, match="template moved"):
        sandbox.assert_shared_databases_unchanged(source_url, before)


async def test_sweep_stale_private_blank_templates_against_real_postgres(
    pg_admin: Any, shared_names,
) -> None:
    """The sweep's SQL, against real `pg_database`/`pg_stat_activity` — the
    offline `tests/unit/test_sandbox_shared_database_guard.py` fakes those
    tables' shape; this proves the real queries parse and mean what the
    fake assumes. Two private-blank-shaped databases, one genuinely idle and
    old, one old but held open by a live connection: only the idle one goes.
    """
    from datetime import UTC, datetime, timedelta

    source_url, blank_name, _blank_url = shared_names
    old = datetime.now(UTC) - timedelta(hours=7)
    idle_name = f"{blank_name}_run_{old:%Y%m%dT%H%M%S}-aaaa"
    active_name = f"{blank_name}_run_{old:%Y%m%dT%H%M%S}-bbbb"
    _create_database(pg_admin, idle_name)
    _create_database(pg_admin, active_name)

    with psycopg.connect(pg_admin.dsn(active_name)):
        # A LIVE connection held open on `active_name` for the duration of
        # the sweep call — the exact condition that must protect it.
        swept = await sandbox.sweep_stale_private_blank_templates(source_url)

    assert idle_name in swept
    assert active_name not in swept
    databases = _databases(pg_admin)
    assert idle_name not in databases
    assert active_name in databases
