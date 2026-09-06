# tests/test_event_tools.py (this file also gets the doctrine test in Task 10 — start it now)
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
from nora.app.gatherings import GatheringService
from nora.domain.models import PersonRef
from nora.domain.reminders import ReminderCaps
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.gatherings import FakeGatheringStore
from nora.testing.reminders import FakeReminderStore
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
GROUP = "signal:group-1"
OTHER_GROUP = "signal:group-2"


def test_all_six_event_tools_are_registered():
    for name in (
        "create_event",
        "update_event",
        "rsvp_event",
        "cancel_event",
        "list_events",
        "suggest_venue",
    ):
        assert name in NATIVE_TOOLS, f"{name} missing from NATIVE_TOOLS"


async def test_r52_two_open_events_refuses_a_third_rather_than_guessing():
    """R-52: create_event fail-closes on ambiguity — spec §3.1's
    'more than one open event' case — rather than silently picking one or
    creating a duplicate."""
    from nora.app.gatherings import AmbiguousOpenGatheringsError

    svc = GatheringService(
        store=FakeGatheringStore(),
        reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, tzinfo=UTC),
    )
    await svc.create_event(
        conversation_id="signal:group-1",
        requester_person_id="miles",
        title="A",
        platform="signal",
    )
    await svc._store.update(  # force a SECOND open event without going
        # through create_event's own guard, to set up the ambiguous state
        await svc.create_event(
            conversation_id="signal:group-1",
            requester_person_id="miles",
            title="B",
            platform="signal",
        )
    )
    with pytest.raises(AmbiguousOpenGatheringsError):
        await svc.create_event(
            conversation_id="signal:group-1",
            requester_person_id="miles",
            title="C",
            platform="signal",
        )


# ── Fix I6: tool-LAYER translation coverage — the closures in
# `nora.agent.tools`, not `GatheringService` itself, which the two tests above
# never touch. `_tools()`/`_room()` mirror `tests/test_web_fetch_tool.py`'s
# lightweight `ToolRegistry(...).specs(...)` pattern — no database, no real
# room daemon.


class _NullStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


def _room(*, tagger: PersonRef | None = MILES, conversation_id: str = GROUP) -> Room:
    source = FakeRosterSource(groups={conversation_id: (frozenset({"miles"}),)})
    return Room(
        platform="signal",
        conversation_id=conversation_id,
        roster_source=source,
        persons=_FakePersons(MILES),
        tagger=tagger,
    )


def _tools(
    *,
    events: GatheringService | None,
    metrics: Metrics | None = None,
    tagger: PersonRef | None = MILES,
    conversation_id: str = GROUP,
) -> dict:
    registry = ToolRegistry(events=events)
    specs = registry.specs(
        store=_NullStore(),
        conversation_id=conversation_id,
        conversation_type="group",
        persons=_FakePersons(MILES),
        honcho=FakeMemoryStore(),
        deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=_room(tagger=tagger, conversation_id=conversation_id),
    )
    return {spec.name: spec.call for spec in specs}


def _events_service(*, store: FakeGatheringStore | None = None) -> GatheringService:
    return GatheringService(
        store=store or FakeGatheringStore(),
        reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC),
    )


async def test_every_event_tool_answers_unavailable_when_events_is_none():
    tools = _tools(events=None)
    calls = (
        ("list_events", {}),
        ("create_event", {"title": "Dinner"}),
        ("update_event", {"event_id": str(uuid4())}),
        ("rsvp_event", {"event_id": str(uuid4()), "response": "yes"}),
        ("cancel_event", {"event_id": str(uuid4())}),
        ("suggest_venue", {"event_id": str(uuid4())}),
    )
    for name, kwargs in calls:
        out = await tools[name](**kwargs)
        assert out["status"] == "unavailable", name


async def test_create_event_refuses_without_a_tagger():
    tools = _tools(events=_events_service(), tagger=None)
    out = await tools["create_event"](title="Dinner")
    assert out["status"] == "refused"


async def test_rsvp_event_refuses_without_a_tagger():
    tools = _tools(events=_events_service(), tagger=None)
    out = await tools["rsvp_event"](event_id=str(uuid4()), response="yes")
    assert out["status"] == "refused"


async def test_create_event_refuses_an_unparseable_when():
    tools = _tools(events=_events_service())
    out = await tools["create_event"](title="Dinner", when="whenever, idk")
    assert out["status"] == "refused"


async def test_update_event_refuses_an_unparseable_when():
    events = _events_service()
    tools = _tools(events=events)
    ev = await events.create_event(
        conversation_id=GROUP,
        requester_person_id="miles",
        title="Dinner",
        platform="signal",
    )
    out = await tools["update_event"](event_id=str(ev.id), when="whenever, idk")
    assert out["status"] == "refused"


async def test_list_events_refuses_a_bad_status():
    tools = _tools(events=_events_service())
    out = await tools["list_events"](status="not-a-real-status")
    assert out["status"] == "refused"


async def test_update_event_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["update_event"](event_id=str(uuid4()))
    assert out["status"] == "refused"


async def test_rsvp_event_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["rsvp_event"](event_id=str(uuid4()), response="yes")
    assert out["status"] == "refused"


async def test_cancel_event_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["cancel_event"](event_id=str(uuid4()))
    assert out["status"] == "refused"


async def test_suggest_venue_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["suggest_venue"](event_id=str(uuid4()))
    assert out["status"] == "refused"


async def test_update_event_translates_a_cancelled_event_fix_i2():
    events = _events_service()
    tools = _tools(events=events)
    ev = await events.create_event(
        conversation_id=GROUP,
        requester_person_id="miles",
        title="Dinner",
        platform="signal",
    )
    await events.cancel_event(event_id=ev.id, conversation_id=GROUP)
    out = await tools["update_event"](event_id=str(ev.id), venue_text="Soi 29")
    assert out["status"] == "refused"


async def test_list_events_and_create_event_bind_to_the_bound_conversation_id():
    """Fix I6: `list_events`/`create_event` must bind to THIS room's
    `bound_conversation_id`, never a client-suppliable value — an event
    that exists in a DIFFERENT conversation must not appear."""
    events = _events_service()
    await events.create_event(
        conversation_id=OTHER_GROUP,
        requester_person_id="miles",
        title="Elsewhere",
        platform="signal",
    )
    tools = _tools(events=events, conversation_id=GROUP)

    created = await tools["create_event"](title="Dinner")
    assert created["status"] == "ok"

    listed = await tools["list_events"]()
    assert listed["status"] == "ok"
    assert [e["title"] for e in listed["events"]] == ["Dinner"]


async def test_update_event_refuses_an_event_from_another_conversation_fix_i5():
    events = _events_service()
    ev = await events.create_event(
        conversation_id=OTHER_GROUP,
        requester_person_id="miles",
        title="Elsewhere",
        platform="signal",
    )
    tools = _tools(events=events, conversation_id=GROUP)
    out = await tools["update_event"](event_id=str(ev.id), venue_text="Soi 29")
    assert out["status"] == "refused"
