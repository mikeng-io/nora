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

Isolation is by database, not by room. An earlier design shared one database
across every case and minted a hashed room per case (`room_for`), which then
needed a swapped policy entry, a Discord-shaped id, a per-case Honcho
workspace and a clock shift to look like the real room — and still was not
it. A database per case makes the real ids usable and the copied files
verbatim, which is the whole point of copying them.

THE COPY IS AT THE MOMENT'S OWN CLOCK. Every seeded row keeps its real `ts`,
the standing keeps its real `updated_at`, and the subject's clock is frozen
at `before_ms` (`SubjectSpec.now`) — so a row that was four minutes old at
the instant is four minutes old to the model, because the model's "now" IS
the instant. Nothing is shifted. The previous design shifted every timestamp
onto the wall clock instead, which was the faithful thing to do to the
DATABASE and the wrong thing to do to the MODEL only because the clock was not
yet a port; it is one now (`01-boundaries.md` §3).

WHAT IS STILL NOT BYTE-IDENTICAL, each a stated limitation of the extraction:

- **Sender ids are MINTED, never copied.** `Message` has no `sender_id`
  field, so `_speakers()` assigns synthetic ones and a real member's E.164
  cannot reach the sandbox through here. `person_id` IS copied, because it is
  an `identity.toml` handle rather than an account, and it is the key the
  transcript renders a canonical name from.
- **Media is NOT reconstructed.** `snapshot.py` returns `messages.body` as
  stored, which since issue #116 carries a handle (`[image a3f2c1]`) rather
  than a description; `Message.media` would synthesise a `MediaBlob` with a
  DIFFERENT digest, so the handle in the body would name a blob that is not
  the one attached.
- **A note's loop state is whatever `snapshot.py` could recover.** `NoteSeed`
  writes through `SqlNoteStore.create`, so every seeded note is freshly open.
- **Reply threading is FLATTENED.** `TranscriptRow.quoted_ts` names which
  earlier row a message replied to; `Message` has no quote field. Closing it
  needs a citation-style translation, a second piece of work.
- **Only four tiers are extracted.** Reminders, note candidates and the
  debounce/window state tables are EMPTY in the sandbox. That is the same
  emptiness the hashed-room design had, now visible in one place; extending
  `snapshot.py` is how it closes, and `11-the-sandbox-rebuild.md` §6 R1 is
  the rule that will refuse a case once the extraction can say a tier is
  missing rather than merely unread.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nora.domain.notes import Note, NoteRevision
from nora.infra.sql import create_readonly_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.snapshot import Snapshot, snapshot

from .knowledge import Knowledge, Message, NoteSeed, know
from .obtain import Subject

__all__ = ["Sandboxed", "as_knowledge", "sandbox_database", "sandbox_from_snapshot"]

_HOUR_MS = 3_600_000

#: Templates this process has already brought to the migration head, by URL.
#: `run_migrations` on an at-head database is one connection and a version
#: check, but it is also one more session on a template that
#: `CREATE DATABASE ... TEMPLATE` needs nobody else connected to.
_migrated: set[str] = set()


def _maintenance(url: str) -> str:
    """The same server's `postgres` database — where `CREATE DATABASE` runs."""
    return _sibling(url, "postgres")


def _sibling(url: str, name: str) -> str:
    # `render_as_string`, not `str()`: SQLAlchemy's `URL.__str__` masks the
    # password as `***`, and a URL rendered that way authenticates as nobody.
    return make_url(url).set(database=name).render_as_string(hide_password=False)


async def _blank_template(source_url: str) -> str:
    """A migrated, EMPTY database beside the source, made once and reused.

    `<source>_blank`, so two runs against one source share it and a run
    against a different source never does. Brought to head with the
    repo's own migration chain, which is the schema production runs — a
    sandbox at a different revision would be a different store.
    """
    name = f"{make_url(source_url).database}_blank"
    url = _sibling(source_url, name)
    if url in _migrated:
        return name
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
    # Sync Alembic on the event loop's thread: the runner opens and closes its
    # own engine, and this happens once per process per source.
    run_migrations(url)
    _migrated.add(url)
    return name


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
    test, and the reason `obtain()` costs ~64ms against one
    (`03-step0-results.md` §3). Stragglers on the template are terminated
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
        notes=tuple(_note_seed(note, revision) for note, revision in shot.notes),
    )


def _note_seed(note: Note, revision: NoteRevision) -> NoteSeed:
    """One `(Note, NoteRevision)` pair as the seed that reproduces it.

    **A note anchored only by entity ids is REFUSED here rather than
    re-anchored.** `Note.__post_init__` requires `anchor_date` or
    `anchor_entity_ids`, and `NoteSeed` carries only the first — so such a note
    would reach `SqlNoteStore.create` with neither and raise `ValueError` from
    inside the domain, naming a constructor rather than the room the
    reconstruction came from. Raising here says which note and why. It is not
    silently dropped: a note missing from the index is invisible, and invisible
    is indistinguishable from absent.
    """
    if note.anchor_date is None:
        raise ValueError(
            f"note {note.id!r} ({note.title!r}) is anchored by entity ids "
            "alone, and `NoteSeed` can only state an anchor_date — seeding it "
            "would reach `Note.__post_init__` with no anchor at all"
        )
    return NoteSeed(
        title=note.title,
        description=note.description,
        body=revision.body,
        uncertainty=note.uncertainty,
        closing_condition=note.closing_condition,
        anchor_date=note.anchor_date,
    )


@dataclass(frozen=True, slots=True)
class Sandboxed:
    """What `sandbox_from_snapshot` left behind, for the drive that follows."""

    #: The instant the room stands at — the case's `before_ms`, which is also
    #: the subject's frozen clock. The input line, if any, arrives here.
    now_ms: int
    #: Historical row id -> the id the sandbox's store assigned the copy.
    rows: dict[int | str, int]
    #: The window she was seeded with, oldest first, one body per line — the
    #: SOURCE a judge grades grounding against (`evals.backbone`).
    transcript: tuple[str, ...]


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
    return Sandboxed(
        now_ms=written.now_ms,
        rows=written.rows,
        transcript=tuple(row.body or "" for row in shot.transcript),
    )
