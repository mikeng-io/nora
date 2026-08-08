"""Every ReminderStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nora.domain.reminders import (
    DuplicateReminderError,
    NaiveDatetimeError,
    Reminder,
    ReminderState,
    ReminderStore,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.reminders import SqlReminderStore
from nora.testing.reminders import FakeReminderStore

T0 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=1)

# The repo's own downstream clock is +08:00 (`nora.agent.tools._HKT`), so an
# offset that is NOT UTC is the realistic input, not an exotic one.
HKT = timezone(timedelta(hours=8), "HKT")


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


async def test_claim_is_a_compare_and_set_from_pending(store):
    """`claim` is the ONE transition the heartbeat must not lose a race on.

    `mark` moves a row by id alone, whatever state it is in — which is right for
    closing out a row the tick already owns, and wrong for TAKING one. `due()`
    is a query, not a claim, and a turn awaits: between the batch being read and
    a row being fired, a human may cancel it. With `mark` doing the take, that
    cancel was overwritten (CANCELLED -> FIRING) and the reminder was spoken
    anyway; the caller's `if not moved: already resolved` branch could never run
    because `UPDATE ... WHERE id = :id` always matches a row that still exists,
    and nothing deletes reminders.

    So: True only from PENDING, and the second caller of the same row gets
    False with the row untouched.
    """
    r = _row()
    await store.create(r)

    assert await store.claim(r.id) is True
    assert (await store.get(r.id)).state is ReminderState.FIRING
    # Idempotence is not the point — EXCLUSIVITY is. The second claim loses.
    assert await store.claim(r.id) is False

    # Every non-PENDING state refuses, and the row is left exactly as it was.
    for state in (ReminderState.CANCELLED, ReminderState.DONE,
                  ReminderState.FAILED, ReminderState.FIRING):
        other = _row(state=state)
        await store.create(other)
        assert await store.claim(other.id) is False, state
        assert (await store.get(other.id)).state is state

    assert await store.claim(uuid.uuid4()) is False


async def test_a_cancel_beats_a_later_claim(store):
    """The race from the heartbeat's side, end to end: a row selected by `due()`
    and cancelled before the tick gets to it is never fired."""
    r = _row()
    await store.create(r)
    selected = await store.due(now=LATER, limit=10)
    assert [x.id for x in selected] == [r.id]

    assert await store.cancel(r.id, conversation_id="conv-1") is True
    assert await store.claim(r.id) is False
    assert (await store.get(r.id)).state is ReminderState.CANCELLED


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


# --------------------------------------------------------------------------
# The four divergences the original eight tests could not see. Each one below
# failed on at least one implementation before its fix; between them they pin
# the timezone contract, the `limit` contract, the ordering tiebreak, and what
# a duplicate id does.
# --------------------------------------------------------------------------


async def test_stored_datetimes_come_back_utc_aware(store):
    """UTC-AWARE IS THE CONTRACT, on the way in and on the way out.

    A `timestamptz` column normalises to UTC and asyncpg hands the value back
    with `tzinfo=UTC`; an in-memory dict hands back exactly the object it was
    given. Both are "correct" instants, so `==` compares equal and cannot see
    the difference — `utcoffset()` is what differs, and it is what downstream
    formatting reads. Feed a +08:00 aware row (this repo's own clock, `_HKT`)
    and the two stores previously disagreed by eight hours' worth of printed
    digits for the same instant.
    """
    due_hkt = datetime(2026, 8, 9, 17, 0, tzinfo=HKT)
    created_hkt = datetime(2026, 8, 8, 20, 0, tzinfo=HKT)
    r = _row(created_at=created_hkt, due_at=due_hkt)
    await store.create(r)

    got = await store.get(r.id)
    assert got is not None
    for label, value in (("due_at", got.due_at), ("created_at", got.created_at)):
        assert value.utcoffset() == timedelta(0), f"{label} is not UTC-normalised"
    # Same instant, and the digits a downstream formatter would print are UTC's.
    assert got.due_at == due_hkt
    assert got.due_at.hour == 9

    # Every read path, not just get(): due(), pending_for(), and the fired_at
    # stamp a mark() writes.
    (via_due,) = await store.due(now=LATER, limit=10)
    assert via_due.due_at.utcoffset() == timedelta(0)
    (via_pending,) = await store.pending_for("conv-1")
    assert via_pending.due_at.utcoffset() == timedelta(0)

    await store.mark(r.id, ReminderState.DONE,
                     fired_at=datetime(2026, 8, 9, 17, 5, tzinfo=HKT), outcome="sent")
    marked = await store.get(r.id)
    assert marked is not None
    assert marked.fired_at is not None
    assert marked.fired_at.utcoffset() == timedelta(0)
    assert marked.fired_at.hour == 9


async def test_naive_datetimes_are_refused_rather_than_guessed(store):
    """A naive datetime has no answer, so neither store may invent one.

    asyncpg would read it in the CLIENT HOST's zone (a deployment fact, not a
    domain one) and the fake would store it as-is and then raise `TypeError`
    from `naive <= aware` inside `due()`. Both are guesses dressed as answers;
    `NaiveDatetimeError` is the contract instead, on every datetime that
    crosses the port.
    """
    naive = datetime(2026, 8, 9, 9, 0)
    assert naive.tzinfo is None

    with pytest.raises(NaiveDatetimeError):
        await store.create(_row(due_at=naive))
    with pytest.raises(NaiveDatetimeError):
        await store.create(_row(created_at=naive))

    ok = _row()
    await store.create(ok)
    with pytest.raises(NaiveDatetimeError):
        await store.due(now=naive, limit=10)
    with pytest.raises(NaiveDatetimeError):
        await store.mark(ok.id, ReminderState.DONE, fired_at=naive)


async def test_due_refuses_a_non_positive_limit(store):
    """`limit` is a batch size; a non-positive one is a caller bug.

    Postgres raises `InvalidRowCountInLimitClauseError` on `LIMIT -1` while
    Python's `[:-1]` cheerfully returns everything-except-the-last — the two
    stores answered a bad call two different ways, and one of them answered it
    with plausible-looking data. One rule now: `limit >= 1` or `ValueError`.
    """
    await store.create(_row(due_at=T0))
    await store.create(_row(due_at=T0))

    for bad in (-1, 0):
        with pytest.raises(ValueError):
            await store.due(now=LATER, limit=bad)

    assert len(await store.due(now=LATER, limit=1)) == 1


async def test_equal_due_at_is_broken_deterministically_by_id(store):
    """Two reminders for "9am" is ordinary, not exotic.

    `ORDER BY due_at` alone leaves ties to the heap's physical order, which an
    ordinary `mark()` UPDATE reshuffles by moving a tuple; the fake leant on
    Python's stable sort over dict insertion order. Same rows, two orders. With
    `limit`, a tie straddling the batch boundary makes the BATCH's composition
    unspecified too — which is why the third assertion takes `limit=2`.

    `id` is the tiebreak: Postgres compares `uuid` bytewise and Python's
    `UUID.__lt__` compares `.int` (the same 16 bytes, big-endian), so the two
    orderings are the same ordering.
    """
    same = T0 + timedelta(minutes=40)
    ids = sorted(uuid.uuid4() for _ in range(3))
    # Insert in an order that is neither id order nor its reverse.
    for rid in (ids[1], ids[2], ids[0]):
        await store.create(_row(id=rid, due_at=same))

    # An ordinary state move, then a move back: enough to relocate a heap tuple
    # in Postgres, and the exact operation that reordered a real batch.
    await store.mark(ids[1], ReminderState.FIRING)
    await store.mark(ids[1], ReminderState.PENDING)

    assert [x.id for x in await store.due(now=LATER, limit=10)] == ids
    assert [x.id for x in await store.pending_for("conv-1")] == ids
    assert [x.id for x in await store.due(now=LATER, limit=2)] == ids[:2]


async def test_create_refuses_a_duplicate_id(store):
    """The same id twice is a collision, and a collision must be loud.

    Postgres raised `UniqueViolationError` off the primary key; the fake
    silently OVERWROTE the row, so a test that exercised the fake would show a
    clobbered reminder as success and the real store as a 500. `create` is
    insert-only in both now, and the first row is left untouched.
    """
    first = _row(text="洗衫")
    await store.create(first)

    with pytest.raises(DuplicateReminderError):
        await store.create(_row(id=first.id, text="買餸"))

    survivor = await store.get(first.id)
    assert survivor is not None
    assert survivor.text == "洗衫"
