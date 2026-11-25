"""Every GatheringStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from nora.domain.gatherings import (
    RSVP,
    Gathering,
    GatheringStatus,
    RSVPResponse,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.gatherings import FakeGatheringStore
from tests.fixtures.gatherings import SeededGatheringStore

pytestmark = pytest.mark.asyncio

# This repo's own downstream clock is +08:00 (`nora.agent.toolkit.clock._HKT`,
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
        "unit_id": "",
        "platform": "signal",
    }
    defaults.update(overrides)
    return Gathering(**defaults)


@pytest.fixture(params=["sql", "fake"])
async def store(_pg_engine_scope, request):
    if request.param == "sql":
        clean_pg_url = request.getfixturevalue("clean_pg_url")
        run_migrations(clean_pg_url)
        s = SeededGatheringStore(create_sql_engine(clean_pg_url))
    else:
        s = FakeGatheringStore()
    yield s
    await s.aclose()


async def test_get_reads_the_seeded_event(store):
    ev = _event()
    await store.create(ev)
    back = await store.get(ev.id)
    assert back is not None
    assert back.id == ev.id
    assert back.title == ev.title
    assert back.when == ev.when


async def test_get_missing_returns_none(store):
    assert await store.get(uuid4()) is None


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


async def test_a_rows_platform_round_trips_and_scopes_its_room_read(store):
    """The persisted platform is read back and is part of the room scope: the
    same conversation string on two platforms is two rooms, and a signal read
    does not see the whatsapp row."""
    signal_row = _event(
        conversation_id="signal:group-3", unit_id="u", platform="signal",
    )
    whatsapp_row = _event(
        conversation_id="signal:group-3", unit_id="u", platform="whatsapp",
    )
    await store.create(signal_row)
    await store.create(whatsapp_row)

    assert (await store.get(signal_row.id)).platform == "signal"
    signal_read = await store.list_for_conversation(
        conversation_id="signal:group-3", unit_id="u", platform="signal",
    )
    assert [e.id for e in signal_read] == [signal_row.id]
    whatsapp_read = await store.list_for_conversation(
        conversation_id="signal:group-3", unit_id="u", platform="whatsapp",
    )
    assert [e.id for e in whatsapp_read] == [whatsapp_row.id]


async def test_list_for_conversation_quarantines_a_row_with_no_stored_platform(store):
    """Both scoped and unfiltered reads quarantine rows without a durable owner."""
    unit = f"unit-{uuid4()}"
    unresolved = _event(conversation_id="signal:group-5", unit_id=unit, platform="")
    owned = _event(conversation_id="signal:group-5", unit_id=unit, platform="signal")
    await store.create(unresolved)
    await store.create(owned)

    scoped = await store.list_for_conversation(
        conversation_id="signal:group-5", unit_id=unit, platform="signal",
    )
    assert [e.id for e in scoped] == [owned.id]

    unfiltered = await store.list_for_conversation(
        conversation_id="signal:group-5", unit_id=unit, platform=None,
    )
    assert [e.id for e in unfiltered] == [owned.id]


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


async def test_list_for_person_includes_every_event_in_my_rooms(store):
    """An event recorded in a room the viewer is in shows though they neither
    made nor answered it; an event in a room they are not in still does not."""
    mine = _event(conversation_id="signal:group-9", created_by="cognition:u")
    elsewhere = _event(conversation_id="signal:group-10", created_by="cognition:u")
    await store.create(mine)
    await store.create(elsewhere)

    out = await store.list_for_person(
        person_id="miles", conversation_ids=frozenset({"signal:group-9"}),
    )
    assert [e.id for e in out] == [mine.id]


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


async def test_rsvp_reads_only_the_latest_response_per_person(store):
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


# ── migration 0074_event_revision: event_revision, lifecycle statuses,
# base_revision_id, RSVP transition rows, unit-wide search. ──────────────────


async def test_update_appends_a_revision_chained_to_the_old_head(store):
    from dataclasses import replace

    ev = _event()
    await store.create(ev)
    first_head = (await store.get(ev.id)).head_revision_id

    await store.update(replace(ev, status=GatheringStatus.CONFIRMED))

    back = await store.get(ev.id)
    assert back.head_revision_id != first_head
    revisions = await store.revisions_for(ev.id)
    assert len(revisions) == 2
    assert revisions[0].id == first_head
    assert revisions[1].id == back.head_revision_id
    assert revisions[1].parent_revision_id == first_head
    assert revisions[1].status is GatheringStatus.CONFIRMED


async def test_a_stale_base_revision_id_is_refused_with_the_current_row(store):
    from dataclasses import replace

    from nora.domain.gatherings import StaleEventRevisionError

    ev = _event()
    await store.create(ev)
    stale_id = uuid4()  # never the real head

    with pytest.raises(StaleEventRevisionError) as exc_info:
        await store.update(
            replace(ev, status=GatheringStatus.CONFIRMED), base_revision_id=stale_id,
        )
    assert exc_info.value.event_id == ev.id
    assert exc_info.value.current.status is GatheringStatus.PROPOSED  # unchanged

    # The real head_revision_id succeeds.
    real_head = (await store.get(ev.id)).head_revision_id
    await store.update(
        replace(ev, status=GatheringStatus.CONFIRMED), base_revision_id=real_head,
    )
    assert (await store.get(ev.id)).status is GatheringStatus.CONFIRMED


async def test_the_full_lifecycle_is_writable(store):
    from dataclasses import replace

    for status in (
        GatheringStatus.STAGED, GatheringStatus.PROPOSED, GatheringStatus.CONFIRMED,
        GatheringStatus.HAPPENED, GatheringStatus.LAPSED, GatheringStatus.EVICTED,
    ):
        ev = _event(id=uuid4(), status=status)
        await store.create(ev)
        back = await store.get(ev.id)
        assert back.status is status

    ev = _event(id=uuid4())
    await store.create(ev)
    await store.update(replace(ev, status=GatheringStatus.CANCELLED))
    assert (await store.get(ev.id)).status is GatheringStatus.CANCELLED
