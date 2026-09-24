"""Programmatic Alembic. Runtime never reads `alembic.ini`.

`alembic.ini` exists at the repo root for CLI use (`uv run alembic check`,
`uv run alembic revision --autogenerate`). The application builds its Config
here instead, so a missing or unmounted ini can never stop Nora from starting.

Migrations run SYNCHRONOUSLY (Alembic is a sync tool), against a
`postgresql+psycopg://` URL, while the application itself talks to the same
database over `postgresql+asyncpg://`. `sync_url_for` derives the sync URL from
whatever URL it is handed, swapping the async driver for psycopg so a single
`DATABASE_URL` (asyncpg-shaped, what the app uses) is the one source of truth.

**Concurrency guard, added after 2026-09-24.** Deploying migration
`0062_standing_revision_source` recreated nora, presence and analytics from the
same image at once; all three run `run_migrations` at their own startup (see
each composition root), and two hit the shared database's `ALTER TABLE
standing_revision ADD COLUMN source` at the same time. One won the race, the
other raised `psycopg.errors.DuplicateColumn`, crashed, and came up clean on
its one restart — nothing enforced serial access. `run_migrations` and
`stamp_head` now wrap their Alembic call in a Postgres SESSION-level advisory
lock (`MIGRATION_LOCK_KEY`, a fixed 64-bit key both hold), taken on a DEDICATED
connection so a caller that already holds a connection or is inside its own
transaction is never touched. A losing process POLLS for the lock (see
`MIGRATION_LOCK_POLL_INTERVAL_S` — a plain blocking wait deadlocks against this
repo's one `CREATE INDEX CONCURRENTLY` migration), then finds the database
already at head and does nothing — `command.upgrade`/`command.stamp` are
idempotent, so this is a no-op, not a retry. The lock is released in
`finally`, including on an Alembic failure, so one caller's error never wedges
the database for the next.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"

#: Session-level advisory lock key serializing `run_migrations`/`stamp_head`
#: across concurrent processes racing Alembic against the same database (see
#: module docstring). Fixed and documented so every caller computes the same
#: value; changing it is a breaking change for any process mid-upgrade, so
#: grep every reader (`MIGRATION_LOCK_KEY`) before touching it. Spells
#: "NORAMIGR" in ASCII, chosen only to be recognisable in `pg_locks`, not for
#: any semantic meaning; the top byte (0x4E) keeps it a positive `bigint`.
MIGRATION_LOCK_KEY = 0x4E4F52414D494752

#: Poll interval for `_migration_lock`'s wait. NOT a plain blocking
#: `pg_advisory_lock` — see that function's docstring for why a single
#: multi-second blocking call is unsafe here: migration `0013_room_language`
#: runs `CREATE INDEX CONCURRENTLY`, which must wait for every OTHER backend's
#: in-progress statement on the database to finish before it can complete,
#: with no exception for one that is merely blocked acquiring an advisory
#: lock. A loser parked inside one long `pg_advisory_lock` call IS such an
#: in-progress statement, so it and the winner's `CREATE INDEX CONCURRENTLY`
#: would wait on each other forever — reproduced 2026-09-25 as a genuine hang,
#: not a false alarm: Postgres's own deadlock detector cannot see it, because
#: the winner's dependency on the loser is a real lock wait but the loser's
#: dependency on the winner exists only in this Python code, never in
#: `pg_locks`. Polling with `pg_try_advisory_lock` keeps every attempt a
#: near-instant, self-contained statement, so the backend is genuinely idle
#: between tries and carries no long-lived snapshot for CONCURRENTLY to wait
#: on.
MIGRATION_LOCK_POLL_INTERVAL_S = 0.1


def sync_url_for(url: str) -> str:
    """The synchronous (psycopg) URL Alembic uses, derived from the application
    URL (which is asyncpg — see `nora.infra.sql.engine.create_sql_engine`).

    An asyncpg driver is swapped for psycopg; a URL that already names a sync
    driver, or a bare `postgresql://`, is normalised to `postgresql+psycopg`.
    """
    parsed = make_url(url)
    return parsed.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sync_url_for(url))
    return cfg


def head_revision() -> str:
    return ScriptDirectory(str(_MIGRATIONS)).get_current_head() or ""


def current_revision(url: str) -> str | None:
    engine = create_engine(sync_url_for(url))
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


@contextlib.contextmanager
def _migration_lock(url: str) -> Iterator[None]:
    """Hold `MIGRATION_LOCK_KEY` as a Postgres session-level advisory lock for
    the duration of the wrapped Alembic call, on a connection dedicated to the
    lock alone — never the one Alembic migrates through, so a caller already
    holding a connection or running inside its own transaction is unaffected.

    Waits by POLLING `pg_try_advisory_lock` (`MIGRATION_LOCK_POLL_INTERVAL_S`
    between tries), never the plain blocking `pg_advisory_lock` — see that
    constant's docstring for the deadlock a single long blocking call caused
    against this repo's one `CREATE INDEX CONCURRENTLY` migration. Always
    releases in `finally`, including when the wrapped call raises, so one
    failed migration never wedges the database for the next caller.
    """
    engine = create_engine(sync_url_for(url))
    try:
        with engine.connect() as conn:
            while not conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            ).scalar():
                conn.commit()
                time.sleep(MIGRATION_LOCK_POLL_INTERVAL_S)
            conn.commit()
            try:
                yield
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
                conn.commit()
    finally:
        engine.dispose()


def run_migrations(url: str) -> None:
    """`alembic upgrade head` against `url`, serialized against every other
    `run_migrations`/`stamp_head` caller on the same database (module
    docstring). A process that loses the race waits, then finds the database
    already at head — `alembic upgrade head` is idempotent, so this is a
    no-op — and returns exactly as it would have running alone."""
    with _migration_lock(url):
        command.upgrade(alembic_config(url), "head")


def stamp_head(url: str) -> None:
    """Mark an already-correct database as being at head without running any
    migration. Used once, on the live 0-row database at cutover (design/20 §5).
    Serialized the same way as `run_migrations`, against the same lock key —
    the two must never run against one database at the same time either."""
    with _migration_lock(url):
        command.stamp(alembic_config(url), "head")
