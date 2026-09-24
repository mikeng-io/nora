"""The advisory-lock guard around `run_migrations`/`stamp_head` — see the
module docstring in `nora.infra.sql.migrations_runner` for the incident it
fixes.

**Observed live, 2026-09-24.** Deploying migration `0062_standing_revision_source`
recreated nora, presence and analytics from the same image at once. All three
run `run_migrations` at their own startup, and two hit the shared database's
`ALTER TABLE standing_revision ADD COLUMN source` at the same time: one won,
the other raised `psycopg.errors.DuplicateColumn`, crashed, and came up clean
on its one restart. Nothing serialized them.

`test_run_migrations_blocks_while_the_lock_is_held` is the test that PROVES
the guard: it holds `MIGRATION_LOCK_KEY` from a raw connection and asserts
`run_migrations` waits on it. Strip the lock out of `run_migrations` and this
goes red — the real DDL race is timing-dependent and does not reproduce on
every run, but a held lock that is never checked is deterministic every time.
"""

from __future__ import annotations

import threading
import uuid

import psycopg
import pytest

from nora.infra.sql.migrations_runner import (
    MIGRATION_LOCK_KEY,
    current_revision,
    head_revision,
    run_migrations,
    sync_url_for,
)


def _fresh_empty_database(pg_admin) -> tuple[str, str]:
    """A brand-new, UNMIGRATED database — never `pg_url`, which is already
    cloned from a migrated template (`tests/conftest.py::pg_template`) and so
    would make every `run_migrations` call here a cheap no-op before either
    the lock or the real DDL is ever exercised. Returns `(async_url, dbname)`;
    `dbname` is for building a raw `psycopg` connection via `pg_admin.dsn`."""
    dbname = "t_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    return url, dbname


def test_concurrent_run_migrations_all_succeed_and_reach_head(pg_admin):
    """Several processes racing `run_migrations` against the SAME fresh
    database — the 2026-09-24 shape, nora/presence/analytics recreated from
    one image at once — all return with no exception, and the database ends
    at head: never partially migrated, never a duplicate-column error."""
    url, _ = _fresh_empty_database(pg_admin)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            run_migrations(url)
        except BaseException as exc:  # noqa: BLE001 - collected across threads, not raised here
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert all(not t.is_alive() for t in threads), "a run_migrations thread hung"
    assert not errors, f"concurrent run_migrations raised: {errors!r}"
    assert current_revision(url) == head_revision()


def test_run_migrations_blocks_while_the_lock_is_held(pg_admin):
    """Deterministic proof of the guard (see module docstring): hold
    `MIGRATION_LOCK_KEY` from a raw connection, start `run_migrations` in a
    thread against the same database, and confirm it is still waiting a beat
    later. Release, and it completes and reaches head."""
    url, dbname = _fresh_empty_database(pg_admin)
    holder = psycopg.connect(pg_admin.dsn(dbname))
    holder.autocommit = True
    holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))

    done = threading.Event()

    def _run() -> None:
        run_migrations(url)
        done.set()

    t = threading.Thread(target=_run)
    t.start()
    try:
        blocked = not done.wait(timeout=1.0)
        assert blocked, (
            "run_migrations returned while MIGRATION_LOCK_KEY was still held "
            "elsewhere — the advisory lock guard is missing"
        )
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
        holder.close()

    assert done.wait(timeout=30), "run_migrations never completed after the lock was released"
    t.join(timeout=5)
    assert current_revision(url) == head_revision()


def test_run_migrations_releases_the_lock_after_an_exception(pg_admin, monkeypatch):
    """The lock is released in `finally`: a failed upgrade must not wedge the
    database for the next caller, and that next caller must then proceed."""
    url, dbname = _fresh_empty_database(pg_admin)

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr("nora.infra.sql.migrations_runner.command.upgrade", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        run_migrations(url)
    monkeypatch.undo()

    # A plain `pg_try_advisory_lock` grabs the key with no wait — it only
    # succeeds if the failed call released it.
    with psycopg.connect(pg_admin.dsn(dbname)) as conn:
        conn.autocommit = True
        got = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)
        ).fetchone()[0]
        assert got is True, "run_migrations did not release the lock after raising"
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))

    # A second, real run proceeds and reaches head.
    run_migrations(url)
    assert current_revision(url) == head_revision()


def test_sync_url_for_round_trips_for_the_lock_connection(pg_admin):
    """`_migration_lock` opens its dedicated connection through `sync_url_for`
    (the same derivation Alembic itself uses) — proof the psycopg-shaped URL
    it hands to a raw `create_engine` actually connects."""
    url, _ = _fresh_empty_database(pg_admin)
    sync_url = sync_url_for(url)
    assert sync_url.startswith("postgresql+psycopg://")
    run_migrations(url)  # exercises the lock connection built from sync_url_for
    assert current_revision(url) == head_revision()
