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


# ── migration 0074_event_revision: event_revision, lifecycle statuses,
# base_revision_id, RSVP transition rows, unit-wide search. ──────────────────


async def test_create_writes_one_revision_and_points_the_head_at_it(store):
    ev = _event()
    await store.create(ev)

    back = await store.get(ev.id)
    assert back.head_revision_id is not None

    revisions = await store.revisions_for(ev.id)
    assert len(revisions) == 1
    assert revisions[0].id == back.head_revision_id
    assert revisions[0].parent_revision_id is None
    assert revisions[0].title == ev.title


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


async def test_rsvp_is_stored_append_only_even_though_the_read_projects_latest(store):
    """RSVP transition rows: a repeat RSVP must be a second row in
    storage, not an overwrite of the first, even though `rsvps_for` (the
    read side) still answers with only the current one (the test above)."""
    ev = _event()
    await store.create(ev)
    first = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)
    second = datetime(2026, 8, 19, 14, 0, tzinfo=UTC)
    await store.add_rsvp(RSVP(
        event_id=ev.id, person_id="miles", response=RSVPResponse.MAYBE,
        responded_at=first,
    ))
    await store.add_rsvp(RSVP(
        event_id=ev.id, person_id="miles", response=RSVPResponse.YES,
        responded_at=second,
    ))

    if isinstance(store, FakeGatheringStore):
        assert len(store._rsvps) == 2
    else:
        from sqlalchemy import text
        async with store._engine.connect() as conn:
            count = (await conn.execute(
                text("SELECT count(*) FROM event_rsvps WHERE event_id = :id"),
                {"id": ev.id},
            )).scalar_one()
        assert count == 2


async def test_search_is_unit_wide_and_filters_by_place_person_and_interval(store):
    unit = f"unit-{uuid4()}"
    in_room_a = _event(
        id=uuid4(), conversation_id="signal:room-a", unit_id=unit,
        title="Dinner", venue_name="Soi 29", created_by="miles",
        when=datetime(2026, 8, 22, 20, 0, tzinfo=UTC),
    )
    in_room_b = _event(
        id=uuid4(), conversation_id="discord:room-b", unit_id=unit,
        title="Lunch", venue_name="Elsewhere", created_by="daisy",
        when=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )
    other_unit = _event(id=uuid4(), unit_id=f"other-{uuid4()}", title="Not this unit")
    await store.create(in_room_a)
    await store.create(in_room_b)
    await store.create(other_unit)

    everything = await store.search(unit_id=unit)
    assert {e.id for e in everything} == {in_room_a.id, in_room_b.id}

    by_place = await store.search(unit_id=unit, place="soi")
    assert [e.id for e in by_place] == [in_room_a.id]

    by_person = await store.search(unit_id=unit, person="daisy")
    assert [e.id for e in by_person] == [in_room_b.id]

    by_interval = await store.search(
        unit_id=unit, start=datetime(2026, 8, 23, 0, 0, tzinfo=UTC),
    )
    assert [e.id for e in by_interval] == [in_room_b.id]


async def test_search_room_ids_finds_legacy_events_without_leaking_other_units(store):
    """The migration backfills every pre-existing event to `unit_id=""` (units
    live in `policy.toml`, not the DB, so the migration cannot fill it).
    `room_ids` is how the caller — which resolves the unit's rooms from policy
    — gets those rows back into a unit-wide search. The negative matters as
    much as the positive: a legacy row in a room the caller did NOT name
    (another unit's room) must never be returned, or `room_ids` becomes a
    cross-unit leak."""
    unit = f"unit-{uuid4()}"
    legacy_in_unit = _event(
        id=uuid4(), conversation_id="signal:room-a", unit_id="",
        title="Pre-migration dinner",
    )
    legacy_in_other_unit = _event(
        id=uuid4(), conversation_id="discord:room-z", unit_id="",
        title="Someone else's pre-migration dinner",
    )
    backfilled = _event(
        id=uuid4(), conversation_id="signal:room-b", unit_id=unit,
        title="Post-migration dinner",
    )
    await store.create(legacy_in_unit)
    await store.create(legacy_in_other_unit)
    await store.create(backfilled)

    found = await store.search(unit_id=unit, room_ids=("signal:room-a", "signal:room-b"))
    assert {e.id for e in found} == {legacy_in_unit.id, backfilled.id}
    assert legacy_in_other_unit.id not in {e.id for e in found}, (
        "a legacy unit_id='' event in a room outside room_ids must never "
        "leak into another unit's search"
    )

    # No room_ids at all: back to the exact unit_id match — a legacy
    # event is invisible unless the caller explicitly resolved its room.
    without_room_ids = await store.search(unit_id=unit)
    assert {e.id for e in without_room_ids} == {backfilled.id}

