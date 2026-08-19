# tests/test_events_service.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.events import AmbiguousOpenEventsError, EventService
from nora.domain.events import (
    Event,
    EventCancelledError,
    EventNotFoundError,
    EventStatus,
    RSVPResponse,
)
from nora.domain.reminders import ReminderCaps
from nora.testing.events import FakeEventStore
from nora.testing.reminders import FakeReminderStore

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


class _FakeRouteSource:
    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {
            "status": "ok", "name": "Soi 29 Thai Kitchen",
            "address": "29 Soi Rd, Central", "rating": 4.5,
            "price_level": "PRICE_LEVEL_MODERATE",
            "place_id": "ChIJfake123",
        }

    async def place_info(self, name: str) -> dict:
        return self._response


class _FakeHoncho:
    def __init__(self, answers: dict[str, str | None] | None = None) -> None:
        self._answers = answers or {}

    async def chat(self, *, peer_id, query, reasoning_level="low", target=None):
        return self._answers.get(target)


def _service(
    *, store=None, reminders=None, route_source=None, honcho=None,
) -> EventService:
    return EventService(
        store=store or FakeEventStore(),
        reminders=reminders or FakeReminderStore(),
        reminder_caps=ReminderCaps(),
        route_source=route_source or _FakeRouteSource(),
        honcho=honcho or _FakeHoncho(),
        ai_peer="nora",
        now=lambda: NOW,
    )


async def test_create_event_persists_and_returns_it():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    assert ev.title == "Dinner"
    assert ev.status is EventStatus.PROPOSED
    back = await svc.get_event(event_id=ev.id)
    assert back.id == ev.id


async def test_create_event_with_when_creates_a_linked_one_shot_reminder():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    when = NOW + timedelta(days=3)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when,
    )
    assert ev.reminder_id is not None
    row = await reminders.get(ev.reminder_id)
    assert row is not None
    assert row.due_at == when
    assert row.series is None  # never a recurrence — spec §4


async def test_update_event_resolves_venue_once_via_route_source():
    route_source = _FakeRouteSource()
    calls = []
    orig = route_source.place_info

    async def counting(name):
        calls.append(name)
        return await orig(name)

    route_source.place_info = counting  # type: ignore[method-assign]

    svc = _service(route_source=route_source)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(
        event_id=ev.id, conversation_id="signal:group-1", venue_text="Soi 29",
    )

    assert updated.venue_name == "Soi 29 Thai Kitchen"
    assert updated.venue_place_id == "ChIJfake123"
    assert updated.venue_rating == 4.5
    assert updated.venue_resolved_at == NOW
    # `maps_url` is synthesized by EventService itself (Fix 3) — the route
    # source never returns it — from the RESOLVED name, url-quoted.
    assert updated.venue_maps_url == (
        "https://www.google.com/maps/search/?api=1&query=Soi%2029%20Thai%20Kitchen"
    )
    assert calls == ["Soi 29"]  # resolved exactly once

    # Reading it back must NOT re-query the resolver.
    back = await svc.get_event(event_id=ev.id)
    assert back.venue_name == "Soi 29 Thai Kitchen"
    assert calls == ["Soi 29"]


async def test_venue_resolution_refuses_a_raw_coordinate():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(
        event_id=ev.id, conversation_id="signal:group-1",
        venue_text="22.2793,114.1628",
    )
    # Refused at the R-28 screen — venue_text is kept as what was said,
    # nothing gets resolved.
    assert updated.venue_text == "22.2793,114.1628"
    assert updated.venue_place_id is None


async def test_rsvp_records_a_response():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    r = await svc.rsvp(
        event_id=ev.id, conversation_id="signal:group-1",
        person_id="daisy", response=RSVPResponse.YES,
    )
    assert r.response is RSVPResponse.YES


async def test_cancel_event_cancels_its_linked_reminder():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    when = NOW + timedelta(days=3)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when,
    )
    cancelled = await svc.cancel_event(
        event_id=ev.id, conversation_id="signal:group-1",
    )
    assert cancelled.status is EventStatus.CANCELLED

    row = await reminders.get(ev.reminder_id)
    from nora.domain.reminders import ReminderState

    assert row.state is ReminderState.CANCELLED


async def test_list_events_filters_by_status():
    svc = _service()
    a = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="A",
    )
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="B",
    )
    await svc.cancel_event(event_id=a.id, conversation_id="signal:group-1")

    proposed = await svc.list_events(
        conversation_id="signal:group-1", status=EventStatus.PROPOSED,
    )
    assert [e.title for e in proposed] == ["B"]


async def test_suggest_venue_ranks_by_mention_and_fails_closed_when_empty():
    honcho = _FakeHoncho({"miles": "Soi 29", "daisy": "Soi 29", "sam": None})
    svc = _service(honcho=honcho)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await svc.suggest_venue(
        event_id=ev.id, conversation_id="signal:group-1",
        roster_person_ids=["miles", "daisy", "sam"],
    )
    assert out.status == "ok"
    assert out.candidates[0].venue_text == "Soi 29"
    assert out.candidates[0].source == "mention_frequency"
    assert out.route_hints is None  # reserved, always None — spec §5.1


async def test_suggest_venue_nothing_found_when_every_person_is_empty():
    honcho = _FakeHoncho({"miles": None, "daisy": None})
    svc = _service(honcho=honcho)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await svc.suggest_venue(
        event_id=ev.id, conversation_id="signal:group-1",
        roster_person_ids=["miles", "daisy"],
    )
    assert out.status == "nothing_found"
    assert out.candidates == ()


async def test_create_event_raises_when_two_open_events_already_exist():
    store = FakeEventStore()
    svc = _service(store=store)
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="A",
    )
    # Directly insert a second PROPOSED event, bypassing the doctrine check
    # the `create_event` TOOL is expected to have already run — this
    # method's own raise is a last-resort backstop, not the primary
    # mechanism (spec §3.1).
    second = Event(
        id=uuid.uuid4(),
        conversation_id="signal:group-1",
        title="B",
        created_by="miles",
        when=None,
        venue_text=None,
        venue_place_id=None,
        venue_name=None,
        venue_address=None,
        venue_rating=None,
        venue_price_level="",
        venue_maps_url=None,
        venue_resolved_at=None,
        status=EventStatus.PROPOSED,
        reminder_id=None,
        created_at=NOW,
    )
    await store.create(second)

    with pytest.raises(AmbiguousOpenEventsError):
        await svc.create_event(
            conversation_id="signal:group-1", requester_person_id="miles",
            title="C",
        )


async def test_create_event_beyond_horizon_creates_event_with_no_reminder():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    # ReminderCaps().max_horizon_days == 365. 365 days + 12 hours is past the
    # cap under a full-precision `timedelta` comparison even though the old
    # `.days`-floored check let it through (Fix 4's regression case).
    when = NOW + timedelta(days=365, hours=12)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when,
    )
    # The event itself is still created — R-5: never silently pretend a
    # reminder was set.
    assert ev.when == when
    assert ev.reminder_id is None
    # Never a stray reminder minted either.
    assert list(await reminders.pending_for("signal:group-1")) == []


async def test_update_event_reschedule_cancels_old_reminder_and_links_new_one():
    from nora.domain.reminders import ReminderState

    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    when1 = NOW + timedelta(days=3)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when1,
    )
    old_reminder_id = ev.reminder_id
    assert old_reminder_id is not None

    when2 = NOW + timedelta(days=5)
    updated = await svc.update_event(
        event_id=ev.id, conversation_id="signal:group-1", when=when2,
    )

    # A NEW reminder, pointing at the new due_at — never a silently orphaned
    # old one (Fix 1's regression case).
    assert updated.reminder_id is not None
    assert updated.reminder_id != old_reminder_id

    old_row = await reminders.get(old_reminder_id)
    assert old_row is not None
    assert old_row.state is ReminderState.CANCELLED

    new_row = await reminders.get(updated.reminder_id)
    assert new_row is not None
    assert new_row.state is ReminderState.PENDING
    assert new_row.due_at == when2


# ── Fix I1: past PROPOSED events must not lock out create_event forever ────

def _proposed_event(*, title: str, when: datetime | None) -> Event:
    return Event(
        id=uuid.uuid4(), conversation_id="signal:group-1", title=title,
        created_by="miles", when=when, venue_text=None, venue_place_id=None,
        venue_name=None, venue_address=None, venue_rating=None,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=None,
        status=EventStatus.PROPOSED, reminder_id=None, created_at=NOW,
    )


async def test_create_event_ignores_past_proposed_events_in_the_open_count():
    """Two dinners that already happened and were never explicitly
    cancelled must NOT refuse a third create_event forever — only a
    PROPOSED event that is still ahead (or has no `when` at all) counts
    toward the ambiguity backstop."""
    store = FakeEventStore()
    svc = _service(store=store)
    await store.create(_proposed_event(title="Past A", when=NOW - timedelta(days=10)))
    await store.create(_proposed_event(title="Past B", when=NOW - timedelta(days=5)))

    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="A New Dinner",
    )
    assert ev.title == "A New Dinner"


async def test_create_event_still_refuses_on_two_future_open_events():
    """The doctrine still holds for events that are genuinely still open —
    one with a future `when`, one with none at all."""
    store = FakeEventStore()
    svc = _service(store=store)
    await store.create(_proposed_event(title="Future A", when=NOW + timedelta(days=1)))
    await store.create(_proposed_event(title="Future B (no time yet)", when=None))

    with pytest.raises(AmbiguousOpenEventsError):
        await svc.create_event(
            conversation_id="signal:group-1", requester_person_id="miles",
            title="C",
        )


# ── Fix I2: updating/RSVPing a CANCELLED event must refuse, not re-arm ─────

async def test_update_event_refuses_on_a_cancelled_event():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    await svc.cancel_event(event_id=ev.id, conversation_id="signal:group-1")

    with pytest.raises(EventCancelledError):
        await svc.update_event(
            event_id=ev.id, conversation_id="signal:group-1",
            when=NOW + timedelta(days=3),
        )
    # No reminder was minted for the cancelled event, and its other fields
    # are untouched.
    assert list(await reminders.pending_for("signal:group-1")) == []
    back = await svc.get_event(event_id=ev.id)
    assert back.status is EventStatus.CANCELLED
    assert back.when is None


async def test_rsvp_refuses_on_a_cancelled_event():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    await svc.cancel_event(event_id=ev.id, conversation_id="signal:group-1")

    with pytest.raises(EventCancelledError):
        await svc.rsvp(
            event_id=ev.id, conversation_id="signal:group-1",
            person_id="daisy", response=RSVPResponse.YES,
        )


# ── Fix I5: event ids are conversation-scoped on every write verb ──────────

async def test_update_event_refuses_a_mismatched_conversation_id():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    with pytest.raises(EventNotFoundError):
        await svc.update_event(
            event_id=ev.id, conversation_id="signal:some-other-group",
            venue_text="Soi 29",
        )


async def test_cancel_event_refuses_a_mismatched_conversation_id():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    with pytest.raises(EventNotFoundError):
        await svc.cancel_event(
            event_id=ev.id, conversation_id="signal:some-other-group",
        )


async def test_rsvp_refuses_a_mismatched_conversation_id():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    with pytest.raises(EventNotFoundError):
        await svc.rsvp(
            event_id=ev.id, conversation_id="signal:some-other-group",
            person_id="daisy", response=RSVPResponse.YES,
        )


async def test_suggest_venue_refuses_a_mismatched_conversation_id():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    with pytest.raises(EventNotFoundError):
        await svc.suggest_venue(
            event_id=ev.id, conversation_id="signal:some-other-group",
            roster_person_ids=["miles"],
        )


# ── Fix I4: the FULL R-28 screen-call-screen sandwich, not just the half ───

async def test_venue_resolution_refuses_a_query_naming_a_known_person():
    """«Tyler's place» must not reach Google as a venue query — the same
    person-name half of R-28 `place_info` already enforces, now shared via
    `nora.agent.security.screen_and_resolve_place` (Fix I4)."""
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(
        event_id=ev.id, conversation_id="signal:group-1",
        venue_text="Tyler's place", person_names=frozenset({"tyler"}),
    )
    assert updated.venue_text == "Tyler's place"
    assert updated.venue_place_id is None
    assert updated.venue_name is None


async def test_venue_resolution_strips_a_google_returned_name_that_trips_the_screen():
    """Fix I4's return-path screen: a Google-returned `name`/`address` that
    itself trips `scan_identifiers`/`looks_like_coordinate` must be
    stripped before it is persisted — `_resolve_venue` used to write
    Google's raw fields straight into `venue_name`/`venue_address` with no
    screen at all."""
    route_source = _FakeRouteSource({
        "status": "ok", "name": "Call +85212345678 to book",
        "address": "22.2793,114.1628", "rating": 4.0,
        "price_level": "", "place_id": "ChIJfake456",
    })
    svc = _service(route_source=route_source)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(
        event_id=ev.id, conversation_id="signal:group-1", venue_text="Soi 29",
    )
    assert updated.venue_name is None        # stripped: an identifier
    assert updated.venue_address is None      # stripped: a coordinate
    assert updated.venue_place_id == "ChIJfake456"  # untouched fields kept
