"""The Cognition session writes live: each verb runs the
validator the tag path runs and then commits through the tier's own store, stamped
`cognition:<unit>`, and a refusal still comes back to the model. One test per verb,
over in-memory stores, with a scripted agent (`tests/unit/cognition_rig.py`)."""

from __future__ import annotations

from typing import Any

from nora.app.cognition_session import now_utc
from nora.domain.cognition_ground import Grounding, NoteLine
from nora.domain.cognition_session import AgendaItem, Room, build_agenda
from nora.domain.gatherings import GatheringStatus
from nora.domain.notes import Note, NoteSupersedeReason, likely_duplicates
from nora.domain.reminders import ReminderState
from nora.domain.standing import StandingHead
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import render_agenda
from nora.testing.note_store import InMemoryNoteStore
from tests.unit.cognition_rig import GOOD_BODY, NOTE, NOW, A, Stores, iso, session

PROVENANCE = "cognition:u"


async def _seed_note(stores: Stores, title: str = "dinner") -> Any:
    return await stores.notes.create(
        unit="u", title=title, description="where", uncertainty="the place",
        closing_condition="a booking", established_in=A.conversation_id,
        body=GOOD_BODY, established_by="tag:p_ada", now_ms=NOW - 1000,
        anchor_entity_ids=("Ada",),
    )


async def test_note_create_commits_with_cognition_provenance_and_backs_a_written_close():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["close"](item_index=0, reason="written"))
        answers.append(await by["note_create"](body=GOOD_BODY, **NOTE))
        answers.append(await by["close"](item_index=0, reason="written"))

    stores, outcome = await session(behave)
    assert answers[0]["reason"] == "no_write_for_item"
    assert answers[1]["status"] == "ok" and answers[2]["status"] == "ok"
    (note,) = await stores.notes.standing(unit="u")
    assert note.title == "dinner" and answers[1]["note_id"] == note.id
    (revision,) = [r for r in stores.notes.revisions.values() if r.note_id == note.id]
    assert revision.established_by == PROVENANCE
    assert [c.reason for c in outcome.closed] == ["written"]


async def test_a_body_citing_a_message_the_session_was_not_shown_is_refused_and_writes_nothing():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_create"](
            body=GOOD_BODY.replace("[101]", "[999]"), **NOTE,
        ))

    stores, _ = await session(behave)
    assert answers[0]["status"] == "refused"
    assert await stores.notes.standing(unit="u") == ()


async def test_note_update_appends_and_a_stale_parent_is_unavailable():
    stores = Stores()
    note = await _seed_note(stores)
    answers: list[Any] = []
    body = GOOD_BODY + "- who pays (Ben) [102]\n"

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        for _ in range(2):
            answers.append(await by["note_update"](
                note_id=note.id, body=body, parent_revision_id=note.head_revision_id,
            ))

    await session(behave, stores=stores)
    assert answers[0]["status"] == "ok"
    assert answers[1] == {**answers[1], "status": "unavailable", "reason": "stale_head"}
    _, head = await stores.notes.read(unit="u", note_id=note.id)
    assert head.body.endswith("[102]") and head.established_by == PROVENANCE


async def test_note_retire_closes_a_settled_note():
    stores = Stores()
    note = await _seed_note(stores)
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_retire"](note_id=note.id, reason="settled"))
        answers.append(await by["note_retire"](note_id=note.id, reason="settled"))

    await session(behave, stores=stores)
    assert [a["status"] for a in answers] == ["ok", "refused"]
    retired = stores.notes.notes[note.id]
    assert retired.retire_reason == NoteSupersedeReason.RETIRED_CLOSED
    assert await stores.notes.standing(unit="u") == ()


async def test_note_merge_folds_the_duplicate_into_the_survivor():
    stores = Stores()
    survivor = await _seed_note(stores, "dinner")
    duplicate = await _seed_note(stores, "dinner place")
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_merge"](
            survivor_id=survivor.id, duplicate_id=duplicate.id, body=GOOD_BODY,
        ))

    await session(behave, stores=stores)
    assert answers[0]["status"] == "ok"
    folded = stores.notes.notes[duplicate.id]
    assert folded.retire_reason == NoteSupersedeReason.RETIRED_MERGED
    assert folded.replaced_by == survivor.id
    assert [n.id for n in await stores.notes.standing(unit="u")] == [survivor.id]


async def test_events_commit_with_their_stated_mode_and_move_on_the_live_service():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="abduced", confidence="low", cited_ids=["101"],
            when=iso(NOW + 86_400_000),
        ))
        answers.append(await by["event_set_status"](
            event_id=answers[0]["id"], status="confirmed", reasoning="told",
            confidence="high", cited_ids=["102"],
        ))

    stores, _ = await session(behave)
    assert [a["status"] for a in answers] == ["ok", "ok"]
    (event,) = await stores.gatherings.list_for_conversation(conversation_id=A.conversation_id)
    assert event.created_by == PROVENANCE and event.status is GatheringStatus.CONFIRMED
    assert {m.field: m.reasoning for m in event.field_meta}["start"] == "abduced"


async def test_an_event_with_a_time_owns_no_reminder():
    """DEC-303: an event is a record, never a reminder's owner. Proposing one
    with a start and moving that start write no reminder row; whether, when and
    what to remind is the session's own `remind`, which cites the event."""
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["101"],
            when=iso(NOW + 86_400_000),
        ))
        answers.append(await by["event_revise"](
            event_id=answers[0]["id"], when=iso(NOW + 2 * 86_400_000),
            reasoning="told", confidence="high", cited_ids=["102"],
        ))

    stores, _ = await session(behave)
    assert [a["status"] for a in answers] == ["ok", "ok"]
    (event,) = await stores.gatherings.list_for_conversation(conversation_id=A.conversation_id)
    assert event.when is not None and event.reminder_id is None
    assert list(await stores.reminders.all_pending(limit=100)) == []


async def test_standing_revise_writes_the_rooms_position():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](where_it_stands="dinner is on, place open"))

    stores, _ = await session(behave, head=None)
    assert answers[0]["status"] == "ok"
    written = await stores.standing.read(platform=A.platform, conversation_id=A.conversation_id)
    assert written is not None and written.body == "dinner is on, place open"


async def test_a_standing_revise_against_a_stale_base_is_refused():
    head = StandingHead(standing=None, revision_id="rev-2")  # type: ignore[arg-type]
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](
            where_it_stands="dinner is on", base_revision_id="rev-1",
        ))

    await session(behave, head=head)
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "stale_base"


async def test_remind_writes_the_proactive_row_the_fire_path_fires_and_can_move_and_cancel_it():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="ask", text="ask where dinner is", subject_rows=["101"],
            at=iso(NOW + 3_600_000),
        ))
        answers.append(await by["remind"](
            intent="ask", text="again", subject_rows=["101"], at=iso(NOW + 3_600_000),
        ))
        answers.append(await by["remind_move"](
            reminder_id=answers[0]["reminder_id"], at=iso(NOW + 7_200_000), why="later",
        ))
        answers.append(await by["remind_cancel"](
            reminder_id=answers[2]["reminder_id"], why="settled",
        ))

    stores, _ = await session(behave)
    assert answers[0]["status"] == "ok"
    assert answers[1]["reason"] == "duplicate_pending"
    assert answers[2]["status"] == "ok" and answers[3]["status"] == "ok"
    rows = list(stores.reminders._rows.values())
    assert {r.kind for r in rows} == {"proactive"}
    assert {r.requester_person_id for r in rows} == {"nora"}
    assert {(r.payload or {}).get("by") for r in rows} == {PROVENANCE}
    assert rows[0].text == "ask where dinner is"
    assert all(r.state is ReminderState.CANCELLED for r in rows)


async def test_a_note_linked_remind_carries_the_note_and_follows_a_merge_and_a_retire():
    stores = Stores()
    stores.notes = InMemoryNoteStore(reminders=stores.reminders)
    survivor = await _seed_note(stores, "dinner")
    duplicate = await _seed_note(stores, "dinner place")
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="ask", text="ask where dinner is",
            subject_rows=[f"note:{duplicate.id}", "101"], at=iso(NOW + 3_600_000),
        ))
        row = next(iter(stores.reminders._rows.values()))
        assert row.payload["note_id"] == duplicate.id
        assert row.payload["note_title"] == "dinner place"
        answers.append(await by["note_merge"](
            survivor_id=survivor.id, duplicate_id=duplicate.id, body=GOOD_BODY,
        ))
        assert row.state is ReminderState.PENDING
        answers.append(await by["note_retire"](note_id=survivor.id, reason="settled"))

    await session(behave, stores=stores)
    assert [a["status"] for a in answers] == ["ok", "ok", "ok"]
    (row,) = stores.reminders._rows.values()
    assert row.payload["note_id"] == survivor.id
    assert row.state is ReminderState.CANCELLED and row.outcome == "cancelled_note_retired"


async def test_a_remind_citing_a_note_by_its_bare_shown_id_links_it_and_dies_with_it():
    """The agenda shows a note as `<note n="<id>">`, so the model cites the bare
    id. That row must link the note: a second row on the same note is refused, and
    retiring the note cancels the first. Unlinked, every row on a note survives
    its retire and fires on a settled premise."""
    stores = Stores()
    stores.notes = InMemoryNoteStore(reminders=stores.reminders)
    note = await _seed_note(stores, "dinner")
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="ask", text="ask how many are coming",
            subject_rows=["101", note.id], at=iso(NOW + 3_600_000),
        ))
        answers.append(await by["remind"](
            intent="ask", text="ask the headcount again",
            subject_rows=[note.id, "102"], at=iso(NOW + 7_200_000),
        ))
        answers.append(await by["note_retire"](note_id=note.id, reason="settled"))

    await session(behave, stores=stores)
    assert [a["status"] for a in answers] == ["ok", "refused", "ok"]
    assert answers[1]["reason"] == "duplicate_pending"
    (row,) = stores.reminders._rows.values()
    assert row.payload["note_id"] == note.id
    assert row.state is ReminderState.CANCELLED and row.outcome == "cancelled_note_retired"


async def test_a_bracketed_note_id_links_too():
    stores = Stores()
    stores.notes = InMemoryNoteStore(reminders=stores.reminders)
    note = await _seed_note(stores, "dinner")
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="ask", text="ask how many are coming",
            subject_rows=["101", f" [{note.id}]"], at=iso(NOW + 3_600_000),
        ))

    await session(behave, stores=stores)
    assert answers[0]["status"] == "ok"
    (row,) = stores.reminders._rows.values()
    assert row.payload["note_id"] == note.id


async def test_a_move_that_loses_the_race_to_the_heartbeat_is_refused_and_writes_nothing():
    """The heartbeat claims the row between the move's read and its cancel. The
    row is firing, so a moved copy would say the thing twice; and on a linked
    row it would also collide with the note's one open slot."""
    stores = Stores()
    stores.notes = InMemoryNoteStore(reminders=stores.reminders)
    note = await _seed_note(stores, "dinner")
    answers: list[Any] = []
    cancel = stores.reminders.cancel

    async def claimed_first(reminder_id: Any, *, conversation_id: str) -> bool:
        await stores.reminders.claim(reminder_id, now=now_utc(NOW))
        return await cancel(reminder_id, conversation_id=conversation_id)

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="ask", text="ask how many are coming",
            subject_rows=[note.id], at=iso(NOW + 3_600_000),
        ))
        stores.reminders.cancel = claimed_first  # type: ignore[method-assign]
        answers.append(await by["remind_move"](
            reminder_id=answers[0]["reminder_id"], at=iso(NOW + 7_200_000), why="later",
        ))

    await session(behave, stores=stores)
    assert answers[0]["status"] == "ok"
    assert answers[1] == {**answers[1], "status": "refused", "reason": "already_due"}
    (row,) = stores.reminders._rows.values()
    assert row.state is ReminderState.FIRING


def _note(note_id: str, title: str) -> Note:
    return Note(
        id=note_id, unit="u", title=title, description="d", uncertainty="u",
        closing_condition="c", established_in="room-a", created_at=NOW,
        head_revision_id=f"h-{note_id}", anchor_entity_ids=("Ada",),
    )


def test_two_titles_sharing_most_of_their_words_are_likely_duplicates():
    twins = likely_duplicates([
        _note("n1", "Saturday dinner place"), _note("n2", "dinner place Saturday?"),
        _note("n3", "hiking trip"),
    ])
    assert twins == {"n1": "n2", "n2": "n1"}


def test_the_daily_floor_is_a_notebook_review_and_the_index_marks_what_code_noticed():
    floor = Stimulus(
        id=7, unit="u", kind="time", item="floor", ref="floor:07:00@2026-10-03",
        at_ms=NOW, recorded_at_ms=NOW,
    )
    (item,) = build_agenda([floor], home_room=A).items
    assert item.kind == "time" and "notebook" in item.summary
    grounding = Grounding(notes=(
        NoteLine(id="n1", title="dinner", description="d", review="anchor_passed",
                 may_duplicate="n2"),
        NoteLine(id="n2", title="dinner place", description="d"),
    ))
    text = render_agenda(
        [AgendaItem(kind="time", room=Room("signal", "room-a"), summary=item.summary,
                    stimulus_ids=(7,), cues=(), surprise=0.0)],
        stimuli={7: floor}, grounding=grounding, now_ms=NOW,
    )
    assert 'review="anchor_passed"' in text and 'may_duplicate="n2"' in text


async def test_the_upcoming_reader_names_each_room_once_inside_the_horizon():
    """DEC-303: the wake evaluator's look-ahead seam names a room holding an
    event that starts between now and `days` ahead, once and by its primary id,
    whichever id form the event is stored under; a past event, one beyond the
    horizon, one with no time, and a room Nora may not reach unasked name
    nothing."""
    from datetime import UTC
    from datetime import datetime as _dt

    from nora.app.cognition_wiring import upcoming_reader

    day = 86_400_000

    class _E:
        def __init__(self, start_ms):
            self.when = None if start_ms is None else _dt.fromtimestamp(start_ms / 1000, UTC)

    by_id = {
        "room-a": [_E(NOW + day)],
        "room-a-old": [_E(NOW + 2 * day)],
        "room-b": [_E(NOW - day), _E(NOW + 5 * day), _E(None)],
        "room-c": [_E(NOW + day)],
    }

    async def events(conversation_id):
        return by_id.get(conversation_id, [])

    rooms = [
        ("signal", "room-a", {"room-a", "room-a-old"}),
        ("signal", "room-b", {"room-b"}),
        ("signal", "room-c", {"room-c"}),
        ("signal", "room-d", {"room-d", "room-a-old"}),
    ]
    read = upcoming_reader(
        events, lambda unit: rooms, days=3,
        room_proactive=lambda platform, cid: cid != "room-c",
    )
    assert list(await read("unit-1", NOW)) == [("signal", "room-a"), ("signal", "room-d")]
