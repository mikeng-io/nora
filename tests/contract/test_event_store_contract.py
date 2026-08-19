"""Every EventStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nora.domain.events import (
    RSVP,
    DuplicateEventError,
    Event,
    EventStatus,
    RSVPResponse,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.events import SqlEventStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.events import FakeEventStore

pytestmark = pytest.mark.asyncio


def _event(**overrides) -> Event:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    defaults = {
        "id": uuid4(),
        "conversation_id": "signal:group-1",
        "title": "Dinner",
        "created_by": "miles",
        "when": now + timedelta(days=3),
        "venue_text": None,
        "venue_place_id": None,
        "venue_name": None,
        "venue_address": None,
        "venue_rating": None,
        "venue_price_level": "",
        "venue_maps_url": None,
        "venue_resolved_at": None,
        "status": EventStatus.PROPOSED,
        "reminder_id": None,
        "created_at": now,
    }
    defaults.update(overrides)
    return Event(**defaults)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlEventStore(create_sql_engine(pg_url))
    else:
        s = FakeEventStore()
    yield s
    await s.aclose()


async def test_create_then_get_round_trips(store):
    ev = _event()
    await store.create(ev)
    back = await store.get(ev.id)
    assert back is not None
    assert back.id == ev.id
    assert back.title == ev.title
    assert back.when == ev.when


async def test_get_missing_returns_none(store):
    assert await store.get(uuid4()) is None


async def test_duplicate_create_raises(store):
    ev = _event()
    await store.create(ev)
    with pytest.raises(DuplicateEventError):
        await store.create(ev)


async def test_stored_datetimes_come_back_utc_aware(store):
    ev = _event()
    await store.create(ev)
    back = await store.get(ev.id)
    assert back.when.tzinfo is not None
    assert back.created_at.tzinfo is not None


async def test_list_for_conversation_filters_by_status(store):
    proposed = _event(conversation_id="signal:group-2", status=EventStatus.PROPOSED)
    cancelled = _event(conversation_id="signal:group-2", status=EventStatus.CANCELLED)
    await store.create(proposed)
    await store.create(cancelled)

    only_proposed = await store.list_for_conversation(
        conversation_id="signal:group-2", status=EventStatus.PROPOSED,
    )
    assert [e.id for e in only_proposed] == [proposed.id]

    both = await store.list_for_conversation(conversation_id="signal:group-2")
    assert {e.id for e in both} == {proposed.id, cancelled.id}


async def test_list_for_conversation_scopes_to_conversation(store):
    here = _event(conversation_id="signal:group-3")
    elsewhere = _event(conversation_id="signal:group-4")
    await store.create(here)
    await store.create(elsewhere)

    out = await store.list_for_conversation(conversation_id="signal:group-3")
    assert [e.id for e in out] == [here.id]


async def test_update_persists_changes(store):
    ev = _event()
    await store.create(ev)
    from dataclasses import replace

    updated = replace(ev, status=EventStatus.CONFIRMED, venue_text="Soi 29")
    await store.update(updated)

    back = await store.get(ev.id)
    assert back.status == EventStatus.CONFIRMED
    assert back.venue_text == "Soi 29"


async def test_rsvp_then_rsvps_for_round_trips(store):
    ev = _event()
    await store.create(ev)
    r = RSVP(
        event_id=ev.id, person_id="miles",
        response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
    )
    await store.add_rsvp(r)

    back = await store.rsvps_for(ev.id)
    assert len(back) == 1
    assert back[0].person_id == "miles"
    assert back[0].response is RSVPResponse.YES


async def test_rsvp_upserts_on_same_person(store):
    ev = _event()
    await store.create(ev)
    first = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)
    second = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)

    await store.add_rsvp(
        RSVP(event_id=ev.id, person_id="miles",
             response=RSVPResponse.MAYBE, responded_at=first),
    )
    await store.add_rsvp(
        RSVP(event_id=ev.id, person_id="miles",
             response=RSVPResponse.YES, responded_at=second),
    )

    back = await store.rsvps_for(ev.id)
    assert len(back) == 1
    assert back[0].response is RSVPResponse.YES
    assert back[0].responded_at == second
