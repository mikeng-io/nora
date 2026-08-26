"""Every GatheringStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from nora.domain.gatherings import (
    RSVP,
    DuplicateGatheringError,
    Gathering,
    GatheringStatus,
    RSVPResponse,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.gatherings import FakeGatheringStore

pytestmark = pytest.mark.asyncio

# This repo's own downstream clock is +08:00 (`nora.agent.tools._HKT`,
# `ReminderCaps.timezone` default is Asia/Hong_Kong), so an offset that is
# NOT UTC is the realistic input, not an exotic one.
HKT = timezone(timedelta(hours=8), "HKT")


def _event(**overrides) -> Gathering:
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
        "status": GatheringStatus.PROPOSED,
        "reminder_id": None,
        "created_at": now,
    }
    defaults.update(overrides)
    return Gathering(**defaults)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlGatheringStore(create_sql_engine(pg_url))
    else:
        s = FakeGatheringStore()
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
    with pytest.raises(DuplicateGatheringError):
        await store.create(ev)


async def test_stored_datetimes_come_back_utc_aware(store):
    """UTC-AWARE IS THE CONTRACT, on the way in and on the way out.

    A `timestamptz` column normalises to UTC and asyncpg hands the value back
    with `tzinfo=UTC`; an in-memory dict hands back exactly the object it was
    given. Both are "correct" instants, so `==` compares equal and cannot see
    the difference — `utcoffset()` is what differs. Feed a +08:00 aware event
    (this repo's own clock) and a store that only tags tzinfo rather than
    normalising it would still pass a bare `tzinfo is not None` check while
    printing the wrong hour downstream.
    """
    when_hkt = datetime(2026, 8, 22, 20, 0, tzinfo=HKT)
    created_hkt = datetime(2026, 8, 19, 9, 0, tzinfo=HKT)
    ev = _event(when=when_hkt, created_at=created_hkt)
    await store.create(ev)

    back = await store.get(ev.id)
    assert back is not None
    for label, value in (("when", back.when), ("created_at", back.created_at)):
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0), f"{label} is not UTC-normalised"

    # Same instants, and the digits a downstream formatter would print are
    # UTC's: 20:00+08:00 -> 12:00 UTC, 09:00+08:00 -> 01:00 UTC.
    assert back.when == when_hkt
    assert back.when.hour == 12
    assert back.created_at == created_hkt
    assert back.created_at.hour == 1


async def test_list_for_conversation_filters_by_status(store):
    proposed = _event(conversation_id="signal:group-2", status=GatheringStatus.PROPOSED)
    cancelled = _event(conversation_id="signal:group-2", status=GatheringStatus.CANCELLED)
    await store.create(proposed)
    await store.create(cancelled)

    only_proposed = await store.list_for_conversation(
        conversation_id="signal:group-2", status=GatheringStatus.PROPOSED,
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


async def test_list_for_person_includes_events_i_created(store):
    mine = _event(conversation_id="signal:group-5", created_by="miles")
    someone_elses = _event(conversation_id="signal:group-5", created_by="daisy")
    await store.create(mine)
    await store.create(someone_elses)

    out = await store.list_for_person(person_id="miles")
    assert [e.id for e in out] == [mine.id]


async def test_list_for_person_includes_events_i_rsvpd_to(store):
    ev = _event(conversation_id="signal:group-6", created_by="daisy")
    await store.create(ev)
    await store.add_rsvp(RSVP(
        event_id=ev.id, person_id="miles", response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
    ))

    out = await store.list_for_person(person_id="miles")
    assert [e.id for e in out] == [ev.id]


async def test_list_for_person_excludes_events_im_not_part_of(store):
    ev = _event(conversation_id="signal:group-7", created_by="daisy")
    await store.create(ev)
    await store.add_rsvp(RSVP(
        event_id=ev.id, person_id="sam", response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
    ))

    out = await store.list_for_person(person_id="miles")
    assert out == []


async def test_list_for_person_deduplicates_creator_who_also_rsvpd(store):
    ev = _event(conversation_id="signal:group-8", created_by="miles")
    await store.create(ev)
    await store.add_rsvp(RSVP(
        event_id=ev.id, person_id="miles", response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, 13, 0, tzinfo=UTC),
    ))

    out = await store.list_for_person(person_id="miles")
    assert [e.id for e in out] == [ev.id]


async def test_update_persists_changes(store):
    ev = _event()
    await store.create(ev)
    from dataclasses import replace

    updated = replace(ev, status=GatheringStatus.CONFIRMED, venue_text="Soi 29")
    await store.update(updated)

    back = await store.get(ev.id)
    assert back.status == GatheringStatus.CONFIRMED
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
