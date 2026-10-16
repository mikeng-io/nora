from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.tools import NATIVE_TOOLS
from nora.app.gatherings import GatheringService
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.gatherings import FakeGatheringStore
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
GROUP = "signal:group-1"
OTHER_GROUP = "signal:group-2"


def test_all_six_event_tools_are_registered():
    for name in (
        "event_propose",
        "event_revise",
        "event_rsvp",
        "event_set_status",
        "event_search",
        "suggest_venue",
    ):
        assert name in NATIVE_TOOLS, f"{name} missing from NATIVE_TOOLS"


async def test_r52_two_open_events_refuses_a_third_rather_than_guessing():
    """R-52: create_event fail-closes on ambiguity (the
    'more than one open event' case) rather than silently picking one or
    creating a duplicate."""
    from nora.app.gatherings import AmbiguousOpenGatheringsError

    svc = GatheringService(
        store=FakeGatheringStore(),
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


# ── tool-LAYER translation coverage — the closures in
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


def _room(
    *, tagger: PersonRef | None = MILES, conversation_id: str = GROUP,
    trigger_message_ids: tuple[int, ...] = (),
) -> Room:
    source = FakeRosterSource(groups={conversation_id: (frozenset({"miles"}),)})
    return Room(
        platform="signal",
        conversation_id=conversation_id,
        roster_source=source,
        persons=_FakePersons(MILES),
        tagger=tagger,
        trigger_message_ids=trigger_message_ids,
    )


def _tools(
    *,
    events: GatheringService | None,
    metrics: Metrics | None = None,
    tagger: PersonRef | None = MILES,
    conversation_id: str = GROUP,
    trigger_message_ids: tuple[int, ...] = (),
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
        room=_room(
            tagger=tagger, conversation_id=conversation_id,
            trigger_message_ids=trigger_message_ids,
        ),
    )
    return {spec.name: spec.call for spec in specs}


def _events_service(*, store: FakeGatheringStore | None = None) -> GatheringService:
    return GatheringService(
        store=store or FakeGatheringStore(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC),
    )


async def test_every_event_tool_answers_unavailable_when_events_is_none():
    tools = _tools(events=None)
    calls = (
        ("event_search", {}),
        ("event_propose", {"title": "Dinner"}),
        ("event_revise", {"event_id": str(uuid4())}),
        ("event_rsvp", {"event_id": str(uuid4()), "response": "yes"}),
        ("event_set_status", {"event_id": str(uuid4()), "status": "cancelled"}),
        ("suggest_venue", {"event_id": str(uuid4())}),
    )
    for name, kwargs in calls:
        out = await tools[name](**kwargs)
        assert out["status"] == "unavailable", name


async def test_create_event_refuses_without_a_tagger():
    tools = _tools(events=_events_service(), tagger=None)
    out = await tools["event_propose"](title="Dinner")
    assert out["status"] == "refused"


async def test_rsvp_event_refuses_without_a_tagger():
    tools = _tools(events=_events_service(), tagger=None)
    out = await tools["event_rsvp"](event_id=str(uuid4()), response="yes")
    assert out["status"] == "refused"


async def test_create_event_refuses_an_unparseable_when():
    tools = _tools(events=_events_service())
    out = await tools["event_propose"](title="Dinner", when="whenever, idk")
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
    out = await tools["event_revise"](event_id=str(ev.id), when="whenever, idk")
    assert out["status"] == "refused"


async def test_list_events_refuses_a_bad_status():
    tools = _tools(events=_events_service())
    out = await tools["event_search"](status="not-a-real-status")
    assert out["status"] == "refused"


async def test_update_event_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["event_revise"](event_id=str(uuid4()))
    assert out["status"] == "refused"


async def test_rsvp_event_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["event_rsvp"](event_id=str(uuid4()), response="yes")
    assert out["status"] == "refused"


async def test_cancel_event_translates_event_not_found():
    tools = _tools(events=_events_service())
    out = await tools["event_set_status"](event_id=str(uuid4()), status="cancelled")
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
    out = await tools["event_revise"](event_id=str(ev.id), venue_text="Soi 29")
    assert out["status"] == "refused"


async def test_list_events_and_create_event_bind_to_the_bound_conversation_id():
    """`list_events`/`create_event` must bind to THIS room's
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

    created = await tools["event_propose"](title="Dinner")
    assert created["status"] == "ok"

    listed = await tools["event_search"]()
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
    out = await tools["event_revise"](event_id=str(ev.id), venue_text="Soi 29")
    assert out["status"] == "refused"


# ── ADR 0015: how to report a status rides in the result ────────────────────


class _Raising:
    """Every GatheringService verb the tools call, raising `exc` (or, for
    `suggest_venue`, answering `venues`). The tool layer's translation is what
    is under test, so the service is a stub here."""

    def __init__(self, exc: Exception | None = None, venues=None) -> None:
        self._exc = exc
        self._venues = venues

    async def _raise(self, **_: object):
        if self._exc is not None:
            raise self._exc
        return self._venues

    create_event = update_event = rsvp = cancel_event = list_events = _raise
    set_event_status = search_events = _raise

    async def suggest_venue(self, **_: object):
        return await self._raise()


async def test_every_non_ok_event_status_carries_a_note():
    """The six descriptions only NAME their statuses (ADR 0015); how to
    report each is the result's `note`. Every non-ok code path is driven, so
    one that loses its note, or grows a dash, goes red."""
    from nora.domain.gatherings import (
        AmbiguousOpenGatheringsError,
        GatheringCancelledError,
        GatheringNotFoundError,
        VenueSuggestions,
    )

    eid = str(uuid4())
    real = _events_service()
    boom = _Raising(RuntimeError("store down"))
    missing = _Raising(GatheringNotFoundError(UUID(eid)))
    gone = _Raising(GatheringCancelledError(UUID(eid)))
    calls = {
        "unavailable/off/list": (_tools(events=None), "event_search", {}),
        "unavailable/off/create": (_tools(events=None), "event_propose", {"title": "D"}),
        "unavailable/off/update": (_tools(events=None), "event_revise", {"event_id": eid}),
        "unavailable/off/rsvp": (
            _tools(events=None), "event_rsvp", {"event_id": eid, "response": "yes"}),
        "unavailable/off/cancel": (
            _tools(events=None), "event_set_status",
            {"event_id": eid, "status": "cancelled"}),
        "unavailable/off/suggest": (_tools(events=None), "suggest_venue", {"event_id": eid}),
        "unavailable/raise/list": (_tools(events=boom), "event_search", {}),
        "unavailable/raise/create": (_tools(events=boom), "event_propose", {"title": "D"}),
        "unavailable/raise/update": (_tools(events=boom), "event_revise", {"event_id": eid}),
        "unavailable/raise/rsvp": (
            _tools(events=boom), "event_rsvp", {"event_id": eid, "response": "yes"}),
        "unavailable/raise/cancel": (
            _tools(events=boom), "event_set_status",
            {"event_id": eid, "status": "cancelled"}),
        "unavailable/raise/suggest": (_tools(events=boom), "suggest_venue", {"event_id": eid}),
        "unavailable/memory/suggest": (
            _tools(events=_Raising(venues=VenueSuggestions(status="unavailable"))),
            "suggest_venue", {"event_id": eid}),
        "nothing_found/suggest": (
            _tools(events=_Raising(venues=VenueSuggestions(status="nothing_found"))),
            "suggest_venue", {"event_id": eid}),
        "refused/bad_status/list": (_tools(events=real), "event_search", {"status": "x"}),
        "refused/no_tagger/create": (
            _tools(events=real, tagger=None), "event_propose", {"title": "D"}),
        "refused/no_tagger/rsvp": (
            _tools(events=real, tagger=None), "event_rsvp",
            {"event_id": eid, "response": "yes"}),
        "refused/bad_when/create": (
            _tools(events=real), "event_propose", {"title": "D", "when": "whenever"}),
        "refused/bad_when/update": (
            _tools(events=real), "event_revise", {"event_id": eid, "when": "whenever"}),
        "refused/ambiguous/create": (
            _tools(events=_Raising(AmbiguousOpenGatheringsError(GROUP, ()))),
            "event_propose", {"title": "D"}),
        "refused/not_found/update": (_tools(events=missing), "event_revise", {"event_id": eid}),
        "refused/not_found/rsvp": (
            _tools(events=missing), "event_rsvp", {"event_id": eid, "response": "yes"}),
        "refused/not_found/cancel": (
            _tools(events=missing), "event_set_status",
            {"event_id": eid, "status": "cancelled"}),
        "refused/not_found/suggest": (_tools(events=missing), "suggest_venue", {"event_id": eid}),
        "refused/cancelled/update": (_tools(events=gone), "event_revise", {"event_id": eid}),
        "refused/cancelled/rsvp": (
            _tools(events=gone), "event_rsvp", {"event_id": eid, "response": "yes"}),
        "refused/bad_id/update": (_tools(events=real), "event_revise", {"event_id": "x"}),
        "refused/bad_id/rsvp": (
            _tools(events=real), "event_rsvp", {"event_id": "x", "response": "yes"}),
        "refused/bad_id/cancel": (
            _tools(events=real), "event_set_status",
            {"event_id": "x", "status": "cancelled"}),
        "refused/bad_id/suggest": (_tools(events=real), "suggest_venue", {"event_id": "x"}),
    }
    for case, (tools, verb, kwargs) in calls.items():
        out = await tools[verb](**kwargs)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case


async def test_an_ok_event_result_carries_no_reporting_note():
    """No ok guidance left these descriptions, so none is added to an ok."""
    events = _events_service()
    tools = _tools(events=events)
    created = await tools["event_propose"](title="Dinner")
    assert created["status"] == "ok" and "note" not in created
    listed = await tools["event_search"]()
    assert listed["status"] == "ok" and "note" not in listed


# ── migration 0074_event_revision (step 2b): base_revision_id at the tool
# layer, and event_set_status's lifecycle moves. ────────────────────────────


async def test_event_revise_refuses_a_stale_base_revision_id():
    events = _events_service()
    tools = _tools(events=events, conversation_id=GROUP)
    created = await tools["event_propose"](title="Dinner")
    real_head = created["head_revision_id"]

    out = await tools["event_revise"](
        event_id=created["id"], venue_text="Soi 29",
        base_revision_id=str(uuid4()),  # never the real head
    )
    assert out["status"] == "refused"
    assert "note" in out

    # The real head_revision_id still writes.
    out_ok = await tools["event_revise"](
        event_id=created["id"], venue_text="Soi 29", base_revision_id=real_head,
    )
    assert out_ok["status"] == "ok"
    assert out_ok["head_revision_id"] != real_head


async def test_event_set_status_moves_through_the_lifecycle_and_bumps_the_revision():
    events = _events_service()
    tools = _tools(events=events, conversation_id=GROUP)
    created = await tools["event_propose"](title="Dinner")

    confirmed = await tools["event_set_status"](
        event_id=created["id"], status="confirmed",
    )
    assert confirmed["status"] == "ok"
    assert confirmed["head_revision_id"] != created["head_revision_id"]

    happened = await tools["event_set_status"](
        event_id=created["id"], status="happened",
    )
    assert happened["status"] == "ok"
    assert happened["head_revision_id"] != confirmed["head_revision_id"]

    ev = await events.get_event(event_id=UUID(created["id"]))
    assert ev.status.value == "happened"


# ── The tag path's event writes cite the message that raised the turn (an event
# with no edge is out of the withdrawal cascade's reach). The ids are the Room's
# (code's own knowledge), never a tool argument.


async def test_a_tag_turn_s_event_writes_cite_the_message_that_raised_the_turn():
    store = FakeGatheringStore()
    tools = _tools(events=_events_service(store=store), trigger_message_ids=(77,))
    made = await tools["event_propose"](
        title="Dinner", when="2026-09-01T19:00:00+08:00", venue_text="place one",
    )
    assert made["status"] == "ok"
    cited = {(c.part, c.ref) for _, c in store.citations}
    assert cited == {(part, "77") for part in ("what", "status", "start", "place")}
    # A revision restates its own parts on the same message; a status move its own.
    await tools["event_revise"](event_id=made["id"], venue_text="place two")
    await tools["event_set_status"](event_id=made["id"], status="confirmed")
    assert {(c.part, c.ref) for _, c in store.citations} == cited
    # No id in the turn (a fire turn, a row outside the window): nothing to cite.
    bare = FakeGatheringStore()
    await _tools(events=_events_service(store=bare))["event_propose"](title="Dinner")
    assert bare.citations == []


def test_the_triggering_row_is_the_taggers_row_at_the_messages_own_time():
    from nora.agent.room import trigger_message_ids
    from nora.domain.models import TranscriptRow

    def row(id_: int, sender: str, ts: int, *, nora: bool = False) -> TranscriptRow:
        return TranscriptRow(
            id=id_, sender_id=sender, sender_name=None, ts=ts, body="x", is_nora=nora,
            delivery_status="sent", raw_json=None,  # type: ignore[arg-type]
        )

    window = (
        row(1, "+1", 1000), row(2, "+2", 2000), row(3, "+1", 2000, nora=True),
        row(4, "+1", 2000), row(5, "+1", 3000),
    )
    assert trigger_message_ids(window, sender_id="+1", timestamp=2000) == (4,)
    # Not in the window: no id, and never a nearby row.
    assert trigger_message_ids(window, sender_id="+1", timestamp=2500) == ()
