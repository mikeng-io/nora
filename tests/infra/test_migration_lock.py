"""The advisory-lock guard around `run_migrations`/`stamp_head` — see the
module docstring in `nora.infra.sql.migrations_runner`.

**The hazard.** Nora, presence and analytics recreated from the same image at
once all run `run_migrations` at their own startup, and two can hit the same
`ALTER TABLE ... ADD COLUMN` on the shared database at the same time: one wins,
the other raises `psycopg.errors.DuplicateColumn` and crashes. The lock
serializes them.

`test_run_migrations_blocks_while_the_lock_is_held` and
`test_stamp_head_blocks_while_the_lock_is_held` are the tests that PROVE the
guard: each holds `MIGRATION_LOCK_KEY` from a raw connection and asserts
Alembic's own entry point (`command.upgrade`/`command.stamp`, replaced with a
spy) is never reached while the lock is held elsewhere. That is deterministic
by construction — it does not depend on a real migration finishing slower than
some wait, a margin that would erode as more migrations are added or on a
slower machine.

`test_concurrent_run_migrations_all_succeed_and_reach_head` is NOT that proof:
five unlocked racers against a fresh database do not reliably raise
`DuplicateColumn` the way the race described above does — `CREATE INDEX CONCURRENTLY`
(migrations 0013/0014/0019) can instead make the whole group HANG (see
`MIGRATION_LOCK_POLL_INTERVAL_S`'s docstring for the exact deadlock). This test
carries a single deadline shared across all five threads (daemon, so a hang
doesn't block interpreter exit), so a missing lock fails it within that deadline
instead of the up-to-5x-60s a per-thread timeout would allow.
"""

from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest

from nora.infra.sql.migrations_runner import (
    MIGRATION_LOCK_KEY,
    current_revision,
    head_revision,
    run_migrations,
    stamp_head,
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


def _hold_lock(pg_admin, dbname: str) -> psycopg.Connection:
    """A raw connection that already holds `MIGRATION_LOCK_KEY`, for a test to
    contend against. Caller releases with `_release_lock`."""
    holder = psycopg.connect(pg_admin.dsn(dbname))
    holder.autocommit = True
    holder.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    return holder


def _release_lock(holder: psycopg.Connection) -> None:
    holder.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    holder.close()


def test_concurrent_run_migrations_all_succeed_and_reach_head(pg_admin):
    """Several processes racing `run_migrations` against the SAME fresh
    database — the shape of nora/presence/analytics recreated from
    one image at once — all return with no exception, and the database ends
    at head: never partially migrated, never a duplicate-column error.

    NOT the deterministic proof of the guard — see module docstring. A shared
    60s deadline across all five threads (rather than a 60s timeout per
    thread, up to 300s total) means a missing lock fails this within ~60s
    instead of hanging for minutes.
    """
    url, _ = _fresh_empty_database(pg_admin)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            run_migrations(url)
        except BaseException as exc:  # noqa: BLE001 - collected across threads, not raised here
            errors.append(exc)

    threads = [threading.Thread(target=_run, daemon=True) for _ in range(5)]
    for t in threads:
        t.start()

    deadline = time.monotonic() + 60.0
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))

    assert all(not t.is_alive() for t in threads), (
        "a run_migrations thread hung past the shared deadline"
    )
    assert not errors, f"concurrent run_migrations raised: {errors!r}"
    assert current_revision(url) == head_revision()


def test_run_migrations_blocks_while_the_lock_is_held(pg_admin, monkeypatch):
    """Deterministic proof of the guard (see module docstring): replace
    `command.upgrade` with a spy, hold `MIGRATION_LOCK_KEY` from a raw
    connection, and assert the spy is NOT entered while the lock is held —
    not a timing margin. Release, and the spy runs."""
    url, dbname = _fresh_empty_database(pg_admin)
    entered = threading.Event()
    monkeypatch.setattr(
        "nora.infra.sql.migrations_runner.command.upgrade",
        lambda *_a, **_k: entered.set(),
    )
    holder = _hold_lock(pg_admin, dbname)

    done = threading.Event()

    def _run() -> None:
        run_migrations(url)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        done.wait(timeout=1.0)
        assert not entered.is_set(), (
            "command.upgrade ran while MIGRATION_LOCK_KEY was still held "
            "elsewhere — the advisory lock guard is missing"
        )
    finally:
        _release_lock(holder)

    assert done.wait(timeout=30), "run_migrations never completed after the lock was released"
    t.join(timeout=5)
    assert entered.is_set(), "command.upgrade never ran after the lock was released"


def test_stamp_head_blocks_while_the_lock_is_held(pg_admin, monkeypatch):
    """Same proof as `test_run_migrations_blocks_while_the_lock_is_held`, for
    `stamp_head` — it shares `_migration_lock` and the same lock key
    (module docstring: 'the two must never run against one database at the
    same time either'), so it needs its own guard test, not an inference from
    `run_migrations`'s."""
    url, dbname = _fresh_empty_database(pg_admin)
    entered = threading.Event()
    monkeypatch.setattr(
        "nora.infra.sql.migrations_runner.command.stamp",
        lambda *_a, **_k: entered.set(),
    )
    holder = _hold_lock(pg_admin, dbname)

    done = threading.Event()

    def _run() -> None:
        stamp_head(url)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        done.wait(timeout=1.0)
        assert not entered.is_set(), (
            "command.stamp ran while MIGRATION_LOCK_KEY was still held "
            "elsewhere — the advisory lock guard is missing"
        )
    finally:
        _release_lock(holder)

    assert done.wait(timeout=30), "stamp_head never completed after the lock was released"
    t.join(timeout=5)
    assert entered.is_set(), "command.stamp never ran after the lock was released"


def test_run_migrations_raises_after_the_deadline_while_the_lock_is_held(pg_admin, monkeypatch):
    """A losing process does not wait forever: past `MIGRATION_LOCK_DEADLINE_S`
    it raises `RuntimeError`, so a composition root's existing
    `try/except -> StartupError` around `run_migrations` surfaces it as an
    ordinary, visible startup failure instead of an unexplained hang."""
    import nora.infra.sql.migrations_runner as migrations_runner

    monkeypatch.setattr(migrations_runner, "MIGRATION_LOCK_DEADLINE_S", 0.3)
    url, dbname = _fresh_empty_database(pg_admin)
    holder = _hold_lock(pg_admin, dbname)
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="MIGRATION_LOCK_KEY"):
            run_migrations(url)
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"took {elapsed:.2f}s to raise past a 0.3s deadline"
    finally:
        _release_lock(holder)


def test_run_migrations_unlocks_explicitly_not_only_via_connection_close(pg_admin, monkeypatch):
    """The explicit `pg_advisory_unlock` call is what releases the key, not
    merely `engine.dispose()` closing the session afterward: removing the
    explicit unlock would leave every other test green, because dispose runs
    immediately after anyway and releases the lock incidentally.

    Neuters `Engine.dispose` for this one `run_migrations` call, so the lock
    connection is never actually closed. If the key is free immediately after
    `run_migrations` returns, it can only be because the explicit unlock ran —
    the session that acquired it is (deliberately) still open."""
    import sqlalchemy.engine.base as sa_base

    url, dbname = _fresh_empty_database(pg_admin)
    monkeypatch.setattr(sa_base.Engine, "dispose", lambda self: None)
    run_migrations(url)
    monkeypatch.undo()  # restore real dispose before any other engine needs it

    with psycopg.connect(pg_admin.dsn(dbname)) as conn:
        conn.autocommit = True
        got = conn.execute(
            "SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,)
        ).fetchone()[0]
        assert got is True, (
            "MIGRATION_LOCK_KEY was still held after run_migrations returned "
            "with engine.dispose() neutered — the explicit pg_advisory_unlock "
            "did not run"
        )
        conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


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
