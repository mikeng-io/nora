"""`sandbox_from_snapshot()` — a REAL room's history, copied into an isolated
subject, end to end against Postgres.

**Everything here is seeded through the stores production writes with** —
`SqlRecordStore`, `SqlStandingStore`, `SqlCultureStore`, `SqlNoteStore` — and
read back through them too, for `tests/infra/test_snapshot.py`'s reason: a copy
checked against rows a test wrote its own way measures the test's idea of the
schema.

**The two claims this file exists to hold, and neither is provable from the
other:**

1. the isolated subject's own stores contain a FAITHFUL copy — the transcript
   with the ages it had at the instant, the standing with the staleness it had
   then, the culture account and the notebook that were current;
2. the REAL room is UNTOUCHED by the seeding. That one is asserted explicitly
   rather than inferred from "the code does not write there": the read and the
   write happen over ONE physical database (see `sandbox_from_snapshot`'s own
   docstring), so "wrote into the wrong room" is a live failure mode and not a
   hypothetical one.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.domain.standing import Standing
from nora.infra.sql import create_sql_engine
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import MESSAGES
from nora.infra.sql.standing import SqlStandingStore
from nora.policy import load_policy
from nora.subject import (
    Knowledge,
    Subject,
    SubjectSpec,
    know,
    obtain,
    room_for,
    sandbox_from_snapshot,
    workspace_for,
)

#: The REAL room the history belongs to. Nothing in this file lets the subject
#: be composed against it — that is the point.
REAL = ("signal", "the-real-group")
REAL_CULTURE_KEY = "the-real-group"
REAL_UNIT = "the-real-unit"

BEFORE_MS = 1_752_000_000_000
NOW_MS = 1_757_000_000_000
MINUTE = 60_000
HOUR = 3_600_000


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def subject(pg_url: str, tmp_path: Any, monkeypatch: Any) -> AsyncIterator[Subject]:
    """A subject of this test's own — its own room, its own workspace.

    `room_for`/`workspace_for` are what every eval case already uses; the
    sandbox writes into whatever they produced, which is why nothing below
    passes a write target.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    subject = await obtain(SubjectSpec(
        database_url=pg_url,
        workdir=tmp_path / "subject",
        room=room_for("sandbox-from-snapshot"),
        overrides={"memory.workspace_id": workspace_for("sandbox-from-snapshot")},
        now=None,
    ))
    try:
        yield subject
    finally:
        for shut in (
            subject.deps.background.cancel_all(),
            subject.deps.audit.aclose(),
            subject.deps.client.close(),
        ):
            with contextlib.suppress(Exception):
                await shut


async def _say(engine: Any, body: str, *, at: int, name: str = "Miles",
               person_id: str | None = "miles_novak", is_nora: bool = False) -> int:
    return await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            platform=REAL[0], conversation_id=REAL[1], conversation_type="group",
            # A REAL-SHAPED account id, so "did it travel" is answerable below.
            sender_id="+85214535926", sender_name=name, timestamp=at,
            body=body, raw={},
        ),
        person_id=person_id,
        is_nora=is_nora,
    )


@pytest.fixture
async def history(engine: Any) -> dict[str, Any]:
    """The real room, as it stood at `BEFORE_MS` — and as it stood AFTER, so
    the copy can be caught taking today's state instead of the instant's.

    Every tier carries a second, LATER state for `tests/infra/test_snapshot.py`'s
    reason: a reconstruction that ignored `before_ms` passes any single-state
    test.
    """
    rows = {
        "first": await _say(engine, "are we still on for friday", at=BEFORE_MS - 10 * MINUTE),
        # SAME display name, NO person. Found by falsifying: with a distinct
        # name, `_speakers()` mints an unmapped sender anyway, so `person=""`
        # and `person=None` produce the same row and the assertion below cannot
        # tell them apart. A real row whose sender never resolved is exactly
        # this shape, and it is the only shape that discriminates.
        "second": await _say(engine, "i can't make it", at=BEFORE_MS - 4 * MINUTE,
                             person_id=None),
        "hers": await _say(engine, "want me to move it?", at=BEFORE_MS - MINUTE,
                           is_nora=True),
        # AFTER the instant. Must not reach the sandbox.
        "later": await _say(engine, "moved it to saturday", at=BEFORE_MS + HOUR),
    }
    standing = SqlStandingStore(engine)
    await standing.write(standing=Standing(
        platform=REAL[0], conversation_id=REAL[1],
        body="they are deciding whether friday still works",
        fold_count=3, updated_at=BEFORE_MS - 3 * HOUR,
    ))
    await standing.write(standing=Standing(
        platform=REAL[0], conversation_id=REAL[1],
        body="friday is off; saturday is the plan",
        fold_count=4, updated_at=BEFORE_MS + 2 * HOUR,
    ))
    culture = SqlCultureStore(engine)
    await culture.append(
        key=REAL_CULTURE_KEY, body="they confirm plans late and nobody minds",
        written_at=BEFORE_MS - 40 * HOUR, established_by="run-7",
    )
    await culture.append(
        key=REAL_CULTURE_KEY, body="they confirm plans late, and now they say so",
        written_at=BEFORE_MS + 3 * HOUR, established_by="run-8",
    )
    notes = SqlNoteStore(engine)
    note = await notes.create(
        unit=REAL_UNIT, title="Friday dinner", description="whether friday holds",
        uncertainty="open", closing_condition="somebody says yes or no",
        established_in=REAL[1], body="nobody has confirmed friday",
        established_by="debrief", now_ms=BEFORE_MS - 9 * HOUR, anchor_date=20260701,
    )
    await notes.append_revision(
        unit=REAL_UNIT, note_id=note.id, body="jordan is out; friday is dead",
        parent_revision_id=note.head_revision_id, established_by="debrief",
        now_ms=BEFORE_MS + 4 * HOUR,
    )
    # A note that did not exist yet at the instant.
    await notes.create(
        unit=REAL_UNIT, title="Saturday dinner", description="whether saturday holds",
        uncertainty="open", closing_condition="somebody books it",
        established_in=REAL[1], body="nobody has booked saturday",
        established_by="debrief", now_ms=BEFORE_MS + 5 * HOUR, anchor_date=20260702,
    )
    return {"rows": rows, "note": note}


async def _seed(subject: Subject) -> Any:
    return await sandbox_from_snapshot(
        subject,
        platform=REAL[0], conversation_id=REAL[1], before_ms=BEFORE_MS,
        culture_key=REAL_CULTURE_KEY, unit=REAL_UNIT, now_ms=NOW_MS,
    )


# --- the fourth gap: the seed's unit must be the TURN's unit too -------------


async def test_the_seeded_unit_is_the_one_a_real_turn_reads(
    pg_url: str, tmp_path: Any, monkeypatch: Any,
) -> None:
    """**Found by a THIRD review pass.** The three refusals guard where the
    seed LANDS; none of them guard whether a real entry point driven
    afterward reads notes back from the SAME place. Production resolves a
    note's unit from `Conversation.unit` (`nora.app.composition
    ._note_unit_of`), never from `memory.workspace_id` -- so a subject whose
    generated policy did not tie the two together got notes seeded
    invisibly (or worse, a turn's own write landing at the policy's default
    unit instead of the isolated one).

    `Subject` exposes no field for the composed room `Policy` (`deps.policy`
    is the unrelated ENGAGE policy), so this reads back the ACTUAL
    `policy.toml` `build_config` wrote, through the real parser, rather than
    reaching into the graph -- the same evidentiary standard `test_the_real
    _room_is_not_written_to` uses for the database side of this same claim.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    own_room = room_for("the-seeded-unit-test")
    own_workspace = workspace_for("the-seeded-unit-test")
    workdir = tmp_path / "subject"
    subject = await obtain(SubjectSpec(
        database_url=pg_url, workdir=workdir, room=own_room,
        overrides={"memory.workspace_id": own_workspace}, now=None,
    ))
    try:
        policy = load_policy(workdir / "policy.toml")
        convo = policy.conversation(*own_room)
        assert convo is not None, f"{own_room} names no conversation in its own policy"
        assert convo.unit == own_workspace == subject.cfg.memory.workspace_id
    finally:
        for shut in (
            subject.deps.background.cancel_all(),
            subject.deps.audit.aclose(),
            subject.deps.client.close(),
        ):
            with contextlib.suppress(Exception):
                await shut


# --- the copy ----------------------------------------------------------------


async def test_the_isolated_room_holds_the_transcript_as_it_stood_then(
    subject: Subject, history: dict[str, Any]
) -> None:
    """The three rows at or before the instant, in order, and not the fourth."""
    await _seed(subject)

    rows = await subject.deps.audit.recent(
        conversation_id=subject.room[1], limit=50,
    )

    assert [r.body for r in rows] == [
        "are we still on for friday", "i can't make it", "want me to move it?",
    ]


async def test_the_copied_window_has_the_ages_it_had_at_the_instant(
    subject: Subject, history: dict[str, Any]
) -> None:
    """The whole timeline is shifted by `now_ms - before_ms`, so what was ten
    minutes old is ten minutes old. Copying `ts` verbatim would render a room
    that had said nothing for two months — a different input, and one that
    never existed."""
    await _seed(subject)

    rows = await subject.deps.audit.recent(
        conversation_id=subject.room[1], limit=50,
    )

    assert [r.ts for r in rows] == [
        NOW_MS - 10 * MINUTE, NOW_MS - 4 * MINUTE, NOW_MS - MINUTE,
    ]


async def test_her_own_rows_come_back_as_hers(
    subject: Subject, history: dict[str, Any]
) -> None:
    await _seed(subject)

    rows = await subject.deps.audit.recent(
        conversation_id=subject.room[1], limit=50,
    )

    assert [r.is_nora for r in rows] == [False, False, True]


async def test_a_real_account_id_does_not_reach_the_sandbox(
    subject: Subject, engine: Any, history: dict[str, Any]
) -> None:
    """`Message` has no `sender_id`, so `_speakers()` mints one. Asserted
    against the DATABASE rather than the dataclass: this is the property that
    makes seeding a real incident into a shared eval database something other
    than copying a member's phone number into it."""
    await _seed(subject)

    async with engine.connect() as conn:
        senders = (await conn.execute(
            sa.select(MESSAGES.c.sender_id).where(
                MESSAGES.c.conversation_id == subject.room[1]
            )
        )).scalars().all()

    assert "+85214535926" not in set(senders)


async def test_a_sender_the_identity_did_not_know_stays_unknown(
    subject: Subject, history: dict[str, Any]
) -> None:
    """A null `person_id` is a real fact about a real row and must survive as
    one. Seeding it as the default person would put a stranger's words in a
    member's mouth, which is precisely the `person_name=None` case the eval
    corpora exist to cover."""
    await _seed(subject)

    rows = await subject.deps.audit.recent(
        conversation_id=subject.room[1], limit=50,
    )

    assert [r.person_id for r in rows] == ["miles_novak", None, "miles_novak"]


async def test_the_returned_map_translates_a_real_row_id_into_the_copy(
    subject: Subject, history: dict[str, Any]
) -> None:
    """`Written.rows` is keyed by the HISTORICAL row id, so a case can cite the
    message that really started the incident and be graded against the row the
    sandbox's store assigned."""
    written = await _seed(subject)

    rows = await subject.deps.audit.recent(
        conversation_id=subject.room[1], limit=50,
    )
    by_id = {r.id: r.body for r in rows}

    assert by_id[written.rows[history["rows"]["first"]]] == (
        "are we still on for friday"
    )
    assert history["rows"]["later"] not in written.rows


async def test_the_isolated_room_holds_the_position_she_held_then(
    subject: Subject, engine: Any, history: dict[str, Any]
) -> None:
    """The middle fold, with the staleness it had at the instant — not the one
    written two hours later, and not "three hours before today"."""
    await _seed(subject)

    standing = await SqlStandingStore(engine).read(
        platform=subject.room[0], conversation_id=subject.room[1],
    )

    assert standing is not None
    assert standing.body == "they are deciding whether friday still works"
    assert standing.updated_at == NOW_MS - 3 * HOUR


async def test_the_isolated_room_holds_the_culture_account_current_then(
    subject: Subject, engine: Any, history: dict[str, Any]
) -> None:
    """Written under the SUBJECT's own culture key, which is its room's
    `primary_id` — never the real room's."""
    await _seed(subject)

    head = await SqlCultureStore(engine).head(key=subject.room[1])

    assert head is not None
    assert head.body == "they confirm plans late and nobody minds"


async def test_the_isolated_workspace_holds_the_notebook_as_it_stood_then(
    subject: Subject, engine: Any, history: dict[str, Any]
) -> None:
    """One note, with the body its AS-OF revision carried — not the head's,
    which was written four hours after the instant — and not the note created
    five hours after it."""
    await _seed(subject)

    notes = await SqlNoteStore(engine).standing(
        unit=subject.cfg.memory.workspace_id,
    )

    assert [n.title for n in notes] == ["Friday dinner"]
    revision = await SqlNoteStore(engine).revision_by_id(
        revision_id=notes[0].head_revision_id,
    )
    assert revision is not None
    assert revision.body == "nobody has confirmed friday"


# --- the real room is untouched ----------------------------------------------


async def test_the_real_room_is_not_written_to(
    subject: Subject, engine: Any, history: dict[str, Any]
) -> None:
    """The read and the write share ONE physical database, so this is a live
    failure mode rather than a hypothetical one: every tier of the real room
    must be exactly what the fixture left."""
    await _seed(subject)

    async with engine.connect() as conn:
        bodies = (await conn.execute(
            sa.select(MESSAGES.c.body)
            .where(MESSAGES.c.conversation_id == REAL[1])
            .order_by(MESSAGES.c.ts.asc())
        )).scalars().all()
    assert list(bodies) == [
        "are we still on for friday", "i can't make it", "want me to move it?",
        "moved it to saturday",
    ]

    standing = await SqlStandingStore(engine).read(
        platform=REAL[0], conversation_id=REAL[1],
    )
    assert standing is not None
    assert standing.body == "friday is off; saturday is the plan"

    head = await SqlCultureStore(engine).head(key=REAL_CULTURE_KEY)
    assert head is not None
    assert head.body == "they confirm plans late, and now they say so"

    real_notes = await SqlNoteStore(engine).standing(unit=REAL_UNIT)
    assert sorted(n.title for n in real_notes) == ["Friday dinner", "Saturday dinner"]


async def test_seeding_a_room_into_itself_is_refused(
    subject: Subject, history: dict[str, Any]
) -> None:
    """The one argument that would make this write into the room it read.

    Fail-LOUD rather than a no-op: a case built this way would drive an entry
    point against a room holding two copies of itself and would be graded
    anyway.

    `culture_key=REAL_CULTURE_KEY`, not `subject.room[1]`: found by a THIRD
    review pass -- with `culture_key=subject.room[1]` (an earlier version of
    this test), falsifying the conversation_id check alone left this GREEN,
    caught by the culture check instead. Pinned by the wrong check is still
    pinned, but this test's own name and match= claim the conversation_id
    check specifically, so only that one may collide here."""
    with pytest.raises(ValueError, match="equals the subject's own room's conversation_id"):
        await sandbox_from_snapshot(
            subject,
            platform=subject.room[0], conversation_id=subject.room[1],
            before_ms=BEFORE_MS, culture_key=REAL_CULTURE_KEY, unit=REAL_UNIT,
        )


async def test_a_cross_platform_conversation_id_collision_is_refused(
    subject: Subject,
) -> None:
    """**Found by a SECOND independent review, after the first fix landed.**
    The original room check compared the FULL `(platform, conversation_id)`
    tuple — but `SqlRecordStore.recent`/`since`/`since_including_nora` filter
    on `conversation_id` ALONE (`TranscriptRow` carries no platform column at
    all), so a subject on a DIFFERENT platform sharing the real room's
    conversation_id passed the tuple check untouched. Reproduced live: seeded
    rows, clock-shifted to sort NEWEST, came back out of the REAL room's own
    `recent()` read. The check is now keyed on `conversation_id` alone, the
    same key the read path actually uses."""
    other_platform = "discord" if subject.room[0] != "discord" else "signal"
    with pytest.raises(ValueError, match="equals the subject's own room's conversation_id"):
        await sandbox_from_snapshot(
            subject,
            # culture_key is REAL_CULTURE_KEY, not subject.room[1]: this
            # isolates the conversation_id check from the culture_key check
            # below it. With both set equal to subject.room[1] (as an
            # earlier draft of this test did), the culture_key check alone
            # is enough to make the test pass even with the conversation_id
            # check falsified out -- caught by falsifying this test itself.
            platform=other_platform, conversation_id=subject.room[1],
            before_ms=BEFORE_MS, culture_key=REAL_CULTURE_KEY, unit=REAL_UNIT,
        )


async def test_a_culture_key_matching_the_subjects_own_room_is_refused(
    subject: Subject,
) -> None:
    """Isolates the CULTURE-specific check from the conversation_id check
    above: `culture_key` is not required to equal the snapshot source's own
    `conversation_id` (a real room's culture can be keyed on a different
    `primary_id` than the room being read), so this uses a conversation_id
    that is genuinely NOT the subject's own — only `culture_key` collides.
    A room whose `conversation_id` differs but whose `culture_key` equals the
    subject's own room passes the check above and is caught only here."""
    with pytest.raises(ValueError, match="culture_key"):
        await sandbox_from_snapshot(
            subject,
            platform=REAL[0], conversation_id=REAL[1],
            before_ms=BEFORE_MS, culture_key=subject.room[1], unit=REAL_UNIT,
        )


async def test_a_unit_matching_the_subjects_own_workspace_is_refused(
    subject: Subject,
) -> None:
    """No check caught this at all before: notes key on `memory.workspace_id`,
    which is not `subject.room`, so a `unit` equal to the subject's own
    isolated workspace collided with the real unit's notebook by omission
    alone -- reachable without any argument being wrong in isolation, only
    two calls agreeing on a value neither checked against the other."""
    with pytest.raises(ValueError, match="equals the subject's own memory"):
        await sandbox_from_snapshot(
            subject,
            platform=REAL[0], conversation_id=REAL[1],
            before_ms=BEFORE_MS, culture_key=REAL_CULTURE_KEY,
            unit=subject.cfg.memory.workspace_id,
        )


# --- `Knowledge.culture`, stated rather than snapshotted ---------------------


async def test_know_writes_a_stated_culture_account_for_the_rooms_own_key(
    subject: Subject, engine: Any
) -> None:
    """The new field, exercised the way every other `Knowledge` field is: state
    it, then read it back through the store production reads it with.

    The key is the room's `conversation_id`, which IS its `primary_id` because
    `build_config` resolves the policy it writes through the real parser and
    refuses unless they agree — not merely a substring test that the room's id
    appears somewhere in the file (found insufficient, fourth review pass). A
    key that matched nothing, or matched the wrong room, would read exactly
    like a room that had never been folded, or silently key under a real other
    room — no error, no row where expected, and an eval case grading a prompt
    missing the block it is about, or worse, a live one it was never about."""
    await know(subject, Knowledge(culture="they speak in half sentences"))

    head = await SqlCultureStore(engine).head(key=subject.room[1])

    assert head is not None
    assert head.body == "they speak in half sentences"
    # `culture_revision.established_by` is NOT NULL, so a seed has to name
    # something; it names the same thing the note seed does.
    assert head.established_by == "case-seed"
    assert head.parent_revision_id is None


async def test_a_knowledge_that_states_no_culture_writes_no_revision(
    subject: Subject, engine: Any
) -> None:
    """`None` is the knowledge cut, not "write an empty account". An empty
    account renders as a `<previous_account>` block saying nothing, which is a
    different input from having no such block."""
    await know(subject, Knowledge(standing="they are mid-plan"))

    assert await SqlCultureStore(engine).head(key=subject.room[1]) is None
