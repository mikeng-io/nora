"""What Nora ACTUALLY knew about a real room, copied into an isolated subject.

`knowledge.py` writes what a case STATES; this writes what a room REALLY held.
`nora.infra.sql.snapshot` reconstructs the second — transcript, standing,
culture, notebook, at one historical instant — and this is the one place that
turns that reconstruction into the shapes `know()` already writes.

WHY THIS IS A SEPARATE MODULE. It is the only file in this package that READS
REAL PRODUCTION ROWS, and that is worth being able to grep for. `knowledge.py`
never names a room it did not get from the subject; every `(platform,
conversation_id)` it touches is one `obtain()` already isolated. This one takes
a real room as an argument, so the isolation argument has to be made HERE, once,
rather than inferred from a call site:

- the READ is the only thing that touches real data, and it cannot write —
  `snapshot.py` carries no `INSERT`, `UPDATE`, `DELETE` or `begin()`, which its
  own docstring states as a structural property rather than a promise;
- the WRITE goes into `subject.room` and `subject.cfg.memory.workspace_id`,
  which `room_for()`/`workspace_for()` made this case's own. There is no
  parameter for a write target, deliberately: the eval that runs afterwards
  drives an entry point that may write a note, a reminder or a reply, and every
  one of those must land in the copy.

That is the operator's Option A, and the alternative was named and rejected:
driving the real room directly would measure the real room's future.

THE COPY IS NOT BYTE-IDENTICAL, and each difference below is a decision.

- **The clock is SHIFTED, not copied.** Every timestamp moves by
  `now_ms - before_ms`, so a row that was four minutes old at the instant is
  four minutes old in the sandbox. Copying `ts` verbatim would be the faithful
  thing to do to the DATABASE and the wrong thing to do to the MODEL: ages are
  what reach it (`nora.agent.prefill._age`), so a snapshot of last July would
  render as a room that has said nothing for two months — a different input, and
  one that never existed. `know()`'s own docstring says the same about frozen
  historical constants.
- **Sender ids are MINTED, never copied.** `Message` has no `sender_id` field,
  so `_speakers()` assigns synthetic ones and a real member's E.164 cannot
  reach an eval workspace through here. `person_id` IS copied, because it is an
  `identity.toml` handle rather than an account, and it is the key the
  transcript renders a canonical name from.
- **Media is NOT reconstructed.** `snapshot.py` returns `messages.body` as
  stored, which since issue #116 carries a handle (`[image a3f2c1]`) rather
  than a description; `Message.media` would synthesise a `MediaBlob` with a
  DIFFERENT digest, so the handle in the body would name a blob that is not the
  one attached. A missing join is a stated limitation of the snapshot already;
  a mismatched one would be a new defect invented here.
- **A note's loop state is whatever `snapshot.py` could recover.** `NoteSeed`
  writes through `SqlNoteStore.create`, so every seeded note is freshly open:
  no strikes, no revisit, head pointing at its own first revision. The
  snapshot's `(Note, NoteRevision)` pair is read for the six columns that
  decided what the notebook SHOWED — title, description, uncertainty, closing
  condition, anchor, and the as-of body — which are exactly the ones
  `snapshot.py` says are written once and never updated.
- **Reply threading is FLATTENED, found by review rather than decided up
  front.** `snapshot.py`'s `TranscriptRow.quoted_ts` names which earlier row a
  message replied to; `Message` has no quote field, so `as_knowledge` drops it
  silently. `nora.agent.prefill` resolves `quoted_ts` into the transcript's
  `↳` reply lines, so a real threaded room copies into the sandbox with every
  reply edge gone — the model is shown a room that never talked the way it
  did. Left open rather than fixed here: closing it needs a citation-style
  translation (the historical row id the quote names is not the id the
  sandbox's own store assigns it), which is a second piece of work, not a
  field.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.notes import Note, NoteRevision
from nora.infra.sql import create_sql_engine
from nora.infra.sql.snapshot import Snapshot, snapshot

from .knowledge import Knowledge, Message, NoteSeed, Written, know
from .obtain import Subject

__all__ = ["as_knowledge", "sandbox_from_snapshot"]

_HOUR_MS = 3_600_000


def as_knowledge(shot: Snapshot, *, now_ms: int) -> Knowledge:
    """A reconstruction, as the `Knowledge` that reproduces it at `now_ms`.

    PURE, and separate from the call that writes it, because the one property
    worth testing here is arithmetic: an instant maps to an instant, and a
    staleness maps to a staleness. A translation that only existed inside a
    database round trip could only be checked by reading rows back, which is
    the slowest possible way to ask whether a subtraction is the right way
    round.

    `shot.before_ms` maps to `now_ms` exactly. So a row that landed AT the
    instant lands at `now_ms` — the newest line had just been said, which is
    what the room looked like. A line delivered afterwards at the same
    millisecond still sorts above it, because `RecordStore.recent` breaks a
    `ts` tie on `id` and the arriving row's is larger.
    """
    shift = now_ms - shot.before_ms
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
                # from the historical row to the copy. A case that wants to
                # cite "the message that started it" names the real id and gets
                # the sandbox's.
                label=row.id,
                at_ms=row.ts + shift,
                is_nora=row.is_nora,
            )
            for row in shot.transcript
        ),
        standing=None if shot.standing is None else shot.standing.body,
        # HOW STALE IT WAS THEN, not how stale it is now. The prompt renders
        # `written=` beside the position precisely so the model can tell a
        # fresh read of the room from a weeks-old one, and measuring that
        # against today's clock would report the age of the SNAPSHOT.
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
    is indistinguishable from absent, which is the failure the note tier's own
    `standing()` refuses to have a `limit` for.
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


async def sandbox_from_snapshot(
    subject: Subject,
    *,
    platform: str,
    conversation_id: str,
    before_ms: int,
    culture_key: str,
    unit: str,
    transcript_limit: int | None = None,
    now_ms: int | None = None,
    leave_room_for_arrival: bool = True,
) -> Written:
    """Seed an isolated eval subject with what Nora actually knew about a REAL
    room at a REAL historical moment, read once and copied.

    The subject driven afterward is the synthetic one `obtain()` already built
    — never the real room this data came from. Nothing here takes a write
    target; see the module docstring.

    **ONE DATABASE, ONE ENGINE, and that is a finding rather than a shortcut.**
    A subject's `database_url` is not a database of its own: `evals/__main__.py`
    takes a single `--database-url` and `evals/rebuilt/runner.py` hands the same
    string to every case, which is exactly why `room_for()` and
    `workspace_for()` exist — cases are isolated by ROOM and WORKSPACE naming,
    not by a database each. So the real historical room and the sandbox live in
    the same physical Postgres whenever this is pointed at real history, and one
    engine reads and writes both. A second engine would be two pools over one
    database. (The engine is still this call's OWN, disposed before `know()`
    opens its own: the composition root keeps its engine private and
    `Dependencies` does not name it, which is the same reason `know()` builds
    one.)

    `culture_key` and `unit` are REQUIRED and passed straight through, because
    `snapshot()` refuses to derive them and this is not the layer that has
    earned the right to. They come from the REAL room's `policy.toml`; the keys
    the copy is WRITTEN under are the subject's own and are not settable.

    `leave_room_for_arrival` is `know()`'s own, forwarded rather than
    defaulted, because it decides whether the copied window ends one step
    BEFORE `now_ms` or AT it — and that is per ENTRY POINT, not per seeding
    style. `evals.backbone._drive` passes `port.delivers_input`: `False` for
    the gate, whose window IS the input and whose newest line must therefore
    land at `now_ms` rather than a step before it. Defaulting it here would
    give a snapshot-backed gate case a different window than the same case
    hand-authored — the one divergence between the two seeding paths that
    would not be visible in either file.

    Returns what `know()` returned, so `Written.rows` maps each historical row
    id onto the id the sandbox's store assigned it.
    """
    # Keyed on `conversation_id` ALONE, not the `(platform, conversation_id)`
    # tuple `subject.room` is. Found live, second pass: `SqlRecordStore`'s
    # reads (`recent`/`since`/`since_including_nora`) filter on
    # `conversation_id` only — `TranscriptRow` carries no platform column at
    # all — so a subject on a DIFFERENT platform sharing the real room's
    # conversation_id passed the ORIGINAL tuple check untouched and its seeded
    # rows (clock-shifted to `now_ms`, so sorted NEWEST) came back out of the
    # real room's own `recent()`. The tuple check was too narrow, not wrong in
    # direction: every subject this refuses, the tuple check already refused;
    # this refuses strictly more.
    if conversation_id == subject.room[1]:
        raise ValueError(
            f"conversation_id {conversation_id!r} equals the subject's own "
            "room's conversation_id, which is what the transcript reads AND "
            "writes on (platform is not part of that key — `TranscriptRow` "
            "carries none). Standing IS tuple-keyed and would not itself "
            "collide on conversation_id alone, but this refusal covers it "
            "too, a fortiori, rather than drawing a line the transcript "
            "risk does not need drawn — seeding would read back through "
            "the real room's own `recent()` — pass the REAL room to read, "
            "and let `obtain()` own the room that is written"
        )
    # The check above guards ONE of the three keys `know()` writes under
    # (`subject.room[1]`, for the transcript and standing — see above for why
    # platform is not part of it). Culture keys on `conversation_id` ALONE
    # too, but via a DIFFERENT identifier — `culture_key`, not `subject.room`
    # — because `snapshot()`'s own `culture_key` is not required to equal the
    # room's `conversation_id` (a real room's culture can be keyed on a
    # different `primary_id` than the room being read). Notes key on
    # `subject.cfg.memory.workspace_id`, which is not `subject.room` at all.
    # Found live: a cross-platform pair sharing a conversation_id appended a
    # `case-seed` revision onto the REAL room's culture chain, moving its
    # head; a subject whose workspace_id was left at its config default
    # collided with a real unit's notebook with no argument having to be
    # contrived at all. Both are checked here, not folded into the check
    # above, because they are keyed on different VALUES than the room is,
    # not just a narrower shape of the same one.
    if culture_key == subject.room[1]:
        raise ValueError(
            f"culture_key {culture_key!r} equals the subject's own room's "
            "conversation_id, which is what culture keys on (primary_id) — "
            "seeding would append onto the REAL room's culture chain and "
            "move its head. Pass the REAL room's culture_key, not the "
            "subject's own."
        )
    if unit == subject.cfg.memory.workspace_id:
        raise ValueError(
            f"unit {unit!r} equals the subject's own memory.workspace_id — "
            "seeding notes would write into the REAL unit's notebook. Pass "
            "the REAL room's unit, not the subject's own isolated workspace."
        )

    engine = create_sql_engine(subject.database_url)
    try:
        shot = await snapshot(
            engine,
            platform=platform,
            conversation_id=conversation_id,
            culture_key=culture_key,
            unit=unit,
            before_ms=before_ms,
            transcript_limit=transcript_limit,
        )
    finally:
        await engine.dispose()
    if now_ms is None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return await know(
        subject, as_knowledge(shot, now_ms=now_ms), now_ms=now_ms,
        leave_room_for_arrival=leave_room_for_arrival,
    )
