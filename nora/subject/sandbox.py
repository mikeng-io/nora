"""The sandbox: a database of the case's own, holding what Nora ACTUALLY knew
about a real room at a real moment, read from the deployment's clone.

`knowledge.py` writes what a caller STATES; this writes what a room REALLY
held. `nora.infra.sql.snapshot` reconstructs the second — transcript,
standing, culture, notebook, at one historical instant — and this is the one
place that turns that reconstruction into the shapes `know()` already writes.

TWO DATABASES, AND WHICH IS WHICH.

- **The source** is the deployment's clone (`nora_eval` on the deploy host:
  `scripts/refresh_eval_clone.sh`), a copy of production taken by hand and
  never written by anything here. It is read through
  `create_readonly_sql_engine`, so a write is refused by the server rather
  than promised away. Production itself is never opened: the clone is a
  frozen copy, so the moment a case names denotes the same rows next month.
- **The sandbox** is a database of this case's own, cloned from a blank
  migrated template BESIDE the source (`sandbox_database`), and it is what
  `SubjectSpec.database_url` points at. Everything the case seeds and
  everything the turn writes lands there, under the REAL room's id, the
  deployment's own `policy.toml` and `identity.toml`, and it is dropped when
  the case ends.

Isolation is by database, not by room: a database per case makes the real ids
usable and the copied files verbatim.

THE COPY IS AT THE MOMENT'S OWN CLOCK. Every seeded row keeps its real `ts`,
the standing keeps its real `updated_at`, and the subject's clock is frozen
at `before_ms` (`SubjectSpec.now`) — so a row that was four minutes old at
the instant is four minutes old to the model, because the model's "now" IS
the instant. Nothing is shifted.

WHAT IS STILL NOT BYTE-IDENTICAL, each a stated limitation of the extraction:

- **Source sender ids are never copied.** `Message` has no `sender_id` field.
  Known human speakers use an account from the subject's loaded roster, so
  production's platform-account name projection works. Unknown speakers and
  Nora's own rows retain minted ids. `person_id` is copied to identify the
  roster person; it is not itself the name projection's lookup key.
- **Media is NOT reconstructed.** `snapshot.py` returns `messages.body` as
  stored, which carries a handle (`[image a3f2c1]`) rather than a description;
  `Message.media` would synthesise a `MediaBlob` with a DIFFERENT digest, so
  the handle in the body would name a blob that is not the one attached.
- **A note's loop state is whatever `snapshot.py` could recover.** `NoteSeed`
  writes through `SqlNoteStore.create`, so every seeded note is freshly open.
- **Reply threading is FLATTENED.** `TranscriptRow.quoted_ts` names which
  earlier row a message replied to; `Message` has no quote field.
- **Only four tiers are extracted** by this path. `sandbox_as_of` restores
  every store as of T instead, row for row; the corpora
  `evals.scenarios.FULL_RESTORE_CORPORA` names are seeded that way, and
  `restored_room` hands their drive what this path's `Sandboxed` carries.
  For every other corpus, reminders, note candidates and the
  debounce/window state tables are EMPTY in the sandbox; extending
  `snapshot.py` is how that closes.

THE SOURCE AND THE TEMPLATE ARE BOTH SHARED, AND NEITHER MAY EVER BE
MIGRATED BY A RUN. `nora_eval` (the source) and `nora_eval_blank` (the
template `sandbox_database` clones every case from) are read by every session
on the host, not just this run's. `command.upgrade(cfg, "head")` has to find
the database's CURRENT revision in ITS OWN script directory to plan a path
from it, so upgrading the shared template to a branch's head would make every
other session's run (from a checkout that has never heard of that revision)
fail at seeding with `Can't locate revision '0065_…'`. `_blank_template` is
the ONLY place in this package that calls `run_migrations` on a shared, not
per-case, name, and it guards it:

- `_blank_template` upgrades the shared template ONLY the first time it
  is ever created (nothing depends on it yet) or when it is already at this
  process's own head (a no-op skipped entirely, not even attempted). Any
  other relationship — this branch ahead OR behind what is already there —
  is a DIVERGENCE, and the response is a throwaway PRIVATE copy
  (`<template>_run_<nonce>`), cloned from the shared one at its own
  untouched revision, migrated to this process's head, and dropped by
  `close_private_blank_templates` when the run ends. The shared template is
  never written to on that path.
- The source is read-only by construction
  (`create_readonly_sql_engine`/`default_transaction_read_only=on`), so it
  cannot be migrated through this module at all — but it CAN be silently
  misread: a branch whose migrations add a column `snapshot()` selects, run
  against a source that predates that migration, fails deep in a query with
  a message naming a column, not a reason. `refuse_if_source_diverges_from_head`
  refuses before any case runs, but ONLY when the source is AHEAD of this
  checkout's head (a revision `revision_is_known` does not recognize at all
  — this repo's ONE linear alembic chain makes "known at all" the same fact
  as "an ancestor of head"). A source BEHIND head runs — refusing there
  would refuse nearly every ordinary run from master, since most migrations
  add nothing `snapshot()` selects — and `nora.infra.sql.snapshot.
  snapshot` is the second, fail-closed layer: a read that actually hits a
  missing column or table there raises the SAME `SourceRevisionMismatchError`
  (defined in `nora.infra.sql.migrations_runner`, the lower layer both
  raisers share), naming both revisions.
- `shared_database_versions`/`assert_shared_databases_unchanged` are the
  independent, second check: recorded at run start and re-read at run end
  (`evals.__main__.main`), they do not trust that the two rules above held —
  they MEASURE the outcome, and ERROR loudly if either shared database's
  alembic version moved.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nora.agent.prefill import render_transcript
from nora.documents.check import check_document
from nora.domain.culture import CultureRevision
from nora.domain.documents import RevisionIn
from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.domain.notes import Note, NoteRevision
from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql import create_readonly_sql_engine, history, snapshot_at
from nora.infra.sql.migrations_runner import (
    SourceRevisionMismatchError,
    current_revision,
    head_revision,
    revision_is_known,
    run_migrations,
    sync_url_for,
)
from nora.infra.sql.schema import MESSAGES
from nora.infra.sql.snapshot import Snapshot, snapshot
from nora.infra.sql.snapshot_at import SeedReport
from nora.persons import Persons

from .knowledge import AccountSeed, Knowledge, Message, NoteSeed, ReminderSeed, know
from .memory_workspace import new_run_id
from .obtain import Subject

__all__ = [
    "Sandboxed", "SharedDatabaseMutatedError", "SharedDatabaseVersions",
    "SourceRevisionMismatchError", "as_knowledge", "assert_shared_databases_unchanged",
    "close_private_blank_templates", "judge_source", "refuse_if_source_diverges_from_head",
    "restored_room", "sandbox_as_of", "sandbox_database", "sandbox_from_snapshot", "seed_lessons",
    "shared_database_versions",
    "sweep_stale_private_blank_templates",
]

_HOUR_MS = 3_600_000

#: Templates this process has already brought to the migration head, by URL.
#: `run_migrations` on an at-head database is a brief advisory-lock round trip
#: plus a version check — still cheap, but it is also one more session (two,
#: while the lock connection is open) on a template that
#: `CREATE DATABASE ... TEMPLATE` needs nobody else connected to.
_migrated: set[str] = set()


def _maintenance(url: str) -> str:
    """The same server's `postgres` database — where `CREATE DATABASE` runs."""
    return _sibling(url, "postgres")


def _sibling(url: str, name: str) -> str:
    # `render_as_string`, not `str()`: SQLAlchemy's `URL.__str__` masks the
    # password as `***`, and a URL rendered that way authenticates as nobody.
    return make_url(url).set(database=name).render_as_string(hide_password=False)


#: Private per-run copies `_blank_template` built because the SHARED
#: `<source>_blank` diverged from this process's own migration head —
#: `source_url -> the private database's name`. One per source per process:
#: every case in this run reuses the SAME private copy (never re-created,
#: never re-migrated per case) and it is dropped once, at run end, by
#: `close_private_blank_templates`. `_migrated` gates re-entry into
#: `_blank_template` for a given URL; this is what that re-entry returns.
#:
#: `dict()`, not a `{}` literal, to match `_migrated` above: both are the
#: same shape (a process-lifetime cache, constructed once, mutated by
#: `.add`/subscript as the process runs), and
#: `tests/architecture/test_no_module_level_mutable_singletons.py` reads a
#: literal `{}`/`[]`/`{...}` that is mutated as a singleton — the DEC-13
#: shape it exists to catch — but not a call to a builtin constructor,
#: exactly as it already reads `_migrated`'s `set()` call.
_private_blanks: dict[str, str] = dict()  # noqa: C408


async def _blank_template(source_url: str) -> str:
    """A migrated database beside the source, made once and reused — OR, if
    this process's migration head differs from what the shared template
    already holds, a PRIVATE copy this run alone uses and drops when it ends.

    `<source>_blank` is SHARED: every session on the host reads and clones it
    (`sandbox_database`), so nothing here may call `run_migrations` against
    it except to ESTABLISH it the first time it is ever created — nothing
    depends on it yet at that point. Once established, it is reused AS IS
    when its revision already equals this process's own `head_revision()`
    (no upgrade call at all). Anything else — this branch ahead of what is
    there, or behind it — is a DIVERGENCE, and upgrading the SHARED database
    is the hazard this function exists to end (module docstring): every OTHER
    session's run, from an older head, would fail at seeding with
    `Can't locate revision '0065_…'`.

    So a divergence never touches the shared template. It clones a
    throwaway PRIVATE copy (`<template>_run_<nonce>`) from the shared one AT
    ITS OWN, UNMODIFIED revision, migrates THAT to this process's head, and
    tracks it in `_private_blanks` for `close_private_blank_templates` to
    drop when the run ends — the same shape `sandbox_database`'s own
    per-case drop already uses, one level up (one copy per RUN, not per
    case, because the migration chain is a cost worth paying once).
    """
    name = f"{make_url(source_url).database}_blank"
    url = _sibling(source_url, name)
    if url in _migrated:
        return _private_blanks.get(source_url, name)

    admin = create_async_engine(_maintenance(source_url), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = (await conn.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name},
            )).scalar()
            if not exists:
                await conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()

    if not exists:
        # First-ever establishment. Nothing else depends on this database
        # yet, so migrating it in place is the baseline, not a divergence.
        run_migrations(url)
        _migrated.add(url)
        return name

    # Sync Alembic on the event loop's thread, exactly as the established
    # path below does — a plain read, no lock, no upgrade attempt.
    current = current_revision(url)
    local_head = head_revision()
    if current == local_head:
        _migrated.add(url)
        return name

    # DIVERGED. Never call `run_migrations` on `url` (the shared name) from
    # here on — build and migrate a private copy instead, and leave the
    # shared template exactly as found.
    #
    # `new_run_id()` (not a bare `uuid4().hex`): the name CARRIES its own
    # creation instant (`YYYYMMDDTHHMMSS-nonce`), which
    # `sweep_stale_private_blank_templates` reads back to tell a killed
    # run's leftover from another session's live one — Postgres's
    # `pg_database` has no creation-timestamp column to read instead
    # (unlike `pg_stat_activity`, which the sweep also checks).
    private_name = f"{name}_run_{new_run_id()}"
    private_url = _sibling(source_url, private_name)
    admin = create_async_engine(_maintenance(source_url), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :t AND pid <> pg_backend_pid()"
            ), {"t": name})
            await conn.execute(sa.text(f'CREATE DATABASE "{private_name}" TEMPLATE "{name}"'))
    finally:
        await admin.dispose()
    run_migrations(private_url)
    _migrated.add(url)
    _private_blanks[source_url] = private_name
    return private_name


async def close_private_blank_templates() -> None:
    """Drop every private per-run blank template `_blank_template` built
    because the shared one diverged, and forget it — call ONCE, at run end
    (`evals.__main__.main`'s `finally`), never per case: one private copy is
    reused by every case in the run, unlike the per-case sandbox
    `sandbox_database` already drops on its own.

    Best-effort per database, the same shape `sandbox_database`'s own
    cleanup already uses: one that fails to drop must not stop the others,
    and a run's READING must never depend on its own cleanup succeeding.
    Safe to call when `_blank_template` never diverged this process — a
    no-op.
    """
    for source_url, private_name in list(_private_blanks.items()):
        admin = create_async_engine(_maintenance(source_url), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                with contextlib.suppress(Exception):
                    await conn.execute(sa.text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :t AND pid <> pg_backend_pid()"
                    ), {"t": private_name})
                    await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{private_name}"'))
        finally:
            await admin.dispose()
        del _private_blanks[source_url]
        _migrated.discard(_sibling(source_url, f"{make_url(source_url).database}_blank"))


#: How old a private blank template (`<blank>_run_<ts>-<nonce>`) must be
#: before the startup sweep will even consider it — the SAME reasoning
#: `nora.subject.memory_workspace.STALE_AFTER` uses for Honcho draw
#: workspaces: a single run's own private copy lives for the run, which is
#: minutes to a few hours; six hours is far outside any run and inside a
#: day, so another session's LIVE copy is never old enough to match.
STALE_PRIVATE_BLANK_AFTER = timedelta(hours=6)

#: `<anything>_run_YYYYMMDDTHHMMSS-hex` — `new_run_id()`'s own shape
#: (`nora.subject.memory_workspace.new_run_id`), read back by
#: `_private_blank_created_at`. Anchored at the end (`$`): `_run_` cannot
#: appear earlier by construction (`_blank_template` only ever appends this
#: suffix once), but anchoring costs nothing and refuses to guess at a name
#: shaped some other way.
_PRIVATE_BLANK_RUN = re.compile(r"_run_(\d{8}T\d{6})-[0-9a-f]+$")


def _private_blank_created_at(name: str) -> datetime | None:
    """The instant `_blank_template` created `name`, read back from the name
    itself — `None` for a name that does not carry `new_run_id`'s shape
    (never guessed at; see `sweep_stale_private_blank_templates`, which
    treats `None` as "too new to touch", the same as an age it cannot read).
    """
    match = _PRIVATE_BLANK_RUN.search(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


async def sweep_stale_private_blank_templates(
    source_url: str, *, older_than: timedelta = STALE_PRIVATE_BLANK_AFTER,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Drop private blank templates (`_blank_template`'s own,
    `<template>_run_<ts>-<nonce>`) a KILLED run left behind. Called once, at
    run start (`evals.__main__.main`, before any case — the same point
    `_sweep_stale_workspaces` already sweeps Honcho's leftovers from), so a
    run that was `kill -9`'d (bypassing `close_private_blank_templates`'s
    own `finally` entirely — see `evals.__main__._raise_on_sigterm` for the
    ordinary `SIGTERM` case, which this sweep backstops) does not leak a
    migrated database forever.

    A candidate is dropped ONLY when BOTH hold:

    - **it is older than `older_than`.** Read from the name itself
      (`_private_blank_created_at`) — Postgres's `pg_database` carries no
      creation timestamp to read instead. A name this cannot parse is
      treated as too new, never as "unknown, sweep it anyway": a name this
      sweep does not recognize is not this sweep's to guess about.
    - **it holds no connection in `pg_stat_activity` right now.** A
      currently-running session's own copy is either being created/migrated
      (briefly) or about to be cloned FROM (`sandbox_database`'s own
      `CREATE DATABASE ... TEMPLATE`, which itself needs zero connections on
      the template — Postgres's own rule) — either way, a live connection is
      the strongest signal "still in use", checked LAST and closest to the
      drop so the window between checking and dropping is as small as
      possible.

    Neither check alone is safe: age alone could catch a run that has
    genuinely not touched its template in a while but is still running
    (unlikely within `older_than`, not impossible); a connection check alone
    could pass on a genuinely dead copy that happens to be mid-clone by a
    DIFFERENT run reusing the name after a restart (impossible in practice —
    the nonce makes that a collision — but the age floor costs nothing and
    removes the need to argue it). Best-effort per database, the same shape
    `close_private_blank_templates` already uses: one that fails to drop
    must not stop the sweep from trying the rest.
    """
    cutoff = (now or datetime.now(UTC)) - older_than
    blank = f"{make_url(source_url).database}_blank"
    pattern = f"{blank}_run_%"
    admin = create_async_engine(_maintenance(source_url), isolation_level="AUTOCOMMIT")
    swept: list[str] = []
    try:
        async with admin.connect() as conn:
            names = (await conn.execute(
                sa.text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
                {"p": pattern},
            )).scalars().all()
            for name in names:
                created = _private_blank_created_at(name)
                if created is None or created >= cutoff:
                    continue
                active = (await conn.execute(sa.text(
                    "SELECT 1 FROM pg_stat_activity WHERE datname = :n "
                    "AND pid <> pg_backend_pid() LIMIT 1"
                ), {"n": name})).scalar()
                if active:
                    continue
                with contextlib.suppress(Exception):
                    await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
                    swept.append(name)
    finally:
        await admin.dispose()
    return tuple(swept)


def _exists(url: str) -> bool:
    """Whether the database `url` names exists on its server.

    Checked via the maintenance database (`postgres`), never by connecting
    to `url` itself and treating a connection failure as "not created yet" —
    that would also swallow a real connectivity error and misreport it as
    the benign case, which is the exact ERROR-read-as-PASS this module's
    guards exist to refuse.
    """
    admin = sa.create_engine(sync_url_for(_maintenance(url)))
    try:
        with admin.connect() as conn:
            return bool(conn.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": make_url(url).database},
            ).scalar())
    finally:
        admin.dispose()


@dataclass(frozen=True, slots=True)
class SharedDatabaseVersions:
    """Both shared eval databases' alembic versions at one instant — what
    `refuse_if_source_diverges_from_head` and the run-start/run-end guard
    (`assert_shared_databases_unchanged`) compare against."""

    #: `nora_eval`-shaped: the clone every session reads, never written.
    source_rev: str | None
    #: `nora_eval_blank`-shaped. `None` means the template does not exist
    #: yet — not a divergence, since nothing has been established to
    #: diverge from.
    blank_rev: str | None


def shared_database_versions(source_url: str) -> SharedDatabaseVersions:
    """A plain read of both shared databases' `alembic_version` — no lock,
    no upgrade, nothing that could itself be the mutation this exists to
    detect. `evals.__main__.main` calls this once at run start (the
    baseline) and once at run end, through `assert_shared_databases_unchanged`.
    """
    blank_url = _sibling(source_url, f"{make_url(source_url).database}_blank")
    return SharedDatabaseVersions(
        source_rev=current_revision(source_url),
        blank_rev=current_revision(blank_url) if _exists(blank_url) else None,
    )


class SharedDatabaseMutatedError(RuntimeError):
    """A run's own end-of-run guard: `shared_database_versions` read a
    DIFFERENT alembic version than it read at the start, for a database
    that already existed then. Every path that could migrate a shared name
    is refused earlier (`_blank_template`'s divergence branch never calls
    `run_migrations` on the shared url; the source is read-only by
    construction) — this does not trust that those held. It MEASURES the
    outcome, independent of which code path ran, and it is never a PASS."""


def assert_shared_databases_unchanged(
    source_url: str, before: SharedDatabaseVersions,
) -> None:
    """ERROR, loudly, if either shared database changed alembic version
    since `before` was read.

    The template's `blank_rev` moving from `None` to a real value is the one
    allowed transition: that is `_blank_template` establishing the baseline
    the FIRST time it is ever created, not a mutation of something another
    session already depended on. Every other change — the source moving at
    all, or an already-established template moving — is the hazard this
    module exists to end.
    """
    after = shared_database_versions(source_url)
    problems: list[str] = []
    if after.source_rev != before.source_rev:
        problems.append(
            f"source moved from {before.source_rev!r} to {after.source_rev!r}"
        )
    if before.blank_rev is not None and after.blank_rev != before.blank_rev:
        problems.append(
            f"template moved from {before.blank_rev!r} to {after.blank_rev!r}"
        )
    if problems:
        raise SharedDatabaseMutatedError(
            "this run changed a database every eval session on the host "
            "shares, which must never happen: " + "; ".join(problems)
        )


#: `SourceRevisionMismatchError` is defined in `nora.infra.sql.migrations_
#: runner` (imported above), not here: `nora.infra.sql.snapshot.snapshot`
#: raises the SAME type on its own fail-closed catch (a missing column/table
#: mid-read), and `nora.infra.sql` is a LOWER layer than `nora.subject` — the
#: error has to live at or below the layer that first needs to raise it.


def refuse_if_source_diverges_from_head(source_url: str) -> None:
    """Refuse the run ONLY if the source clone is AHEAD of this checkout's
    own migration head — a revision `revision_is_known` does not recognize
    at all.

    The source is read-only (`create_readonly_sql_engine`; Postgres refuses
    any write at the session level), so this is about a MISREAD, not a
    write: a query naming a column this branch's migration added, run
    against a source that predates it, fails deep inside `snapshot()` with a
    message that names a column, not a reason.

    **A source BEHIND head is allowed to run.** This repo keeps ONE linear
    alembic chain, so a revision this checkout recognizes at all —
    `revision_is_known` — is by construction an ANCESTOR of head: the
    source is behind or current, never on a separate branch. Refusing on
    every routine deploy lag would refuse nearly every ordinary run from
    master — most migrations (an index-only one, for instance) add nothing
    `snapshot()`'s five reads name.
    What a behind source cannot promise is that EVERY later migration was
    that harmless, so `snapshot()` itself is the fail-closed second layer:
    a read that actually hits a missing column or table there raises this
    SAME error type, naming both revisions, instead of a bare "column does
    not exist" deep inside a case.

    A source with NO revision at all (`current_revision` reads `None` — an
    unmigrated or unreadable database) is refused unconditionally: there is
    nothing to call "behind" about a database with no history.
    """
    source_rev = current_revision(source_url)
    local_head = head_revision()
    if source_rev == local_head:
        return
    if source_rev is not None and revision_is_known(source_rev):
        return  # BEHIND head — allowed; `snapshot()` covers the rest
    raise SourceRevisionMismatchError(
        f"source at {source_rev!r}, branch at {local_head!r}: the source is "
        "ahead of this checkout's migration head (or carries no revision "
        "this checkout recognizes at all). Refresh the source (`make "
        "eval-source`) or run on a branch at the source's head. The source "
        "is read-only; this run will not migrate it either way."
    )


@contextlib.asynccontextmanager
async def sandbox_database(
    source_url: str, *, template: str | None = None,
) -> AsyncGenerator[str]:
    """A fresh migrated database beside `source_url`, dropped on exit.

    `template` names an already-migrated blank database on the same server
    to clone from — the test suite's session-scoped `pg_template` — so a test
    does not pay the migration chain per case. `None` builds and reuses
    `<source>_blank` (`_blank_template`), which is what a run on the deploy
    host does.

    `CREATE DATABASE ... TEMPLATE <blank>` is a file-level clone inside
    Postgres — the mechanism `tests/conftest.py`'s `pg_url` already uses per
    test, and the reason `obtain()` is cheap against one. Stragglers on the template are terminated
    first, because Postgres refuses to copy a database with any other
    session connected and a pooled handle is exactly what lingers.

    The name carries the source's, so a `\\l` on the server says what a
    leftover was for; dropping is best-effort, because a case's RESULT must
    not depend on whether its cleanup worked.
    """
    if template is None:
        template = await _blank_template(source_url)
    name = f"{make_url(source_url).database}_sb_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(_maintenance(source_url), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :t AND pid <> pg_backend_pid()"
            ), {"t": template})
            await conn.execute(sa.text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
        try:
            yield _sibling(source_url, name)
        finally:
            with contextlib.suppress(Exception):
                async with admin.connect() as conn:
                    await conn.execute(sa.text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :t AND pid <> pg_backend_pid()"
                    ), {"t": name})
                    await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        await admin.dispose()


@contextlib.asynccontextmanager
async def sandbox_as_of(
    source_url: str, at: datetime, *, template: str | None = None,
) -> AsyncGenerator[tuple[str, SeedReport]]:
    """A fresh sandbox (`sandbox_database`) holding EVERY restorable store as
    it stood at `at` in the source — `nora snapshot --at T`'s seeding, into
    the per-case database the harness clones from the blank template.

    Unlike `sandbox_from_snapshot`, which reconstructs one room's four tiers
    and rewrites them through the stores, this copies every row with its own
    ids, sender ids included, so a subject composed against the result reads
    what Nora read then. Seeded BEFORE any subject is composed against it, so
    the target is empty (`snapshot_at.seed` refuses one that is not).
    Memory, the Rollup before migration 0071 and events before 0074 are not
    restored; `snapshot_at.EXCEPTED` says why. Yields the sandbox's URL and what was copied.
    """
    async with sandbox_database(source_url, template=template) as url:
        source = create_readonly_sql_engine(source_url)
        target = create_async_engine(url)
        try:
            report = await snapshot_at.seed(source, target, at)
        finally:
            await source.dispose()
            await target.dispose()
        yield url, report


def _collapse_duplicate_proactive_notes(
    reminders: Sequence[Reminder],
) -> tuple[Reminder, ...]:
    """At most ONE open proactive reminder per note — reproducing what
    production would hold AFTER migration 0064's own collapse
    (`nora/infra/sql/migrations/versions/0064_proactive_open_note.py`).

    Production HISTORY holds instants with TWO open proactive rows on one
    note. `Snapshot.reminders` reconstructs the room AS IT WAS, so it still
    carries both, and seeding both into the sandbox's own store — which
    enforces the same `uq_reminders_proactive_open_note` production does —
    raises
    `DuplicateProactiveNoteError` on the second one. This collapses BEFORE
    seeding, using 0064's OWN rule (its `_RESOLVE_DUPLICATES` — pending
    before firing, then earliest `due_at`, then `created_at`, then `id`), so
    the sandbox reproduces what production's own migration would have left
    standing rather than an invented resolution.

    Every `Snapshot.reminders` row is already normalized to `PENDING`
    as-of the instant (`nora.infra.sql.snapshot._as_of_reminder`), so the
    pending-before-firing key is always equal here — kept for fidelity to
    0064's own rule rather than assumed away.

    Non-proactive rows, and proactive rows naming NO note, are UNTOUCHED:
    the constraint (and 0064's own collapse) only ever applies to
    `kind = 'proactive' AND payload ->> 'note_id' IS NOT NULL`. Order is
    preserved — the caller's own (`_reminders`' `created_at, id` — this
    never reorders, only drops).
    """
    def note_id(reminder: Reminder) -> str | None:
        if reminder.kind != "proactive":
            return None
        payload = reminder.payload or {}
        note_id_value = payload.get("note_id") if isinstance(payload, dict) else None
        return str(note_id_value) if note_id_value else None

    grouped: dict[str, list[Reminder]] = {}
    for reminder in reminders:
        nid = note_id(reminder)
        if nid is not None:
            grouped.setdefault(nid, []).append(reminder)

    def sort_key(reminder: Reminder) -> tuple[bool, datetime, datetime, object]:
        return (
            reminder.state == ReminderState.FIRING, reminder.due_at,
            reminder.created_at, reminder.id,
        )

    winners = {nid: min(rows, key=sort_key) for nid, rows in grouped.items()}
    return tuple(
        reminder for reminder in reminders
        if note_id(reminder) is None or reminder is winners[note_id(reminder)]  # type: ignore[index]
    )


def as_knowledge(shot: Snapshot) -> Knowledge:
    """A reconstruction, as the `Knowledge` that reproduces it AT ITS OWN
    CLOCK.

    PURE, and separate from the call that writes it, so the one property
    worth testing — that nothing moves — is testable without a database.
    Every `at_ms` is the row's real `ts`; the standing's age is how stale it
    was at `shot.before_ms`, which is what `know()` turns back into the real
    `updated_at` when it is handed `now_ms=shot.before_ms`.
    """
    return Knowledge(
        transcript=tuple(
            Message(
                # `messages.body` is nullable; `NormalizedMessage.body` is not.
                # An empty body is a row the renderer drops, which is what a
                # bodiless row already does in production.
                body=row.body or "",
                name=row.sender_name,
                # THE REAL `person_id`, including its `None`. `Message.person`
                # spells the two apart: `""` means "the default for this name"
                # and `None` means the identity does not know this sender —
                # which is what a null `person_id` on a real row means too.
                person=row.person_id,
                # The ORIGINAL row id, so `Written.rows` comes back as a map
                # from the historical row to the copy. A case that cites "the
                # message that started it" names the real id and gets the
                # sandbox's.
                label=row.id,
                at_ms=row.ts,
                is_nora=row.is_nora,
            )
            for row in shot.transcript
        ),
        standing=None if shot.standing is None else shot.standing.body,
        standing_age_hours=(
            None if shot.standing is None
            else (shot.before_ms - shot.standing.updated_at) / _HOUR_MS
        ),
        culture=None if shot.culture is None else shot.culture.body,
        # THE CHARACTER BLOCKS: the unit's account and each listed person's,
        # each with the age it had, so `<unit_culture>` and `<character>`
        # render as the recorded turn rendered them.
        unit_culture=(
            None if shot.unit_culture is None
            else _account_seed(shot.unit_culture, before_ms=shot.before_ms)
        ),
        nora_account=(
            None if shot.nora_account is None
            else _account_seed(shot.nora_account, before_ms=shot.before_ms)
        ),
        characters={
            person_id: _account_seed(account, before_ms=shot.before_ms)
            for person_id, account in shot.characters
        },
        # `<lessons>` as the unit held them at the instant (L5).
        lessons=shot.lessons,
        notes=tuple(_note_seed(note, revision) for note, revision in shot.notes),
        # OFFSETS measured against the instant itself, which is what keeps
        # the copy at the moment's own clock: `know()` is handed
        # `now_ms=shot.before_ms`, so `base + due_in_seconds` reproduces the
        # real `due_at` exactly, and an overdue row stays overdue by the
        # number of seconds it really was. COLLAPSED to one open proactive
        # row per note FIRST (`_collapse_duplicate_proactive_notes`):
        # production history can hold two, and seeding both would hit
        # `uq_reminders_proactive_open_note` on the second.
        reminders=tuple(
            _reminder_seed(r, before_ms=shot.before_ms)
            for r in _collapse_duplicate_proactive_notes(shot.reminders)
        ),
    )


def _account_seed(account: CultureRevision, *, before_ms: int) -> AccountSeed:
    """One account as the seed that reproduces it, at the age it had."""
    return AccountSeed(
        body=account.body, age_hours=(before_ms - account.written_at) / _HOUR_MS,
    )


def _reminder_seed(reminder: Reminder, *, before_ms: int) -> ReminderSeed:
    """One outstanding reminder as the seed that reproduces it.

    `state`, `fired_at` and `outcome` are not carried because `_reminders`
    already established them: everything in `Snapshot.reminders` was
    outstanding at the instant, so all three have one value and `ReminderSeed`
    has no field to get them wrong in.

    The requester IS copied. It is an `identity.toml` handle rather than an
    account — the same reason `person_id` is copied on a transcript row — and
    it is the key the proactive path reads to decide whose request this was.
    """
    instant = datetime.fromtimestamp(before_ms / 1000, tz=UTC)
    return ReminderSeed(
        text=reminder.text,
        due_in_seconds=(reminder.due_at - instant).total_seconds(),
        created_seconds_ago=(instant - reminder.created_at).total_seconds(),
        requester_person_id=reminder.requester_person_id,
        kind=reminder.kind,
        payload=reminder.payload,
    )


def _note_seed(note: Note, revision: NoteRevision) -> NoteSeed:
    """One `(Note, NoteRevision)` pair as the seed that reproduces it.

    **The anchor goes across whole**: `anchor_date` and `anchor_entity_ids`
    both, so a note anchored by entity ids alone seeds like any other instead
    of being refused.

    **`id=note.id` — the SOURCE's own id, not left for `know()` to assign.**
    A reminder seeded alongside keeps this SAME id in
    its payload's `note_id` (`_reminder_seed` copies `payload` verbatim), and
    letting `know()` assign the sandbox note a fresh one (`NoteSeed.id`'s own
    docstring) would leave nothing in the sandbox agreeing on what a note's id
    even was.
    """
    return NoteSeed(
        id=note.id,
        title=note.title,
        description=note.description,
        body=revision.body,
        uncertainty=note.uncertainty,
        closing_condition=note.closing_condition,
        anchor_date=note.anchor_date,
        anchor_entity_ids=tuple(note.anchor_entity_ids),
    )


@dataclass(frozen=True, slots=True)
class Sandboxed:
    """What `sandbox_from_snapshot` left behind, for the drive that follows."""

    #: The instant the room stands at — the case's `before_ms`, which is also
    #: the subject's frozen clock. The input line, if any, arrives here.
    now_ms: int
    #: Historical row id -> the id the sandbox's store assigned the copy.
    rows: dict[int | str, int]
    #: The window she was seeded with, oldest first, ONE RENDERED MESSAGE PER
    #: ITEM — the SOURCE a judge grades grounding against (`evals.backbone`).
    #: Each item is the `<m>` element she was shown for that row, speaker and
    #: all (`judge_source`), so `"\n".join(transcript)` is her
    #: `<group_transcript>` body byte for byte; one BODY per line would hand a
    #: judge told to fail any unlinked attribution a source that links nobody to
    #: anything.
    transcript: tuple[str, ...]


#: Where one `<m>` element ends and the next begins in `render_transcript`'s
#: output. Only there: a body is `escape`d, so a `<` inside one is `&lt;` and a
#: body's own newlines can never be followed by `<m `.
_NEXT_MESSAGE = re.compile(r"\n(?=<m )")


def judge_source(
    rows: Sequence[TranscriptRow],
    *,
    persons: Persons,
    platform: str,
    display_name: str,
    now: datetime,
) -> tuple[str, ...]:
    """The window as SHE was shown it, one `<m>` element per row — what a
    judge verifies «X said Y» against.

    PRODUCTION'S OWN NAMING AND RENDERER, not a format of the eval layer's:
    `Persons.name_transcript` is the projection both context assemblers run
    (DEC-80), and `render_transcript` is the body of `<group_transcript>`. So
    a member is called what the prompt calls them, her own rows are under her
    configured `display_name` — the flag decides, never a name — and a sender
    the identity does not know falls back to the self-set profile name and
    then to «unknown». No account id is ever rendered: the judge is a
    third-party model call and R-18 applies to it as to her.

    `rows` are the SANDBOX's rows, read back after seeding — not the source's.
    The sandbox uses its loaded roster for known humans (see the module
    docstring); projecting the source's rows instead could still name somebody
    the model was never shown.

    Fails closed on a split that does not come back one element per row,
    rather than handing the judge a source whose lines have shifted speakers.
    """
    named = persons.name_transcript(rows, platform=platform)
    rendered = render_transcript(named, display_name=display_name, now=now)
    elements = tuple(_NEXT_MESSAGE.split(rendered)) if rendered else ()
    if len(elements) != len(rows):
        raise ValueError(
            f"the rendered window split into {len(elements)} messages for "
            f"{len(rows)} rows; a judge's source must be one element per row"
        )
    return elements


async def sandbox_from_snapshot(
    subject: Subject,
    *,
    source_url: str,
    before_ms: int,
    leave_room_for_arrival: bool = True,
) -> Sandboxed:
    """Seed the subject's sandbox with what Nora knew about ITS OWN room —
    the real one it was composed for — at `before_ms`, read from the source.

    The room, its culture key and its unit are the SUBJECT'S: `obtain()`
    resolved them from the deployment's own policy entry, so there is nothing
    for a case to state and nothing for it to get wrong. `snapshot()` still
    refuses to derive the two keys from the database, and it still does not
    have to — they come from the policy, which is where production reads
    them.

    Refusals, each an ERROR rather than an empty room:

    - `source_url` naming the subject's own database. Reading the room out
      of the store it is about to be written into would seed the copy on top
      of itself.
    - a subject whose clock is not frozen at `before_ms`. Ages are what
      reach the model, and a window seeded at the moment's clock but read at
      another is a room that never existed.
    - a reconstruction with an EMPTY transcript. A room that had said
      nothing by `before_ms` is not a moment a case can be about, and it is
      also exactly what a mistyped id or a moment before the room's first
      row reads as.

    `leave_room_for_arrival` is `know()`'s own, forwarded: with every row
    carrying its real `ts` the reserved step is unused, but the flag still
    documents whether a line follows the window.
    """
    if make_url(source_url).database == make_url(subject.database_url).database:
        raise ValueError(
            "source_url names the subject's own database "
            f"({make_url(source_url).database!r}); the sandbox is seeded FROM "
            "the deployment's clone INTO a database of its own, never from "
            "itself"
        )
    clock_ms = int(subject.deps.clock().timestamp() * 1000)
    if clock_ms != before_ms:
        raise ValueError(
            f"the subject's clock reads {clock_ms} and the moment is "
            f"{before_ms}; compose it with SubjectSpec.now at the moment, or "
            "every age the model sees is measured against the wrong instant"
        )
    platform, conversation_id = subject.room
    engine = create_readonly_sql_engine(source_url)
    try:
        shot = await snapshot(
            engine,
            platform=platform,
            conversation_id=conversation_id,
            culture_key=subject.culture_key,
            unit=subject.unit,
            before_ms=before_ms,
            # The persons the room LISTS, which is exactly whose accounts
            # `<character>` renders in it (`Persons.room_members`).
            person_ids=[
                ref.person_id for ref in
                subject.deps.persons.room_members(subject.listed_persons)
            ],
        )
    finally:
        await engine.dispose()
    if not shot.transcript:
        raise ValueError(
            f"the source holds no row for {platform}/{conversation_id} at or "
            f"before {before_ms}; a moment before the room's first line is "
            "not a moment, and an unknown id reads exactly the same"
        )
    written = await know(
        subject, as_knowledge(shot), now_ms=before_ms,
        leave_room_for_arrival=leave_room_for_arrival,
    )
    # READ BACK through the store her turn reads, so the source is the rows
    # she is shown rather than the rows they were copied from — and JOINED as
    # every transcript builder joins them: the judge is a model too, and
    # a bare `[image a3f2c1]` where she read what the photo showed would grade
    # her against less than she had. Its own `path=` label, for
    # `nora/subject/dreaming.py`'s reason.
    join: MediaJoin = subject.deps.media_join
    shown = await join.enrich(
        await subject.deps.audit.recent(
            conversation_id=conversation_id, limit=len(shot.transcript),
        ),
        path="judge_source",
    )
    return Sandboxed(
        now_ms=written.now_ms,
        rows=written.rows,
        transcript=judge_source(
            shown,
            persons=subject.deps.persons,
            platform=platform,
            display_name=subject.cfg.doctrine.display_name,
            now=subject.deps.clock(),
        ),
    )


async def restored_room(
    subject: Subject,
    *,
    source_url: str,
    before_ms: int,
) -> Sandboxed:
    """What the drive needs from a sandbox `sandbox_as_of` already filled.

    The FULL-RESTORE sibling of `sandbox_from_snapshot`: that one writes one
    room's four tiers through the stores and returns the row map it built;
    this one writes nothing, because `snapshot_at.seed` copied every
    restorable store as of the instant, row for row WITH ITS OWN IDS, before
    the subject was composed. So the row map is the identity, and that is
    VERIFIED here rather than assumed: the room's message ids in the sandbox
    must be exactly the ids the source's own as-of read
    (`nora.infra.sql.history.as_of`) holds for that room at the instant. A
    restore that renumbered, dropped or added a row refuses the case.

    Refusals, each an ERROR rather than an empty room, the same three
    `sandbox_from_snapshot` makes: a source that names the subject's own
    database, a subject whose clock is not frozen at `before_ms`, and a room
    that held no row at the instant.

    `transcript` is built exactly as `sandbox_from_snapshot` builds it: the
    room's rows read back through the store her turn reads, joined through the
    media join (`path="judge_source"`), named and rendered by
    `judge_source`. Here the join has the restored `media_json` and
    `media_objects` to work with, so an image row carries its stored reading
    instead of a bare handle.
    """
    if make_url(source_url).database == make_url(subject.database_url).database:
        raise ValueError(
            "source_url names the subject's own database "
            f"({make_url(source_url).database!r}); a full restore is read FROM the "
            "deployment's clone INTO a database of its own, never from itself"
        )
    clock_ms = int(subject.deps.clock().timestamp() * 1000)
    if clock_ms != before_ms:
        raise ValueError(
            f"the subject's clock reads {clock_ms} and the moment is "
            f"{before_ms}; compose it with SubjectSpec.now at the moment, or "
            "every age the model sees is measured against the wrong instant"
        )
    platform, conversation_id = subject.room
    at = datetime.fromtimestamp(before_ms / 1000, tz=UTC)

    def in_room(image: dict[str, Any]) -> bool:
        return (
            image.get("platform") == platform
            and image.get("conversation_id") == conversation_id
        )

    source = create_readonly_sql_engine(source_url)
    try:
        held: set[int] = set()
        async with source.connect() as conn:
            async for batch in history.as_of(conn, "messages", at):
                held |= {int(image["id"]) for image in batch if in_room(image)}
    finally:
        await source.dispose()
    target = create_async_engine(subject.database_url)
    try:
        async with target.connect() as conn:
            restored = {int(i) for i in (await conn.execute(
                sa.select(MESSAGES.c.id).where(
                    MESSAGES.c.platform == platform,
                    MESSAGES.c.conversation_id == conversation_id,
                )
            )).scalars()}
    finally:
        await target.dispose()
    if not held:
        raise ValueError(
            f"the source held no row for {platform}/{conversation_id} at "
            f"{at.isoformat()}; a moment before the room's first line is not a "
            "moment, and an unknown id reads exactly the same"
        )
    if restored != held:
        raise ValueError(
            f"the restore holds {len(restored)} rows for the room and the source "
            f"held {len(held)} at the instant ({len(restored - held)} extra, "
            f"{len(held - restored)} missing); a full restore copies ids "
            "verbatim, so the row map is the identity only when the two agree"
        )
    join: MediaJoin = subject.deps.media_join
    shown = await join.enrich(
        await subject.deps.audit.recent(conversation_id=conversation_id, limit=len(held)),
        path="judge_source",
    )
    return Sandboxed(
        now_ms=before_ms,
        rows={i: i for i in sorted(held)},
        transcript=judge_source(
            shown,
            persons=subject.deps.persons,
            platform=platform,
            display_name=subject.cfg.doctrine.display_name,
            now=subject.deps.clock(),
        ),
    )


async def seed_lessons(
    subject: Subject, case_name: str, lessons: Sequence[str], *, now_ms: int,
) -> None:
    """Write the lessons a `lessons` counterfactual attaches into the subject's
    unit, active, oldest first and a millisecond
    apart so `<lessons>` renders them in the order stated. Written straight to
    the store: the case states the lesson as it was held, so the doctrine check
    a session's write passes is not re-run here."""
    if not lessons:
        return
    from nora.infra.sql import create_sql_engine
    from nora.infra.sql.lessons import SqlLessonStore

    engine = create_sql_engine(subject.database_url)
    try:
        store = SqlLessonStore(engine)
        held = [x for x in await store.lessons(unit=subject.unit) if x.active]
        if held:
            # The snapshot already copied lessons the unit held at the instant
            # (`Snapshot.lessons`): seeding on top would render both, and the
            # case would be measuring a unit nobody had.
            raise ValueError(
                f"case {case_name!r} attaches lessons at an instant the unit already "
                f"held {len(held)}; anchor it before the unit's first lesson"
            )
        # At the moment's own clock (`now_ms` is the sandbox's frozen instant).
        base = now_ms - len(lessons)
        for i, body in enumerate(lessons):
            await store.create(
                unit=subject.unit, body=body.strip(), citations=(f"case:{case_name}",),
                written_at=base + i, established_by="eval-counterfactual",
            )
    finally:
        await engine.dispose()


async def seed_documents(subject: Subject, case_name: str, specs: Sequence[dict[str, Any]]) -> None:
    """Put the documents a case names into the subject's own room.

    The production source holds none, so a case about revising a document has to make
    the document it revises. Each spec is `{id, title, body}`: a one-paragraph Page,
    checked by the same `check_document` a publish goes through and stored as revision 1
    in the subject's room, where `<documents>` (and `document_read`) find it. It REFUSES
    a subject whose config has `[documents]` off: the block and both tools would be
    absent, and the case would read as a model failure.
    """
    if not specs:
        return
    if not subject.cfg.documents.enabled:
        raise ValueError(
            f"case {case_name!r} seeds documents, but the subject's config has "
            "[documents] off, so neither the block nor the tools exist; run it on a "
            "subject whose config has them on (`--deployment-config`)"
        )
    platform, conversation_id = subject.room
    for spec in specs:
        page = {
            "version": 1,
            "root": {
                "type": "Page",
                "props": {"title": spec["title"]},
                "children": [{"type": "Markdown", "props": {"text": spec["body"]}}],
            },
        }
        checked = check_document(page)
        if not checked.ok or checked.document is None:
            raise ValueError(f"case {case_name!r}: document {spec['title']!r} does not check")
        await subject.deps.documents.create(
            document_id=uuid.UUID(spec["id"]),
            platform=platform,
            conversation_id=conversation_id,
            created_by="eval",
            revision=RevisionIn(
                title=checked.title, spec=checked.document, text=checked.text,
                sha256=checked.sha256, media=(), written_by="eval",
            ),
        )
