from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.app.gatherings import GatheringService
from nora.domain.gatherings import RSVPResponse
from nora.testing.gatherings import FakeGatheringStore
from nora.web.gatherings import build_gathering_payload, build_gatherings_payload

pytestmark = pytest.mark.asyncio


async def _service() -> GatheringService:
    return GatheringService(
        store=FakeGatheringStore(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, tzinfo=UTC),
    )


async def test_build_gatherings_payload_lists_events_for_person():
    svc = await _service()
    await svc.create_event(
        conversation_id="signal:group-1",
        requester_person_id="miles",
        title="Dinner",
        platform="signal",
    )
    out = await build_gatherings_payload(events=svc, person_id="miles")
    assert len(out) == 1
    assert out[0]["title"] == "Dinner"
    assert out[0]["status"] == "proposed"


async def test_build_gatherings_payload_excludes_events_im_not_part_of():
    svc = await _service()
    await svc.create_event(
        conversation_id="signal:group-1",
        requester_person_id="daisy",
        title="Someone else's dinner",
        platform="signal",
    )
    out = await build_gatherings_payload(events=svc, person_id="miles")
    assert out == []


async def test_build_gathering_payload_returns_none_for_missing_event():
    svc = await _service()
    assert (
        await build_gathering_payload(
            events=svc,
            event_id=uuid4(),
            person_id="miles",
        )
        is None
    )


async def test_build_gathering_payload_returns_none_when_not_a_participant():
    svc = await _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1",
        requester_person_id="daisy",
        title="Someone else's dinner",
        platform="signal",
    )
    out = await build_gathering_payload(events=svc, event_id=ev.id, person_id="miles")
    assert out is None


async def test_build_gathering_payload_includes_rsvps():
    svc = await _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1",
        requester_person_id="miles",
        title="Dinner",
        platform="signal",
    )
    await svc.rsvp(
        event_id=ev.id,
        conversation_id="signal:group-1",
        person_id="daisy",
        response=RSVPResponse.YES,
    )

    out = await build_gathering_payload(events=svc, event_id=ev.id, person_id="miles")
    assert out["rsvps"] == [{"person_id": "daisy", "response": "yes"}]
