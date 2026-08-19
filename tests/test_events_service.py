# tests/test_events_service.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nora.app.events import EventService
from nora.domain.events import EventStatus, RSVPResponse
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
            "maps_url": "https://maps.google.com/?q=soi29",
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
    back = await svc.get_event(ev.id)
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
    updated = await svc.update_event(event_id=ev.id, venue_text="Soi 29")

    assert updated.venue_name == "Soi 29 Thai Kitchen"
    assert updated.venue_place_id == "ChIJfake123"
    assert updated.venue_rating == 4.5
    assert updated.venue_resolved_at == NOW
    assert calls == ["Soi 29"]  # resolved exactly once

    # Reading it back must NOT re-query the resolver.
    back = await svc.get_event(ev.id)
    assert back.venue_name == "Soi 29 Thai Kitchen"
    assert calls == ["Soi 29"]


async def test_venue_resolution_refuses_a_raw_coordinate():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(
        event_id=ev.id, venue_text="22.2793,114.1628",
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
    r = await svc.rsvp(event_id=ev.id, person_id="daisy", response=RSVPResponse.YES)
    assert r.response is RSVPResponse.YES


async def test_cancel_event_cancels_its_linked_reminder():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    when = NOW + timedelta(days=3)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when,
    )
    cancelled = await svc.cancel_event(event_id=ev.id)
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
    await svc.cancel_event(event_id=a.id)

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
        event_id=ev.id, roster_person_ids=["miles", "daisy", "sam"],
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
        event_id=ev.id, roster_person_ids=["miles", "daisy"],
    )
    assert out.status == "nothing_found"
    assert out.candidates == ()
