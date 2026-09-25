"""`SqlNoteStore.merge_into` resolves BOTH notes' open proactive reminders
onto the survivor's single slot, inside the fold's own transaction (issue
#158 residue, PR #517's own gap).

TRUE OF POSTGRES ONLY, so this cannot live in
`tests/contract/test_note_store_contract.py`: that suite parametrizes over
`InMemoryNoteStore` too, and the in-memory double has no `reminders` dict at
all (see `nora/testing/note_store.py`'s `merge_into` docstring for why that
gap is left named rather than closed). `uq_reminders_proactive_open_note`
(migration 0064) is a real Postgres index with no in-memory equivalent —
these are exactly the facts the contract suite is structurally unable to ask.

THE DEFECT THIS GUARDS AGAINST: the index keys on the RAW note id a
proactive row's `payload` carries. Before this fix, folding two notes that
each held an open row left both rows alone — neither row's OWN note id ever
collided, so the index never objected — and both then forwarded to the
survivor through `nora.app.proactive_screen`'s `successor()` chase and both
fired. Reachable only under `[notes] merge_mode = "on"`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.notes import Note, StaleNoteHeadError
from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.reminders import SqlReminderStore

WS = "friends"
T0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
NOW_MS = int(T0.timestamp() * 1000)


@pytest.fixture
async def stores(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    notes = SqlNoteStore(engine)
    reminders = SqlReminderStore(engine)
    yield notes, reminders
    await reminders.aclose()


async def _make_note(store: SqlNoteStore, *, title: str, now_ms: int = NOW_MS) -> Note:
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
    notes: SqlNoteStore, *, survivor: Note, duplicate: Note,
    survivor_head: str | None = None, now_ms: int = NOW_MS + 1_000,
) -> None:
    await notes.merge_into(
        unit=WS, survivor_id=survivor.id,
        survivor_head=survivor_head or survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
        reset_strikes=True, established_by="dream:note-merge:friends:1",
        now_ms=now_ms,
    )


async def test_only_the_duplicate_has_an_open_row_is_repointed(stores) -> None:
    notes, reminders = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    await _fold(notes, survivor=survivor, duplicate=duplicate)

    got = await reminders.get(row.id)
    assert got is not None
    assert got.state is ReminderState.PENDING, "nothing was cancelled"
    assert got.outcome is None
    assert got.payload is not None
    assert got.payload["note_id"] == survivor.id, "re-pointed to the survivor"
    assert got.payload["revision_id"] == duplicate.head_revision_id, (
        "only note_id changes; the rest of the payload is untouched"
    )


async def test_only_the_survivor_has_an_open_row_is_untouched(stores) -> None:
    notes, reminders = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(survivor, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    await _fold(notes, survivor=survivor, duplicate=duplicate)

    got = await reminders.get(row.id)
    assert got is not None
    assert got.state is ReminderState.PENDING
    assert got.outcome is None
    assert got.payload is not None
    assert got.payload["note_id"] == survivor.id


async def test_both_open_rows_resolve_to_one_earliest_due_on_the_survivor(
    stores,
) -> None:
    """Both notes held an open row: after the fold there is exactly ONE open
    row, on the survivor, keeping the earliest `due_at`; the other is
    cancelled naming why.

    Falsification: skip the resolution entirely (comment out the call in
    `merge_into`) and this fails on `open_rows` — two rows survive, one still
    pointing at the retired duplicate — which is the exact production defect
    (issue #158 residue)."""
    notes, reminders = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=2))
    duplicate_row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)

    await _fold(notes, survivor=survivor, duplicate=duplicate)

    kept, cancelled = await reminders.get(duplicate_row.id), await reminders.get(
        survivor_row.id
    )
    assert kept is not None and cancelled is not None
    assert kept.state is ReminderState.PENDING, "the earliest due_at survives"
    assert kept.outcome is None
    assert kept.payload is not None and kept.payload["note_id"] == survivor.id
    assert cancelled.state is ReminderState.CANCELLED
    assert cancelled.outcome == "cancelled_duplicate_note"

    open_rows = [
        r for r in (kept, cancelled) if r.state is ReminderState.PENDING
    ]
    assert len(open_rows) == 1, "exactly one open row after the merge"


async def test_a_firing_row_is_never_cancelled_for_a_pending_one(stores) -> None:
    """The survivor's row is `pending` and due FIRST; the duplicate's is
    `firing` and due LATER. Earliest-`due_at` alone would keep the pending
    row and cancel the one being spoken right now — the double-send
    `sweep_firing`'s own docstring exists to prevent. The firing row must
    survive regardless of due order."""
    notes, reminders = stores
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
    assert firing is not None and pending is not None
    assert firing.state is ReminderState.FIRING, "never given up for a pending row"
    assert firing.payload is not None and firing.payload["note_id"] == survivor.id
    assert pending.state is ReminderState.CANCELLED
    assert pending.outcome == "cancelled_duplicate_note"


async def test_merge_rollback_leaves_reminders_untouched(stores) -> None:
    """A head moved under the fold: `merge_into` raises and the whole
    transaction rolls back. Both proactive rows must be exactly as they were
    — no re-point, no cancel — because the reminder half runs INSIDE the same
    transaction as the two note swaps."""
    notes, reminders = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=2))
    duplicate_row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)

    # The survivor's head moved under the pass.
    await notes.append_revision(
        unit=WS, note_id=survivor.id, body="Ada booked it",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=NOW_MS + 10,
    )

    with pytest.raises(StaleNoteHeadError):
        await _fold(notes, survivor=survivor, duplicate=duplicate)

    still_survivor = await reminders.get(survivor_row.id)
    still_duplicate = await reminders.get(duplicate_row.id)
    assert still_survivor is not None and still_duplicate is not None
    assert still_survivor.state is ReminderState.PENDING
    assert still_survivor.outcome is None
    assert still_survivor.payload is not None
    assert still_survivor.payload["note_id"] == survivor.id
    assert still_duplicate.state is ReminderState.PENDING
    assert still_duplicate.outcome is None
    assert still_duplicate.payload is not None
    assert still_duplicate.payload["note_id"] == duplicate.id
