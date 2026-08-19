from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.app.events import EventService
from nora.domain.reminders import ReminderCaps
from nora.testing.events import FakeEventStore
from nora.testing.reminders import FakeReminderStore
from nora.web.events import build_event_payload, build_events_payload

pytestmark = pytest.mark.asyncio


async def _service() -> EventService:
    return EventService(
        store=FakeEventStore(), reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(), route_source=None, honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, tzinfo=UTC),
    )


async def test_build_events_payload_lists_events_for_conversation():
    svc = await _service()
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await build_events_payload(events=svc, conversation_id="signal:group-1")
    assert len(out) == 1
    assert out[0]["title"] == "Dinner"
    assert out[0]["status"] == "proposed"


async def test_build_event_payload_returns_none_for_missing_event():
    from uuid import uuid4

    svc = await _service()
    assert await build_event_payload(events=svc, event_id=uuid4()) is None


async def test_build_event_payload_includes_rsvps():
    from nora.domain.events import RSVPResponse

    svc = await _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    await svc.rsvp(
        event_id=ev.id, conversation_id="signal:group-1",
        person_id="daisy", response=RSVPResponse.YES,
    )

    out = await build_event_payload(events=svc, event_id=ev.id)
    assert out["rsvps"] == [{"person_id": "daisy", "response": "yes"}]
