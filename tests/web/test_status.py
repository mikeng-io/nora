from datetime import UTC, datetime
from uuid import uuid4

from nora.domain.reminders import Reminder, ReminderState
from nora.testing import FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.status import build_status_payload
from tests.fakes import FakeHoncho


def _reminder(due_at: datetime) -> Reminder:
    return Reminder(
        id=uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="g1", conversation_type="group",
        text="a private sentence that must never reach the web API",
        created_at=due_at, due_at=due_at, state=ReminderState.PENDING,
        fired_at=None, outcome=None,
    )


async def test_reports_uptime_from_started_at():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["uptime_seconds"] > 0


async def test_reports_store_and_honcho_reachability_when_healthy():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["store"] == {"reachable": True}
    assert payload["honcho"] == {"reachable": True}


async def test_reports_honcho_unreachable_without_raising():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(fail=True), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["honcho"] == {"reachable": False}


async def test_reminders_summary_has_count_and_earliest_due_at_only():
    store = FakeReminderStore()
    earlier = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)
    later = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
    await store.create(_reminder(later))
    await store.create(_reminder(earlier))

    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=store, started_at=0.0,
    )

    assert payload["reminders"] == {"count": 2, "next_due_at": "2026-08-18T09:00:00+00:00"}
    assert "text" not in str(payload)
    assert "private sentence" not in str(payload)


async def test_reminders_summary_when_none_pending():
    payload = await build_status_payload(
        store=FakeRecordStore(), honcho=FakeHoncho(), reminders=FakeReminderStore(),
        started_at=0.0,
    )
    assert payload["reminders"] == {"count": 0, "next_due_at": None}
