"""`InMemoryNoteStore.merge_into` models the reminders half too,
when a `FakeReminderStore` is wired in, so `tests/unit/test_note_merge.py`'s
coverage of `NoteMergeAdaptor` can see reminders re-pointed. This is that
store's own contract, run directly against
`InMemoryNoteStore` — the sibling of `tests/infra/
test_note_merge_repoints_reminders.py`, which runs the identical scenarios
against `SqlNoteStore` and real Postgres. Two implementations, one rule,
same as everywhere else this store is tested.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.notes import Note, NoteRevision, StaleNoteHeadError
from nora.domain.reminders import Reminder, ReminderState
from nora.testing.note_store import InMemoryNoteStore
from nora.testing.reminders import FakeReminderStore

WS = "friends"
T0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
NOW_MS = int(T0.timestamp() * 1000)


async def _make_note(store: InMemoryNoteStore, *, title: str, now_ms: int = NOW_MS) -> Note:
    return await store.create(
        unit=WS, title=title, description="venue open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="conv-1", body="Saturday; venue open.",
        established_by="tag:miles", now_ms=now_ms, anchor_date=20260927,
    )


def _proactive_row(
    note: Note, *, due_at: datetime, state: ReminderState = ReminderState.PENDING,
    created_at: datetime = T0,
) -> Reminder:
    return Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="conv-1", conversation_type="group",
        text="ask where the dinner is", created_at=created_at, due_at=due_at,
        state=state, fired_at=None, outcome=None, kind="proactive",
        payload={
            "note_id": note.id, "revision_id": note.head_revision_id,
            "note_title": note.title, "intent": "ask where they are",
        },
    )


async def _fold(
    notes: InMemoryNoteStore, *, survivor: Note, duplicate: Note,
    now_ms: int = NOW_MS + 1_000,
) -> NoteRevision:
    return await notes.merge_into(
        unit=WS, survivor_id=survivor.id, survivor_head=survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
        reset_strikes=True, established_by="dream:note-merge:friends:1",
        now_ms=now_ms,
    )


async def test_reminders_none_is_the_default_and_skips_the_step() -> None:
    """No `reminders` wired: `merge_into` still folds the notes and simply
    has nothing to say about reminders; reminders are an explicit, opt-in `None`."""
    notes = InMemoryNoteStore()
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")

    rev = await _fold(notes, survivor=survivor, duplicate=duplicate)

    assert notes.notes[duplicate.id].retired_at is not None
    assert rev.note_id == survivor.id


async def test_only_the_duplicate_has_an_open_row_is_repointed() -> None:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders)
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    fold = await _fold(notes, survivor=survivor, duplicate=duplicate)

    got = await reminders.get(row.id)
    assert got is not None
    assert got.state is ReminderState.PENDING
    assert got.payload is not None
    assert got.payload["note_id"] == survivor.id
    assert got.payload["revision_id"] == fold.id, (
        "the same blocker the SQL twin guards: revision_id must move too"
    )


async def test_both_open_rows_resolve_to_one_earliest_due_on_the_survivor() -> None:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders)
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=2))
    duplicate_row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)
    outcomes: list[str] = []

    await notes.merge_into(
        unit=WS, survivor_id=survivor.id, survivor_head=survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
        reset_strikes=True, established_by="dream:note-merge:friends:1",
        now_ms=NOW_MS + 1_000, on_reminder_outcome=outcomes.append,
    )

    kept = await reminders.get(duplicate_row.id)
    cancelled = await reminders.get(survivor_row.id)
    assert kept is not None and cancelled is not None
    assert kept.state is ReminderState.PENDING
    assert kept.payload is not None and kept.payload["note_id"] == survivor.id
    assert cancelled.state is ReminderState.CANCELLED
    assert cancelled.outcome == "cancelled_duplicate_note"
    assert outcomes == ["collision_repointed"]


async def test_a_firing_row_is_never_cancelled_for_a_pending_one() -> None:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders)
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(minutes=5))
    duplicate_row = _proactive_row(
        duplicate, due_at=T0 + timedelta(hours=3), state=ReminderState.FIRING,
    )
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)

    await _fold(notes, survivor=survivor, duplicate=duplicate)

    firing = await reminders.get(duplicate_row.id)
    pending = await reminders.get(survivor_row.id)
    assert firing is not None and firing.state is ReminderState.FIRING
    assert pending is not None and pending.state is ReminderState.CANCELLED


async def test_both_rows_firing_neither_is_cancelled() -> None:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders)
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(
        survivor, due_at=T0 + timedelta(hours=2), state=ReminderState.FIRING,
    )
    duplicate_row = _proactive_row(
        duplicate, due_at=T0 + timedelta(hours=1), state=ReminderState.FIRING,
    )
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)

    outcomes: list[str] = []
    await notes.merge_into(
        unit=WS, survivor_id=survivor.id, survivor_head=survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
        reset_strikes=True, established_by="dream:note-merge:friends:1",
        now_ms=NOW_MS + 1_000, on_reminder_outcome=outcomes.append,
    )

    still_firing_loser = await reminders.get(survivor_row.id)
    still_firing_keeper = await reminders.get(duplicate_row.id)
    assert still_firing_loser is not None
    assert still_firing_loser.state is ReminderState.FIRING
    assert still_firing_loser.outcome is None
    assert still_firing_loser.payload is not None
    assert still_firing_loser.payload["note_id"] == survivor.id
    assert still_firing_keeper is not None
    assert still_firing_keeper.state is ReminderState.FIRING
    assert still_firing_keeper.payload is not None
    assert still_firing_keeper.payload["note_id"] == duplicate.id, (
        "untouched, not re-pointed — repointing it would model a note "
        "holding two open reminders at once, the exact shape the real "
        "unique index refuses"
    )
    assert outcomes == ["skipped_firing_collision"]


async def test_a_repointed_row_is_cancelled_moved_when_the_survivor_moved_first() -> None:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders)
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    moved = await notes.append_revision(
        unit=WS, note_id=survivor.id, body="Ada booked it",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=NOW_MS + 500,
    )

    await notes.merge_into(
        unit=WS, survivor_id=survivor.id, survivor_head=moved.id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
        reset_strikes=True, established_by="dream:note-merge:friends:1",
        now_ms=NOW_MS + 1_000,
    )

    resolved = await reminders.get(row.id)
    assert resolved is not None
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome == "cancelled_note_moved"
    assert resolved.payload is not None
    assert resolved.payload["note_id"] == duplicate.id


async def test_merge_rollback_leaves_reminders_untouched() -> None:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders)
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    with pytest.raises(StaleNoteHeadError):
        await notes.merge_into(
            unit=WS, survivor_id=survivor.id,
            survivor_head="00000000-0000-4000-8000-000000000000",
            duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
            body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
            reset_strikes=True, established_by="dream:note-merge:friends:1",
            now_ms=NOW_MS + 1_000,
        )

    untouched = await reminders.get(row.id)
    assert untouched is not None
    assert untouched.state is ReminderState.PENDING
    assert untouched.payload is not None
    assert untouched.payload["note_id"] == duplicate.id
