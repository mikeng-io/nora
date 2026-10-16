"""The proactive ACT turn, driven as the heartbeat drives it.

`HeartbeatLoop.tick` reads due rows, screens each one, and hands the survivors
to `FirePath.run`, which dispatches by `kind` to `proactive_occasion` and
speaks. This does the same three things against the same objects on the same
graph.

The LOOP is not mirrored, and deliberately: `heartbeat.run()` is spawned as an
endless task by the composition root, so a subject with `[heartbeat] enabled`
would tick on its own cadence underneath a case — and `settle()`, which waits
for `len(deps.background)` to reach zero, would never return. A pass driven
through the whole loop would be measuring the clock and would deadlock the
one thing that waits for turns.

What IS production's, unchanged: the row goes into the real `SqlReminderStore`
and comes back out of `due()`, so its identity, state and payload are the
store's rather than this module's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore

from .obtain import Subject


@dataclass(frozen=True, slots=True)
class ActPass:
    """What one act turn left behind.

    `screened` is the SCREEN's refusal reason, and it is not a failure: a row
    the screen cancels — the room lost its `proactive` trust, the note was
    retired, the note moved on the tag path since the row was linked — is the
    system working. It is reported separately from `token` so a corpus can tell
    "she declined to speak" from "she never got the floor".
    """

    token: str
    screened: str | None
    said: tuple[str, ...]
    delivery: str


async def owe(
    subject: Subject,
    *,
    note_title: str,
    intent: str,
    decided_hours_ago: float = 6.0,
    note_moved: bool = False,
) -> Reminder:
    """Write the row the Cognition session's `remind` writes.

    `note_title` must name a note the case SEEDED: the payload carries the note
    id and the revision the decision was made against, and the screen reads
    both back. A title that seeds no note raises rather than writing a row
    pointing at nothing — the screen would then refuse it for an absent premise
    and the case would grade a refusal it did not ask for.

    `note_moved` appends a revision AFTER the row is linked, which is the tag
    path overtaking the decision. That is a real cancellation the screen is
    written to catch, and it is one of the cases.
    """
    platform, conversation_id = subject.room
    now = subject.deps.clock()
    engine = create_sql_engine(subject.database_url)
    try:
        notes = SqlNoteStore(engine)
        standing = await notes.standing(unit=subject.unit)
        matches = [n for n in standing if n.title == note_title]
        if not matches:
            raise LookupError(
                f"act case names note {note_title!r}; seeded notes are "
                f"{[n.title for n in standing]}"
            )
        if len(matches) > 1:
            # LOUD. `standing()` orders by `created_at ASC`, so a silent
            # first-match would link the EARLIER note of that title and a
            # case would be tested against a note its own transcript is not
            # about.
            #
            # With per-case workspaces this cannot happen; it raises anyway,
            # because the isolation is the thing that could regress.
            raise LookupError(
                f"{len(matches)} notes titled {note_title!r} in unit "
                f"{subject.unit!r}; the case would link one "
                "of them and there is no way to say which"
            )
        linked = matches[0]
        # A case built from a real snapshot can carry the very row it stands
        # in for: the snapshot is cut just before that row fired, so it is
        # still PENDING. Left in place it either collides with this one on the
        # open-note unique index (`DuplicateProactiveNoteError`, when it names
        # the same note) or, when it names no note (Cognition's rows carry
        # `subject_rows`, not `note_id`), fires BESIDE it, so the turn reads
        # the same intention twice. The row written below IS the case's row,
        # so the seeded copy, found by its note or by its identical text, is
        # cancelled first.
        for seeded in await subject.deps.reminders.pending_for(conversation_id):
            same_note = (seeded.payload or {}).get("note_id") == linked.id
            if seeded.kind == "proactive" and (same_note or seeded.text == intent):
                await subject.deps.reminders.cancel(
                    seeded.id, conversation_id=conversation_id,
                )
        row = Reminder(
            id=uuid.uuid4(),
            requester_person_id="nora",
            platform=platform,
            conversation_id=conversation_id,
            conversation_type="group",
            text=intent,
            created_at=now - timedelta(hours=decided_hours_ago),
            due_at=now,
            state=ReminderState.PENDING,
            fired_at=None,
            outcome=None,
            kind="proactive",
            payload={
                "note_id": linked.id,
                "revision_id": linked.head_revision_id,
                "note_title": linked.title,
                "intent": intent,
            },
        )
        await subject.deps.reminders.create(row)
        if note_moved:
            await notes.append_revision(
                unit=subject.unit,
                note_id=linked.id,
                body="Settled by the room.",
                parent_revision_id=linked.head_revision_id,
                established_by="tag:eval",
                now_ms=int(now.timestamp() * 1000) - 60_000,
            )
        return row
    finally:
        await engine.dispose()


async def act(subject: Subject, *, after_id: int = 0) -> ActPass:
    """Read the due rows, screen them, speak. The heartbeat's tick, minus the
    clock."""
    from .knowledge import heard  # local: knowledge imports obtain

    path = subject.deps.fire_path
    if path is None:
        raise LookupError("no fire path on the graph")
    now = subject.deps.clock()
    platform, conversation_id = subject.room
    # SCOPED TO THIS SUBJECT'S ROOM. `due()` is global — it returns every due
    # row in the database, which is what the heartbeat wants and what a case
    # must not have: cases share a database, so an earlier case's row is still
    # due when a later one fires, in a room the later case's policy does not
    # trust. The screen then refuses THAT row `failed_policy` and the case
    # reads as a cancellation it never asked for.
    rows = [
        r for r in await subject.deps.reminders.due(now=now, limit=64)
        if r.platform == platform and r.conversation_id == conversation_id
    ]
    if not rows:
        raise LookupError("no row is due — `owe()` must run before `act()`")

    for row in rows:
        refusal = await subject.deps.proactive_screen.refusal(row)
        if refusal is not None:
            _state, why = refusal
            return ActPass(token="", screened=why, said=(), delivery="")

    token = await path.run(rows)
    hers = await heard(subject, after_id=after_id)
    return ActPass(
        token=token,
        screened=None,
        said=tuple(b for b, _ in hers),
        delivery=",".join(sorted({s for _, s in hers})),
    )
