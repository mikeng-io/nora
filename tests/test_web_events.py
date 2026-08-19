from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.app.events import EventService
from nora.domain.events import RSVPResponse
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


async def test_build_events_payload_lists_events_for_person():
    svc = await _service()
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await build_events_payload(events=svc, person_id="miles")
    assert len(out) == 1
    assert out[0]["title"] == "Dinner"
    assert out[0]["status"] == "proposed"


async def test_build_events_payload_excludes_events_im_not_part_of():
    svc = await _service()
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="daisy",
        title="Someone else's dinner",
    )
    out = await build_events_payload(events=svc, person_id="miles")
    assert out == []


async def test_build_event_payload_returns_none_for_missing_event():
    svc = await _service()
    assert await build_event_payload(
        events=svc, event_id=uuid4(), person_id="miles",
    ) is None


async def test_build_event_payload_returns_none_when_not_a_participant():
    svc = await _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="daisy",
        title="Someone else's dinner",
    )
    out = await build_event_payload(events=svc, event_id=ev.id, person_id="miles")
    assert out is None


async def test_build_event_payload_includes_rsvps():
    svc = await _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    await svc.rsvp(
        event_id=ev.id, conversation_id="signal:group-1",
        person_id="daisy", response=RSVPResponse.YES,
    )

    out = await build_event_payload(events=svc, event_id=ev.id, person_id="miles")
    assert out["rsvps"] == [{"person_id": "daisy", "response": "yes"}]
