"""The retained legacy note screen, exercised without the retired scheduler."""

from datetime import UTC, datetime
from uuid import uuid4

from nora.app.proactive_screen import ProactiveScreen
from nora.domain.notes import NoteSupersedeReason
from nora.domain.reminders import Reminder, ReminderState
from nora.policy import Conversation, Policy
from nora.testing import InMemoryNoteStore

UNIT, ROOM = "generated-unit", "generated-room"
AT = datetime(2026, 8, 8, 19, tzinfo=UTC)


def _screen(notes, *, enabled=True, unit=UNIT):
    room = Conversation(
        "signal", frozenset({ROOM}), frozenset(), unit=unit, primary_id=ROOM, proactive=enabled,
    )
    return ProactiveScreen(policy=Policy((room,), _by_id={("signal", ROOM): room}), notes=notes)


async def _note(notes, title="Generated note", now=1000):
    return await notes.create(
        unit=UNIT, title=title, description="Where should we meet?", uncertainty="Venue",
        closing_condition="A venue is named", established_in=ROOM, body="The venue is open.",
        established_by="tag:fixture", now_ms=now, anchor_date=20260808,
    )


def _row(note):
    return Reminder(
        id=uuid4(), requester_person_id="nora", platform="signal", conversation_id=ROOM,
        conversation_type="group", text="Ask for a venue", created_at=AT, due_at=AT,
        state=ReminderState.PENDING, fired_at=None, outcome=None, kind="proactive",
        payload={"note_id": note.id, "revision_id": note.head_revision_id},
    )


async def test_the_room_flag_is_the_legacy_proactive_grant():
    notes = InMemoryNoteStore()
    row = _row(await _note(notes))
    assert await _screen(notes).refusal(row) is None
    assert await _screen(notes, enabled=False).refusal(row) == (
        ReminderState.FAILED, "failed_policy",
    )


async def test_a_retired_note_cancels_the_row():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    row = _row(note)
    assert await _screen(notes).refusal(row) is None
    await notes.supersede(
        key=UNIT, target=note.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=2000,
    )
    assert await _screen(notes).refusal(row) == (ReminderState.CANCELLED, "cancelled_note_retired")


async def test_a_tag_path_revision_since_the_link_cancels_the_row():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    row = _row(note)
    revision = await notes.append_revision(
        unit=UNIT, note_id=note.id, body="Still open", parent_revision_id=note.head_revision_id,
        established_by="dream:fixture", now_ms=2000,
    )
    assert await _screen(notes).refusal(row) is None
    await notes.append_revision(
        unit=UNIT, note_id=note.id, body="Venue named", parent_revision_id=revision.id,
        established_by="tag:fixture", now_ms=3000,
    )
    assert await _screen(notes).refusal(row) == (ReminderState.CANCELLED, "cancelled_note_moved")


async def test_a_linked_row_with_no_note_store_fails_closed():
    notes = InMemoryNoteStore()
    row = _row(await _note(notes))
    assert await _screen(notes).refusal(row) is None
    assert await _screen(None).refusal(row) == (ReminderState.FAILED, "failed_error")


async def test_a_note_the_rows_unit_cannot_reach_fails_closed():
    notes = InMemoryNoteStore()
    row = _row(await _note(notes))
    assert await _screen(notes).refusal(row) is None
    assert await _screen(notes, unit="other-unit").refusal(row) == (
        ReminderState.FAILED, "failed_error",
    )


async def test_the_legacy_screen_follows_a_merge_to_its_current_survivor():
    notes = InMemoryNoteStore()
    survivor = await _note(notes, title="Generated survivor", now=500)
    duplicate = await _note(notes)
    row = _row(duplicate)
    merged = await notes.merge_into(
        unit=UNIT, survivor_id=survivor.id, survivor_head=survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="The merged venue is open", anchor_date=20260808, revisit_at=2000,
        reset_strikes=True, established_by="dream:fixture", now_ms=2000,
    )
    assert merged is not None
    assert await _screen(notes).refusal(row) is None
    await notes.supersede(
        key=UNIT, target=survivor.id, replaced_by=None,
        because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=3000,
    )
    assert await _screen(notes).refusal(row) == (ReminderState.CANCELLED, "cancelled_note_retired")
