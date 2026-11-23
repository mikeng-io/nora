from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nora.app.gatherings import GatheringService
from nora.domain.gatherings import (
    TERMINAL_STATUSES,
    Gathering,
    GatheringCancelledError,
    GatheringNotFoundError,
    GatheringStatus,
    RSVPResponse,
)
from nora.testing.gatherings import FakeGatheringStore
from tests.builders import make_gathering

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _service(store: FakeGatheringStore) -> GatheringService:
    return GatheringService(store=store, route_source=None, now=lambda: NOW)


async def test_rsvp_records_a_response():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="Dinner",
    ))
    r = await svc.rsvp(
        event_id=ev.id,
        conversation_id="signal:group-1", platform="signal",
        person_id="daisy",
        response=RSVPResponse.YES,
    )
    assert r.response is RSVPResponse.YES


async def test_list_events_filters_by_status():
    store = FakeGatheringStore()
    svc = _service(store)
    await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="A", status=GatheringStatus.CANCELLED,
    ))
    await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="B",
    ))

    proposed = await svc.list_events(
        conversation_id="signal:group-1",
        status=GatheringStatus.PROPOSED,
    )
    assert [e.title for e in proposed] == ["B"]


# ── updating/RSVPing a CANCELLED event must refuse, not re-arm ──────────────


async def test_rsvp_refuses_on_a_cancelled_event():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="Dinner", status=GatheringStatus.CANCELLED,
    ))

    with pytest.raises(GatheringCancelledError):
        await svc.rsvp(
            event_id=ev.id,
            conversation_id="signal:group-1", platform="signal",
            person_id="daisy",
            response=RSVPResponse.YES,
        )


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES, key=lambda s: s.value))
async def test_rsvp_refuses_on_every_terminal_status(terminal):
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="Dinner", status=terminal,
    ))

    with pytest.raises(GatheringCancelledError) as exc_info:
        await svc.rsvp(
            event_id=ev.id,
            conversation_id="signal:group-1", platform="signal",
            person_id="daisy",
            response=RSVPResponse.YES,
        )
    assert exc_info.value.status is terminal


async def test_rsvp_refuses_a_mismatched_conversation_id():
    store = FakeGatheringStore()
    svc = _service(store)
    ev = await store.create(make_gathering(
        conversation_id="signal:group-1",
        created_by="miles",
        title="Dinner",
    ))
    with pytest.raises(GatheringNotFoundError):
        await svc.rsvp(
            event_id=ev.id,
            conversation_id="signal:some-other-group", platform="signal",
            person_id="daisy",
            response=RSVPResponse.YES,
        )


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
    await svc.rsvp(
        event_id=theirs.id,
        conversation_id="discord:group-2", platform="signal",
        person_id="miles",
        response=RSVPResponse.YES,
    )
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
    await svc.rsvp(
        event_id=ev.id,
        conversation_id="signal:group-1", platform="signal",
        person_id="miles",
        response=RSVPResponse.MAYBE,
    )
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


def _legacy_event(*, conversation_id: str, title: str) -> Gathering:
    """A pre-migration row with no stored platform or unit."""
    return make_gathering(
        conversation_id=conversation_id, title=title, created_by="miles", platform="",
    )


async def test_a_legacy_row_is_quarantined_from_room_and_unit_reads():
    """A row with no stored platform has no durable owner. Current policy is
    not evidence of what the policy was when it was written, so it is shown to
    NEITHER a room read nor a unit-wide search, not to whoever asks now."""
    store = FakeGatheringStore()
    legacy = await store.create(_legacy_event(
        conversation_id="signal:room-a", title="Pre-migration dinner",
    ))
    svc = _service(store)

    assert not await svc.list_events(
        conversation_id="signal:room-a", unit_id="friends", platform="signal",
    )
    assert legacy.id not in {
        e.id for e in await svc.search_events(
            unit_id="friends", conversation_id="signal:room-a", platform="signal",
        )
    }


async def test_a_row_with_a_persisted_platform_is_listed_and_searchable():
    """The positive control: a row written with its owner is visible to that
    room and to the unit-wide search, so the quarantine above is not a read that
    simply never works."""
    store = FakeGatheringStore()
    svc = _service(store)
    event = await store.create(make_gathering(
        conversation_id="signal:room-a", created_by="miles",
        title="Dinner", unit_id="friends",
    ))

    assert [e.id for e in await svc.list_events(
        conversation_id="signal:room-a", unit_id="friends", platform="signal",
    )] == [event.id]
    assert event.id in {
        e.id for e in await svc.search_events(
            unit_id="friends", conversation_id="signal:room-z", platform="signal",
        )
    }


async def test_a_room_moved_to_another_unit_does_not_take_its_old_events():
    """The historical reassignment: a row written by `work` stays `work`'s when
    `policy.toml` later gives the same room id to `friends`. `friends` neither
    reads nor mutates it, and `work` still does — the row's own persisted
    owner, not the current assignment, decides."""
    store = FakeGatheringStore()
    work = _service(store)
    event = await store.create(make_gathering(
        conversation_id="signal:room-a", created_by="miles",
        title="Work dinner", unit_id="work",
    ))
    friends = _service(store)

    assert not await friends.list_events(
        conversation_id="signal:room-a", unit_id="friends", platform="signal",
    )
    assert event.id not in {
        e.id for e in await friends.search_events(
            unit_id="friends", conversation_id="signal:room-a", platform="signal",
        )
    }
    with pytest.raises(GatheringNotFoundError):
        await friends.rsvp(
            event_id=event.id, conversation_id="signal:room-a",
            platform="signal", unit_id="friends",
            person_id="miles", response=RSVPResponse.YES,
        )
    assert (await store.get(event.id)).unit_id == "work"
    assert [e.id for e in await work.list_events(
        conversation_id="signal:room-a", unit_id="work", platform="signal",
    )] == [event.id]


async def test_rsvp_refuses_the_same_room_id_on_another_platform():
    """A platform move is a room move: the conversation string alone never
    authorizes a write. The row keeps its persisted platform, and the same
    room id on whatsapp cannot RSVP to it."""
    store = FakeGatheringStore()
    event = await store.create(make_gathering(
        conversation_id="signal:room-a", created_by="miles",
        title="Work dinner", unit_id="work",
    ))
    svc = _service(store)
    with pytest.raises(GatheringNotFoundError):
        await svc.rsvp(
            event_id=event.id, conversation_id="signal:room-a",
            platform="whatsapp", unit_id="work",
            person_id="miles", response=RSVPResponse.YES,
        )
    assert (await store.get(event.id)).platform == "signal"


async def test_rsvp_refuses_another_units_event_with_the_same_room_id():
    """The row's own `unit_id` is authoritative: a foreign unit's id never
    passes because the conversation string happens to match."""
    store = FakeGatheringStore()
    foreign = await store.create(make_gathering(
        conversation_id="signal:room-a", created_by="miles",
        title="Work dinner", unit_id="work",
    ))
    svc = _service(store)
    with pytest.raises(GatheringNotFoundError):
        await svc.rsvp(
            event_id=foreign.id, conversation_id="signal:room-a",
            platform="signal", unit_id="friends",
            person_id="miles", response=RSVPResponse.YES,
        )
    assert (await store.get(foreign.id)).unit_id == "work"


async def test_rsvp_refuses_a_row_with_no_stored_platform():
    """A `unit_id=""`/`platform=""` row is quarantined for WRITES too: there is
    no persisted owner to prove it is this caller's, so it refuses like an
    unknown id rather than letting the first asker mutate it."""
    store = FakeGatheringStore()
    legacy = await store.create(_legacy_event(
        conversation_id="signal:room-a", title="Pre-migration dinner",
    ))
    svc = _service(store)
    with pytest.raises(GatheringNotFoundError):
        await svc.rsvp(
            event_id=legacy.id, conversation_id="signal:room-a",
            platform="signal", unit_id="friends",
            person_id="miles", response=RSVPResponse.YES,
        )
    assert not await store.rsvps_for(legacy.id)


async def test_an_omitted_platform_is_not_a_wildcard_on_a_mutation():
    """An omitted platform fails closed for an RSVP to a Signal event."""
    store = FakeGatheringStore()
    svc = _service(store)
    event = await store.create(make_gathering(
        conversation_id="signal:room-a", created_by="miles",
        title="Dinner", unit_id="work",
    ))
    with pytest.raises(GatheringNotFoundError):
        await svc.rsvp(
            event_id=event.id, conversation_id="signal:room-a",
            unit_id="work", person_id="miles", response=RSVPResponse.YES,
        )
