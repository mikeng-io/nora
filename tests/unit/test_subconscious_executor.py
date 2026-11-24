"""Canonical minute tick arithmetic for timed work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.ticks import (
    due_tick_of_at,
    due_tick_of_ms,
    tick_of_at,
    tick_of_ms,
    tick_start_ms,
)


def test_a_deadline_rounds_up_and_the_boundary_is_its_own_tick():
    """Fake-clock boundaries: due at 03:30:50 is eligible from the 03:31 tick;
    due at 03:31:00 exactly is eligible IN 03:31. Upward rounding, never early."""
    # one second into a minute -> next tick
    assert due_tick_of_ms(60_001) == 2
    # exactly on the boundary -> that tick
    assert due_tick_of_ms(120_000) == 2
    # the read side floors
    assert tick_of_ms(119_999) == 1
    assert tick_of_ms(120_000) == 2


def test_datetime_and_ms_arithmetic_agree():
    at = datetime(2026, 8, 8, 12, 40, 0, tzinfo=UTC)
    assert tick_of_at(at) == tick_of_ms(120_000 * 0 + int(at.timestamp() * 1000))
    off = at + timedelta(seconds=30)
    assert due_tick_of_at(off) == due_tick_of_ms(int(off.timestamp() * 1000))
    assert tick_start_ms(tick_of_at(at)) == int(at.timestamp() * 1000)


def test_the_drivers_wait_to_a_cadence_boundary_not_a_fixed_interval():
    """`seconds_to_boundary` is what heartbeat, wake and dream loops sleep
    on: a wait that ends ON the next multiple of the interval in epoch time,
    so a slow pass advances phase rather than drifting it."""
    from nora.domain.ticks import seconds_to_boundary

    # 40s into the minute -> 20s to the boundary.
    assert seconds_to_boundary(60, at=1_800_000_040.0) == pytest.approx(20.0)
    # Exactly ON the boundary -> the full interval (sleeping 0 would busy-fire).
    assert seconds_to_boundary(60, at=1_800_000_000.0) == pytest.approx(60.0)
    # A fractional phase is preserved, not rounded.
    assert seconds_to_boundary(60, at=1_800_000_000.25) == pytest.approx(59.75)


async def test_the_fake_reminder_store_spells_the_same_tick_the_column_computes():
    """Reminder `due()` parity: a row due mid-tick is NOT due at its own
    instant, IS due at the next boundary; a boundary row is due in its tick."""
    import uuid

    from nora.domain.reminders import Reminder, ReminderState
    from nora.testing.reminders import FakeReminderStore

    t0 = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    store = FakeReminderStore()
    mid = Reminder(
        id=uuid.uuid4(), requester_person_id="p", platform="signal",
        conversation_id="c", conversation_type="group", text="mid",
        created_at=t0, due_at=t0 + timedelta(minutes=40, seconds=30),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    edge = Reminder(
        id=uuid.uuid4(), requester_person_id="p", platform="signal",
        conversation_id="c", conversation_type="group", text="edge",
        created_at=t0, due_at=t0 + timedelta(minutes=41),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    await store.create(mid)
    await store.create(edge)
    # At the mid row's own instant it is NOT due (upward rounding).
    assert [r.text for r in await store.due(now=mid.due_at, limit=10)] == []
    # At the next boundary both are: mid's tick is the 12:41 tick, edge's is too.
    boundary = t0 + timedelta(minutes=41)
    assert [r.text for r in await store.due(now=boundary, limit=10)] == ["mid", "edge"]
    # Just before the boundary, neither.
    assert await store.due(now=boundary - timedelta(milliseconds=1), limit=10) == []
    # Long overdue: still exactly the pending set.
    late = await store.due(now=t0 + timedelta(days=1), limit=10)
    assert {r.id for r in late} == {mid.id, edge.id}
    await store.aclose()
