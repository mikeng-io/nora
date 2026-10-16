from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.app.gatherings import GatheringService
from nora.domain.gatherings import Gathering, RSVPResponse
from nora.testing.gatherings import FakeGatheringStore
from nora.web.gatherings import build_gathering_payload, build_gatherings_payload

pytestmark = pytest.mark.asyncio


def _ROOMS(person_id: str) -> frozenset[tuple[str, str]]:
    return frozenset({("signal", "group-1")}) if person_id == "miles" else frozenset()


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


async def test_an_event_in_a_room_i_am_in_is_shown_under_either_id_form():
    """Cognition records an event as `cognition:<unit>`, with no RSVPs; a
    member of its room sees it, by the room's bare id or its `platform:` form,
    in the list and in the detail read. A room they are not in stays hidden."""
    svc = await _service()
    bare = await svc.create_event(
        conversation_id="group-1", requester_person_id="cognition:u",
        title="Saturday dinner", platform="signal",
    )
    prefixed = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="cognition:u",
        title="Sunday lunch", platform="signal",
    )
    await svc.create_event(
        conversation_id="group-2", requester_person_id="cognition:u",
        title="Another room's dinner", platform="signal",
    )
    asked: list[tuple[str, str, str]] = []

    def shown(event: Gathering, viewer: str, platform: str, cid: str) -> bool:
        asked.append((viewer, platform, cid))
        return True

    out = await build_gatherings_payload(
        events=svc, person_id="miles", rooms_of=_ROOMS, shown=shown,
    )
    assert sorted(e["title"] for e in out) == ["Saturday dinner", "Sunday lunch"]
    assert set(asked) == {("miles", "signal", "group-1")}
    for ev in (bare, prefixed):
        detail = await build_gathering_payload(
            events=svc, event_id=ev.id, person_id="miles", rooms_of=_ROOMS, shown=shown,
        )
        assert detail is not None and detail["id"] == str(ev.id)
    assert await build_gatherings_payload(
        events=svc, person_id="daisy", rooms_of=_ROOMS, shown=shown,
    ) == []


async def test_a_room_event_the_rule_withholds_is_absent_from_the_list_and_the_detail():
    svc = await _service()
    ev = await svc.create_event(
        conversation_id="group-1", requester_person_id="cognition:u",
        title="Surprise party", platform="signal",
    )

    def never(event: Gathering, viewer: str, platform: str, cid: str) -> bool:
        return False

    assert await build_gatherings_payload(
        events=svc, person_id="miles", rooms_of=_ROOMS, shown=never,
    ) == []
    assert await build_gathering_payload(
        events=svc, event_id=ev.id, person_id="miles", rooms_of=_ROOMS, shown=never,
    ) is None


async def test_without_a_rule_no_room_only_event_is_shown():
    """Fail closed: rooms without the visibility rule list nothing extra."""
    svc = await _service()
    await svc.create_event(
        conversation_id="group-1", requester_person_id="cognition:u",
        title="Saturday dinner", platform="signal",
    )
    assert await build_gatherings_payload(events=svc, person_id="miles", rooms_of=_ROOMS) == []


async def test_a_surprise_is_never_shown_to_the_person_it_is_kept_from():
    from dataclasses import replace

    from nora.agent.lineage import LineagePolicy
    from nora.app.gatherings import room_event_shown

    svc = await _service()
    ev = await svc.create_event(
        conversation_id="group-1", requester_person_id="cognition:u",
        title="Party", platform="signal",
    )
    surprise = replace(ev, exclude_persons=("miles",))
    off = LineagePolicy()
    assert room_event_shown(off, ev, "miles", "signal", "group-1")
    assert not room_event_shown(off, surprise, "miles", "signal", "group-1")
    assert room_event_shown(off, surprise, "daisy", "signal", "group-1")
