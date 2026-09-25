"""`nora.subject.sandbox.as_knowledge` — a reconstruction, as the `Knowledge`
that reproduces it.

The translation is arithmetic and a handful of field moves, and both are worth
pinning HERE rather than only through a database round trip: a subtraction the
wrong way round reads perfectly in a diff, produces rows a read-back happily
returns, and is only visible as a number.

**The property every timing test below asserts is the same one**: NOTHING
MOVES. The sandbox shows the room as it was at the instant because the
subject's clock is frozen there (`SubjectSpec.now = before_ms`), so every row
keeps its real `ts` and the standing's age is how stale it was THEN. An
earlier design shifted every timestamp onto the wall clock instead, which was
only necessary while the clock was not a port.
**A real sender id not travelling is pinned in
`tests/integration/test_a_real_snapshot_seeds_an_isolated_subject.py`, against
the `messages.sender_id` column, and deliberately not here.** `Message` has no
such field, so a unit-level version of that assertion would be a test
parametrized over the fact it pins: it cannot fail while the dataclass is what
it is, and it would read as coverage.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import UUID

import pytest

from nora.domain.culture import CultureRevision
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import Note, NoteRevision
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.standing import Standing
from nora.infra.sql.snapshot import Snapshot
from nora.subject.sandbox import _note_ids_by_title, _pending_reminder_note_ids, as_knowledge

#: The historical instant the snapshot reconstructs — and, under one path,
#: the instant the case is driven at.
BEFORE_MS = 1_752_000_000_000
MINUTE = 60_000
HOUR = 3_600_000


def _row(
    row_id: int,
    body: str | None,
    *,
    ts: int,
    sender_name: str | None = "Miles",
    person_id: str | None = "miles_novak",
    is_nora: bool = False,
) -> TranscriptRow:
    return TranscriptRow(
        id=row_id,
        # A REAL sender id, so the tests below can assert it does not travel.
        sender_id="+85214535926",
        sender_name=sender_name,
        ts=ts,
        body=body,
        is_nora=is_nora,
        delivery_status=DeliveryStatus.SENT,
        raw_json=None,
        person_id=person_id,
    )


def _note(*, anchor_date: int | None = 20260701, entity_ids: tuple[str, ...] = ()) -> Note:
    return Note(
        id="note-1",
        unit="friends",
        title="Dinner 19 Sept",
        description="whether the table is booked",
        uncertainty="open",
        closing_condition="somebody books it",
        established_in="group-one",
        created_at=BEFORE_MS - 10 * HOUR,
        head_revision_id="rev-2",
        anchor_date=anchor_date,
        anchor_entity_ids=entity_ids,
    )


def _revision(body: str) -> NoteRevision:
    return NoteRevision(
        id="rev-1",
        note_id="note-1",
        body=body,
        parent_revision_id=None,
        written_at=BEFORE_MS - 9 * HOUR,
        established_by="debrief",
    )


def _shot(**over: object) -> Snapshot:
    base: dict[str, object] = {
        "platform": "signal",
        "conversation_id": "group-one",
        "before_ms": BEFORE_MS,
        "transcript": (),
        "standing": None,
        "culture": None,
        "notes": (),
        "reminders": (),
    }
    base.update(over)
    return Snapshot(**base)  # type: ignore[arg-type]


# --- the clock ---------------------------------------------------------------


def test_the_window_keeps_its_real_timestamps() -> None:
    """Ten minutes before the instant IS ten minutes before the subject's
    clock, because the clock is frozen at the instant. Nothing is shifted:
    a shift would only be right against a clock the subject is not reading.
    """
    shot = _shot(transcript=(
        _row(1, "are we still on", ts=BEFORE_MS - 10 * MINUTE),
        _row(2, "think so", ts=BEFORE_MS - 4 * MINUTE),
    ))

    knowledge = as_knowledge(shot)

    assert [m.at_ms for m in knowledge.transcript] == [
        BEFORE_MS - 10 * MINUTE, BEFORE_MS - 4 * MINUTE,
    ]


def test_a_row_said_at_the_instant_stays_at_the_instant() -> None:
    """The newest line had JUST been said — which is what the room looked
    like at the moment being reconstructed. An arriving line stamped the same
    millisecond still sorts above it, because `recent` breaks a `ts` tie on
    `id`."""
    shot = _shot(transcript=(_row(1, "right now", ts=BEFORE_MS),))

    assert as_knowledge(shot).transcript[0].at_ms == BEFORE_MS


def test_the_standing_carries_how_stale_it_was_THEN() -> None:
    """A position folded three hours before the instant is three hours stale in
    the sandbox: `know(now_ms=before_ms)` turns the age straight back into
    the real `updated_at`."""
    shot = _shot(standing=Standing(
        platform="signal", conversation_id="group-one",
        body="they are deciding where to eat",
        fold_count=4, updated_at=BEFORE_MS - 3 * HOUR,
    ))

    knowledge = as_knowledge(shot)

    assert knowledge.standing == "they are deciding where to eat"
    assert knowledge.standing_age_hours == 3.0


# --- what travels, and what does not -----------------------------------------


def test_a_sender_the_identity_does_not_know_stays_unknown() -> None:
    """`person=None` and `person=""` are different facts and must not collapse.

    `""` is `Message`'s "use the default for this name", which resolves to
    `miles_novak`; a real row with a null `person_id` means the identity knows
    nobody by that sender, and seeding it as the default would put a stranger's
    words in a member's mouth.
    """
    shot = _shot(transcript=(
        _row(1, "who is this", ts=BEFORE_MS, sender_name="Jordan", person_id=None),
    ))

    assert as_knowledge(shot).transcript[0].person is None


def test_a_nameless_sender_keeps_having_no_name() -> None:
    """`messages.sender_name` is nullable — a sealed-sender Signal row carries
    no display name — and inventing "Unknown" would be a name the room never
    saw."""
    shot = _shot(transcript=(
        _row(1, "...", ts=BEFORE_MS, sender_name=None, person_id=None),
    ))

    assert as_knowledge(shot).transcript[0].name is None


def test_her_own_speech_stays_hers() -> None:
    """`is_nora` is a FLAG, not a name (`nora.agent.prefill._speaker`). A
    reconstruction that lost it would show a room talking to nobody."""
    shot = _shot(transcript=(
        _row(1, "want me to book it?", ts=BEFORE_MS, is_nora=True),
    ))

    assert as_knowledge(shot).transcript[0].is_nora is True


def test_each_message_is_labelled_with_the_row_it_came_from() -> None:
    """`Written.rows` then maps the historical row id onto the sandbox's own,
    which is what lets a case cite "the message that started it" by the id it
    really has."""
    shot = _shot(transcript=(_row(4321, "hi", ts=BEFORE_MS),))

    assert as_knowledge(shot).transcript[0].label == 4321


def test_a_bodiless_row_survives_as_an_empty_body() -> None:
    """`messages.body` is nullable and `NormalizedMessage.body` is not."""
    shot = _shot(transcript=(_row(1, None, ts=BEFORE_MS),))

    assert as_knowledge(shot).transcript[0].body == ""


# --- culture and the notebook ------------------------------------------------


def test_the_culture_account_current_then_is_what_is_seeded() -> None:
    shot = _shot(culture=CultureRevision(
        id="cr-1", culture_key="group-one",
        body="they roast each other and mean it kindly",
        written_at=BEFORE_MS - 40 * HOUR, established_by="run-7",
    ))

    assert as_knowledge(shot).culture == (
        "they roast each other and mean it kindly"
    )


def test_the_note_body_is_the_as_of_revision_not_the_head() -> None:
    """`Note.head_revision_id` points at TODAY's head; `snapshot()` pairs the
    note with the revision that was current at the instant precisely so nobody
    resolves the head and silently gets the wrong body. The translation must
    read the pair's second element and nothing else."""
    shot = _shot(notes=((_note(), _revision("nobody has booked it yet")),))

    seed = as_knowledge(shot).notes[0]

    assert seed.body == "nobody has booked it yet"
    assert seed.title == "Dinner 19 Sept"
    assert seed.description == "whether the table is booked"
    assert seed.uncertainty == "open"
    assert seed.closing_condition == "somebody books it"
    assert seed.anchor_date == 20260701


def test_a_note_anchored_only_by_entities_is_refused_by_name() -> None:
    """`NoteSeed` can state an `anchor_date` and not an entity list, so such a
    note would reach `Note.__post_init__` with no anchor at all and raise from
    inside a constructor, naming nothing a reader could act on. Refused here
    instead — and refused rather than DROPPED, because a note missing from the
    index is invisible, and invisible is indistinguishable from absent."""
    shot = _shot(notes=(
        (_note(anchor_date=None, entity_ids=("ent-1",)), _revision("body")),
    ))

    with pytest.raises(ValueError, match="anchored by entity ids alone"):
        as_knowledge(shot)


def test_an_empty_snapshot_translates_to_an_empty_knowledge() -> None:
    """A room that held nothing is a legal case — `Knowledge`'s own docstring
    calls empty the knowledge cut — and must not become four `None`s that raise
    somewhere else."""
    knowledge = as_knowledge(_shot())

    assert knowledge.transcript == ()
    assert knowledge.standing is None
    assert knowledge.standing_age_hours is None
    assert knowledge.culture is None
    assert knowledge.notes == ()


# --- the reminder tier -------------------------------------------------------


def _reminder(**over: object) -> Reminder:
    base: dict[str, object] = {
        "id": UUID("11111111-2222-3333-4444-555555555555"),
        "requester_person_id": "tyler_cortez",
        "platform": "signal",
        "conversation_id": "group-one",
        "conversation_type": "group",
        "text": "ask the room where dinner is",
        "created_at": _at(BEFORE_MS - 6 * HOUR),
        "due_at": _at(BEFORE_MS + 2 * HOUR),
        "state": ReminderState.PENDING,
        "fired_at": None,
        "outcome": None,
    }
    base.update(over)
    return Reminder(**base)  # type: ignore[arg-type]


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def test_a_reminder_keeps_the_offsets_it_really_had() -> None:
    """The clock is frozen at the instant, so `know()` reproduces `due_at`
    exactly: the seed carries the OFFSET the real row had, not a new one."""
    seed = as_knowledge(_shot(reminders=(_reminder(),))).reminders[0]
    assert seed.due_in_seconds == 2 * HOUR / 1000
    assert seed.created_seconds_ago == 6 * HOUR / 1000


def test_an_overdue_reminder_stays_overdue_by_the_same_margin() -> None:
    """The case the proactive path is about. A negative offset is the whole
    reason `due_in_seconds` is signed rather than a duration."""
    overdue = _reminder(due_at=_at(BEFORE_MS - 90 * 60 * 1000))
    seed = as_knowledge(_shot(reminders=(overdue,))).reminders[0]
    assert seed.due_in_seconds == -5400.0


def test_the_requester_travels_because_it_is_a_handle_not_an_account() -> None:
    seed = as_knowledge(_shot(reminders=(_reminder(),))).reminders[0]
    assert seed.requester_person_id == "tyler_cortez"


def test_a_proactive_row_keeps_its_kind_and_payload() -> None:
    """`kind` is what the row IS (DEC-139) and a proactive act fires on it, so
    a translation that dropped it would turn every act case into a plain
    reminder and measure nothing the case is about."""
    row = _reminder(kind="proactive", payload={"note_id": "n-1"})
    seed = as_knowledge(_shot(reminders=(row,))).reminders[0]
    assert seed.kind == "proactive"
    assert seed.payload == {"note_id": "n-1"}


def test_a_snapshot_with_no_reminders_seeds_none() -> None:
    assert as_knowledge(_shot()).reminders == ()


# --- issue #158: which notes NORA already has a reminder pending on ---------


def test_pending_reminder_note_ids_reads_noras_own_rows_that_name_a_note() -> None:
    """Backs `evals.backbone._refuse_a_remind_case_already_pending`: only
    NORA's own outstanding reminders that name a note count — a member's own
    reminder, or a row with no note, is not a pending decision about one.

    BY ID (issue #158 review), off `payload["note_id"]`
    (`nora/agent/tools.py:8923`) — not the title, which a rename or a
    duplicate title would make unreliable."""
    ids = _pending_reminder_note_ids((
        _reminder(requester_person_id="nora", kind="proactive",
                   payload={"note_id": "note-1", "note_title": "Dinner plan"}),
        _reminder(requester_person_id="tyler_cortez", kind="proactive",
                   payload={"note_id": "note-2", "note_title": "F1 update"}),
        _reminder(requester_person_id="nora", kind="reminder", payload=None),
    ))
    assert ids == frozenset({"note-1"})


def test_pending_reminder_note_ids_is_empty_with_nothing_pending() -> None:
    assert _pending_reminder_note_ids(()) == frozenset()


def test_note_ids_by_title_resolves_a_single_note() -> None:
    notes = ((_note(), _revision("open")),)
    assert _note_ids_by_title(notes) == {"Dinner 19 Sept": ("note-1",)}


def test_note_ids_by_title_collects_every_id_sharing_a_title() -> None:
    """`NOTE` has no unique constraint on `(workspace, title)` (`_notebook`'s
    own docstring): two notes can share a title, and a caller resolving one
    by title must be able to see that it is ambiguous rather than pick one."""
    twin = dataclasses.replace(_note(), id="note-2")
    notes = ((_note(), _revision("open")), (twin, _revision("open")))
    assert _note_ids_by_title(notes) == {"Dinner 19 Sept": ("note-1", "note-2")}


def test_note_ids_by_title_is_empty_with_no_notes() -> None:
    assert _note_ids_by_title(()) == {}
