from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nora.app.gatherings import GatheringService
from nora.domain.gatherings import RSVP, RSVPResponse
from nora.testing.gatherings import FakeGatheringStore
from tests.builders import make_gathering

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _service(store: FakeGatheringStore) -> GatheringService:
    return GatheringService(store=store)


# ── updating/RSVPing a CANCELLED event must refuse, not re-arm ──────────────


async def test_list_events_for_person_includes_created_and_rsvpd():
    """Also proves list_events_for_person is NOT conversation-scoped —
    "theirs" lives in a different conversation than "mine" and still
    shows up, since miles RSVP'd to it."""
    store = FakeGatheringStore()
    svc = _service(store)
    mine = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="Dinner",
    ))
    theirs = await store.create(make_gathering(
        conversation_id="discord:group-2",
        created_by="daisy",
        title="Movie",
    ))
    await store.add_rsvp(RSVP(
        event_id=theirs.id,
        person_id="miles",
        response=RSVPResponse.YES,
        responded_at=NOW,
    ))
    unrelated = await store.create(make_gathering(
        conversation_id="discord:group-2",
        created_by="daisy",
        title="Gym",
    ))

    out = await svc.list_events_for_person(person_id="miles")
    assert {e.id for e in out} == {mine.id, theirs.id}
    assert unrelated.id not in {e.id for e in out}


async def test_get_event_for_person_returns_event_when_creator():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="Dinner",
    ))
    out = await svc.get_event_for_person(event_id=ev.id, person_id="miles")
    assert out is not None
    assert out.id == ev.id


async def test_get_event_for_person_returns_event_when_rsvpd():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="daisy",
        title="Dinner",
    ))
    await store.add_rsvp(RSVP(
        event_id=ev.id,
        person_id="miles",
        response=RSVPResponse.MAYBE,
        responded_at=NOW,
    ))
    out = await svc.get_event_for_person(event_id=ev.id, person_id="miles")
    assert out is not None


async def test_get_event_for_person_returns_none_when_not_a_participant():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="daisy",
        title="Dinner",
    ))
    out = await svc.get_event_for_person(event_id=ev.id, person_id="miles")
    assert out is None


async def test_get_event_for_person_returns_an_event_of_a_room_i_am_in():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="daisy",
        title="Dinner",
    ))
    out = await svc.get_event_for_person(
        event_id=ev.id, person_id="miles", conversation_ids=frozenset({"signal:group-1"}),
    )
    assert out is not None and out.id == ev.id
    other = await svc.get_event_for_person(
        event_id=ev.id, person_id="miles", conversation_ids=frozenset({"signal:group-2"}),
    )
    assert other is None


async def test_get_event_for_person_returns_none_for_unknown_event():
    store = FakeGatheringStore()
    svc = _service(store)
    out = await svc.get_event_for_person(event_id=uuid.uuid4(), person_id="miles")
    assert out is None
