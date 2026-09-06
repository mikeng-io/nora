from datetime import UTC, datetime
from uuid import uuid4

from nora.domain.reminders import Reminder, ReminderState
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.status import build_status_payload


def _reminder(
    due_at: datetime, *, platform: str = "signal", conversation_id: str = "g1",
) -> Reminder:
    return Reminder(
        id=uuid4(), requester_person_id="miles", platform=platform,
        conversation_id=conversation_id, conversation_type="group",
        text="a private sentence that must never reach the web API",
        created_at=due_at, due_at=due_at, state=ReminderState.PENDING,
        fired_at=None, outcome=None,
    )


async def test_reports_uptime_from_started_at():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["uptime_seconds"] > 0


async def test_reports_store_and_honcho_reachability_when_healthy():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["store"] == {"reachable": True}
    assert payload["honcho"] == {"reachable": True}


async def test_reports_honcho_unreachable_without_raising():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(fail=True), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["honcho"] == {"reachable": False}


async def test_reminders_summary_has_count_and_earliest_due_at():
    store = FakeReminderStore()
    earlier = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    later = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
    await store.create(_reminder(later))
    await store.create(_reminder(earlier))

    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=store, started_at=0.0,
    )

    assert payload["reminders"]["count"] == 2
    assert payload["reminders"]["next_due_at"] == "2026-08-18T09:00:00+00:00"
    assert "text" not in str(payload)
    assert "private sentence" not in str(payload)
    assert "requester_person_id" not in str(payload)
    assert "miles" not in str(payload)


async def test_reminders_upcoming_is_ordered_by_due_at_with_platform_and_room():
    store = FakeReminderStore()
    earlier = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    later = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
    await store.create(_reminder(later, platform="discord", conversation_id="d1"))
    await store.create(_reminder(earlier, platform="signal", conversation_id="g1"))

    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=store, started_at=0.0,
    )

    assert payload["reminders"]["upcoming"] == [
        {"due_at": "2026-08-18T09:00:00+00:00", "platform": "signal", "conversation_id": "g1"},
        {"due_at": "2026-08-18T15:00:00+00:00", "platform": "discord", "conversation_id": "d1"},
    ]


async def test_reminders_upcoming_is_capped_at_ten():
    store = FakeReminderStore()
    for hour in range(12):
        await store.create(_reminder(datetime(2026, 8, 18, hour, tzinfo=UTC)))

    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=store, started_at=0.0,
    )

    assert payload["reminders"]["count"] == 12
    assert len(payload["reminders"]["upcoming"]) == 10


async def test_reminders_summary_when_none_pending():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["reminders"] == {"count": 0, "next_due_at": None, "upcoming": []}
