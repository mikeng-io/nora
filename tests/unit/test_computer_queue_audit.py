"""Queue lifecycle audit rows are standalone events, one per transition."""

from datetime import UTC, datetime
from uuid import uuid4

from nora.computer.wire import _computer_event_callbacks
from nora.domain.computer_tasks import ComputerTask
from nora.testing import FakeComputerEventStore

NOW = datetime(2026, 11, 27, 1, 0, tzinfo=UTC)


def _task() -> ComputerTask:
    return ComputerTask(
        id=uuid4(), scope="a1b2c3d4e5f6a7b8", kind="native", status="running",
        payload={}, origin={"platform": "whatsapp", "conversation_id": "room"},
        created_at=NOW, updated_at=NOW, started_at=NOW,
    )


async def test_queue_audit_records_each_transition_for_one_task() -> None:
    events = FakeComputerEventStore()
    audit, _ = _computer_event_callbacks(None, lambda: NOW, lambda: None, events)
    row = _task()

    await audit(row, "granted")
    await audit(row, "delivered")

    assert [event.status for event in events.events] == ["granted", "delivered"]
    assert [event.result["transition"] for event in events.events] == ["granted", "delivered"]
    assert all(event.result["task"] == str(row.id) for event in events.events)
    assert all(event.phase == "finished" for event in events.events)
