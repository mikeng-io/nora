"""Programmatic Alembic. Runtime never reads `alembic.ini`.

`alembic.ini` exists at the repo root for CLI use (`uv run alembic check`,
`uv run alembic revision --autogenerate`). The application builds its Config
here instead, so a missing or unmounted ini can never stop Nora from starting.

Migrations run SYNCHRONOUSLY (Alembic is a sync tool), against a
`postgresql+psycopg://` URL, while the application itself talks to the same
database over `postgresql+asyncpg://`. `sync_url_for` derives the sync URL from
whatever URL it is handed, swapping the async driver for psycopg so a single
`DATABASE_URL` (asyncpg-shaped, what the app uses) is the one source of truth.

**Concurrency guard.** Several containers built from the same image (nora,
presence, analytics) each run `run_migrations` at their own startup (see each
composition root), so two can race the same DDL on the shared database and one
would fail. `run_migrations` and `stamp_head` therefore wrap their Alembic call
in a Postgres SESSION-level advisory lock (`MIGRATION_LOCK_KEY`, a fixed 64-bit key both hold),
taken on a DEDICATED
connection so a caller that already holds a connection or is inside its own
transaction is never touched. A losing process POLLS for the lock (see
`MIGRATION_LOCK_POLL_INTERVAL_S` — a plain blocking wait deadlocks against this
repo's three `CREATE INDEX CONCURRENTLY` migrations, 0013/0014/0019), logging
once it starts waiting and again every ~30s, and raising after
`MIGRATION_LOCK_DEADLINE_S` rather than waiting forever — a composition root
then surfaces that as an ordinary startup failure instead of an unexplained
hang. A losing process that DOES acquire the lock finds the database already
at head and does nothing — `command.upgrade`/`command.stamp` are idempotent,
so this is a no-op, not a retry. The lock is released in `finally`, including
on an Alembic failure, so one caller's error never wedges the database for the
next; closing the dedicated connection right after (`engine.dispose()`) would
eventually release it too (Postgres drops session-level advisory locks when
the session ends), but the explicit unlock is what makes release immediate and
deterministic rather than incidental to connection teardown.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

log = logging.getLogger("nora.infra.sql.migrations_runner")

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
#: multi-second blocking call is unsafe here: three migrations
#: (`0013_room_language`, `0014_lang_obs_scope_idx`, `0019_exchange_patterns`)
#: run `CREATE INDEX CONCURRENTLY`, which must wait for every OTHER backend's
#: in-progress statement on the database to finish before it can complete,
#: with no exception for one that is merely blocked acquiring an advisory
#: lock. A loser parked inside one long `pg_advisory_lock` call IS such an
#: in-progress statement, so it and the winner's `CREATE INDEX CONCURRENTLY`
#: would wait on each other forever. Postgres's own deadlock detector cannot see it, because
#: the winner's dependency on the loser is a real lock wait but the loser's
#: dependency on the winner exists only in this Python code, never in
#: `pg_locks`. Polling with `pg_try_advisory_lock` keeps every attempt a
#: near-instant, self-contained statement, so the backend is genuinely idle
#: between tries and carries no long-lived snapshot for CONCURRENTLY to wait
#: on.
MIGRATION_LOCK_POLL_INTERVAL_S = 0.1

#: How long `_migration_lock` polls before giving up and raising, rather than
#: waiting forever. Generous on purpose — a real migration chain is seconds,
#: not minutes, so this is sized for "the winner is stuck", not "the winner is
#: slow": a hung winner (e.g. `CREATE INDEX CONCURRENTLY` itself waiting on a
#: long-lived transaction in some other, unrelated container) would otherwise
#: leave every other process waiting silently rather than failing loudly. A
#: composition root's existing `try/except -> StartupError` around
#: `run_migrations` turns the resulting `RuntimeError` into an ordinary,
#: visible startup failure instead of a silent hang.
MIGRATION_LOCK_DEADLINE_S = 600.0

#: How often `_migration_lock` logs while still waiting past the first
#: attempt, so a genuinely long wait shows up in `docker compose logs` rather
#: than only at the deadline.
MIGRATION_LOCK_LOG_INTERVAL_S = 30.0


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


def revision_is_known(revision: str) -> bool:
    """Whether `revision` is one THIS CHECKOUT'S OWN migration chain
    defines — a file under `nora/infra/sql/migrations/versions/` names it
    as its `revision =`.

    Answers "behind head" vs. "ahead of head" with NO database connection,
    using only the local script directory: this repo keeps ONE linear
    alembic chain (`uv run alembic heads` must print exactly one line), so a
    revision this checkout recognizes at all is, by construction, an ANCESTOR of (or equal to)
    `head_revision()` — never a
    sibling branch. A revision this checkout has never heard of is
    therefore either ahead of head (introduced by a branch not yet merged)
    or simply invalid; either way, "known" is what tells the two apart from
    "not known", and `SourceRevisionMismatchError`'s callers use exactly
    that.

    `ScriptDirectory.get_revision` raises `CommandError` for an unknown id
    and returns the `Script` (never `None`) for a known one.
    """
    script = ScriptDirectory(str(_MIGRATIONS))
    try:
        return script.get_revision(revision) is not None
    except CommandError:
        return False


class SourceRevisionMismatchError(RuntimeError):
    """An eval source's alembic revision cannot be trusted for this
    checkout's reads.

    Raised in two places, deliberately the SAME type so a caller handles
    one case rather than two:

    - `nora.subject.sandbox.refuse_if_source_diverges_from_head` — the
      upfront, no-query refusal, when the source is AHEAD of this
      checkout's head (a revision `revision_is_known` does not recognize).
      This never runs a query; it only compares two revision strings.
    - `nora.infra.sql.snapshot.snapshot` — the fail-closed catch, when a
      source BEHIND head (which `refuse_if_source_diverges_from_head`
      allows, since most migrations add nothing `snapshot()` selects) turns
      out to be missing a column or table a read actually named.
    """


@contextlib.contextmanager
def _migration_lock(url: str) -> Iterator[None]:
    """Hold `MIGRATION_LOCK_KEY` as a Postgres session-level advisory lock for
    the duration of the wrapped Alembic call, on a connection dedicated to the
    lock alone — never the one Alembic migrates through, so a caller already
    holding a connection or running inside its own transaction is unaffected.

    Waits by POLLING `pg_try_advisory_lock` (`MIGRATION_LOCK_POLL_INTERVAL_S`
    between tries), never the plain blocking `pg_advisory_lock` — see that
    constant's docstring for the deadlock a single long blocking call caused
    against this repo's `CREATE INDEX CONCURRENTLY` migrations. Logs once it
    starts waiting, again every `MIGRATION_LOCK_LOG_INTERVAL_S` while still
    waiting, and raises `RuntimeError` past `MIGRATION_LOCK_DEADLINE_S` rather
    than waiting forever.

    Always releases in `finally`, including when the wrapped call raises — the
    unlock itself is wrapped so that IT raising (e.g. a connection already
    broken) never replaces the original exception from the wrapped call; it is
    logged instead. `engine.dispose()` right after would eventually release
    the lock regardless (Postgres drops session-level advisory locks when the
    session ends), but that is a safety net, not the mechanism this relies on.
    """
    engine = create_engine(sync_url_for(url))
    try:
        with engine.connect() as conn:
            start = time.monotonic()
            waiting_logged = False
            last_logged = start
            while not conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            ).scalar():
                conn.commit()
                now = time.monotonic()
                elapsed = now - start
                if not waiting_logged:
                    log.warning(
                        "run_migrations: MIGRATION_LOCK_KEY held by another "
                        "process — waiting (deadline %.0fs)",
                        MIGRATION_LOCK_DEADLINE_S,
                    )
                    waiting_logged = True
                    last_logged = now
                elif now - last_logged >= MIGRATION_LOCK_LOG_INTERVAL_S:
                    log.warning(
                        "run_migrations: still waiting for MIGRATION_LOCK_KEY "
                        "(%.0fs elapsed)",
                        elapsed,
                    )
                    last_logged = now
                if elapsed >= MIGRATION_LOCK_DEADLINE_S:
                    raise RuntimeError(
                        f"timed out after {MIGRATION_LOCK_DEADLINE_S:.0f}s waiting "
                        f"for MIGRATION_LOCK_KEY — another process's migration "
                        f"appears to be stuck, not merely slow"
                    )
                time.sleep(MIGRATION_LOCK_POLL_INTERVAL_S)
            conn.commit()
            try:
                yield
            finally:
                try:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
                    )
                    conn.commit()
                except Exception:
                    log.warning(
                        "run_migrations: failed to release MIGRATION_LOCK_KEY "
                        "explicitly — closing the connection below still "
                        "releases it",
                        exc_info=True,
                    )
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
    migration. Serialized the same way as `run_migrations`, against the same lock key —
    the two must never run against one database at the same time either."""
    with _migration_lock(url):
        command.stamp(alembic_config(url), "head")
