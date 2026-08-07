"""Every ReminderStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.reminders import Reminder, ReminderState, ReminderStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.reminders import SqlReminderStore
from nora.testing.reminders import FakeReminderStore

T0 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=1)


def _row(**kw) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles", "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group", "text": "洗衫",
        "created_at": T0, "due_at": T0 + timedelta(minutes=40),
        "state": ReminderState.PENDING, "fired_at": None, "outcome": None,
    }
    base.update(kw)
    return Reminder(**base)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s: ReminderStore = SqlReminderStore(create_sql_engine(pg_url))
    else:
        s = FakeReminderStore()
    yield s
    await s.aclose()


async def test_a_created_row_is_returned_once_due(store):
    r = _row()
    await store.create(r)
    assert await store.due(now=T0, limit=10) == []
    due = await store.due(now=r.due_at, limit=10)
    assert [x.id for x in due] == [r.id]


async def test_due_is_ordered_by_due_at(store):
    late = _row(due_at=T0 + timedelta(hours=2))
    early = _row(due_at=T0 + timedelta(hours=1))
    await store.create(late)
    await store.create(early)
    due = await store.due(now=T0 + timedelta(hours=3), limit=10)
    assert [x.id for x in due] == [early.id, late.id]


async def test_only_pending_rows_are_due(store):
    for state in (ReminderState.DONE, ReminderState.CANCELLED,
                  ReminderState.FAILED, ReminderState.FIRING):
        await store.create(_row(state=state))
    assert await store.due(now=LATER, limit=10) == []


async def test_mark_moves_state_and_reports_the_change(store):
    r = _row()
    await store.create(r)
    assert await store.mark(r.id, ReminderState.DONE,
                            fired_at=T0, outcome="sent") is True
    assert (await store.get(r.id)).state is ReminderState.DONE
    assert await store.mark(uuid.uuid4(), ReminderState.DONE) is False


async def test_cancel_is_scoped_to_the_conversation(store):
    r = _row(conversation_id="conv-1")
    await store.create(r)
    assert await store.cancel(r.id, conversation_id="conv-OTHER") is False
    assert await store.cancel(r.id, conversation_id="conv-1") is True
    assert (await store.get(r.id)).state is ReminderState.CANCELLED


async def test_pending_for_is_scoped_and_excludes_finished(store):
    mine = _row(conversation_id="conv-1")
    theirs = _row(conversation_id="conv-2")
    done = _row(conversation_id="conv-1", state=ReminderState.DONE)
    for r in (mine, theirs, done):
        await store.create(r)
    assert [x.id for x in await store.pending_for("conv-1")] == [mine.id]


async def test_count_pending_is_per_requester(store):
    await store.create(_row(requester_person_id="miles"))
    await store.create(_row(requester_person_id="miles"))
    await store.create(_row(requester_person_id="tyler"))
    assert await store.count_pending("miles") == 2
    assert await store.count_pending("tyler") == 1


async def test_sweep_resolves_firing_rows_to_failed(store):
    """§8.2: at-most-once. A crash mid-fire loses that reminder, visibly. It is
    NEVER re-queued — re-queueing double-sends anything that crashed after
    egress, and nothing links a row to its delivered message.

    The assertion reads the TERMINAL STATE via get(). An earlier draft asserted
    only that the row vanished from due(), which stays green if the sweep moves
    FIRING to anything at all — decorative, per CLAUDE.md."""
    stuck = _row(state=ReminderState.FIRING)
    ok = _row(state=ReminderState.PENDING)
    await store.create(stuck)
    await store.create(ok)

    assert await store.sweep_firing() == 1

    swept = await store.get(stuck.id)
    assert swept is not None
    assert swept.state is ReminderState.FAILED
    assert swept.outcome == "failed_error"
    assert (await store.get(ok.id)).state is ReminderState.PENDING
