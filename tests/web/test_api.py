from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web

from nora.agent.whereis import Circle
from nora.app.gatherings import GatheringService
from nora.domain.gatherings import RSVPResponse
from nora.domain.models import PersonRef
from nora.domain.presence import PresenceFix, WireState
from nora.persons import Persons
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.gatherings import FakeGatheringStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.conftest import build_test_doctrine_store

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
DAISY = PersonRef(person_id="daisy", name="Daisy", honcho_peer_id="daisy")
SAM = PersonRef(person_id="sam", name="Sam", honcho_peer_id="sam")
# Deliberately absent — build_skills_payload's own "no skills configured"
# state, not a fixture that needs real files on disk for tests unrelated
# to /app/v1/skills.
NO_SKILLS_DIR = Path("/nonexistent-skills-dir-for-tests")


def _fake_events() -> GatheringService:
    """A minimal, always-`unavailable`-for-venue-suggestion `GatheringService`
    (`route_source`/`honcho` both `None`) — these fixtures only exercise
    routes unrelated to Gathering, so the real shape doesn't matter, just that
    `register_web_routes` receives a real `GatheringService` instance."""
    return GatheringService(
        store=FakeGatheringStore(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
    )


class FakePresenceReader:
    """Conforms to `nora.agent.whereis.PresenceReader` — the same narrow
    protocol `Whereis` itself programs against, not the concrete SQL store."""

    def __init__(
        self,
        *,
        fixes: dict[str, PresenceFix] | None = None,
        windows: dict[str, list[PresenceFix]] | None = None,
    ) -> None:
        self._fixes = fixes or {}
        self._windows = windows or {}

    async def latest_fix(self, *, circle_id: str, person_id: str) -> PresenceFix | None:
        return self._fixes.get(person_id)

    async def places_for(self, *, circle_id: str) -> list:
        return []

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]:
        return self._windows.get(person_id, [])


@pytest.fixture
async def client(aiohttp_client):
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES}, index={}),
        ai_peer="nora",
        started_at=0.0,
        presence=None,
        presence_circles=(),
        skills_dir=NO_SKILLS_DIR,
        events=_fake_events(),
        doctrine_store=build_test_doctrine_store(),
    )
    return await aiohttp_client(app)


async def test_status_route_returns_200_with_the_expected_shape(client):
    resp = await client.get("/app/v1/status")
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"uptime_seconds", "store", "honcho", "reminders"}


async def test_doctrine_route_returns_the_shipped_sections_in_order(client):
    resp = await client.get("/app/v1/doctrine")
    assert resp.status == 200
    body = await resp.json()
    assert [section["name"] for section in body] == [
        "identity",
        "belonging",
        "voice",
        "conduct",
        "pictures",
        "constraints",
        "room-constraints",
    ]
    assert set(body[0]) == {"name", "description", "version", "body"}


async def test_tools_route_returns_the_live_catalog(client):
    resp = await client.get("/app/v1/tools")
    assert resp.status == 200
    body = await resp.json()
    assert body, "catalog must not be empty"
    names = [entry["name"] for entry in body]
    assert names == sorted(names)
    for entry in body:
        assert set(entry) >= {
            "name", "description", "budget", "terminal", "time_shiftable",
            "source_tier", "cacheable", "read_only", "side_effect_state",
            "idempotency", "retry", "output_contract",
        }
    # The one plugin tool (skills) must never appear — the registry does
    # not build it.
    assert "skills" not in names


async def test_memory_route_returns_only_the_requested_person(client):
    resp = await client.get("/app/v1/memory?personId=miles")
    assert resp.status == 200
    body = await resp.json()
    assert body == [
        {
            "person_id": "miles",
            "display_name": "Miles",
            "reachable": True,
            "identity": [],
            "episodes": ["x"],
        },
    ]


async def test_memory_route_with_no_person_id_returns_empty_not_everyone(client):
    """User-scoped: an unscoped request must never
    fall back to showing every whitelisted person's data."""
    resp = await client.get("/app/v1/memory")
    assert resp.status == 200
    assert await resp.json() == []


async def test_memory_route_with_an_unrecognized_person_id_returns_empty(client):
    resp = await client.get("/app/v1/memory?personId=stranger")
    assert resp.status == 200
    assert await resp.json() == []


async def test_persons_route_returns_the_roster(client):
    resp = await client.get("/app/v1/persons")
    assert resp.status == 200
    assert await resp.json() == [{"personId": "miles", "displayName": "Miles"}]


async def test_person_route_returns_the_profile(client):
    resp = await client.get("/app/v1/persons/miles")
    assert resp.status == 200
    assert await resp.json() == {"personId": "miles", "displayName": "Miles"}


async def test_person_route_is_404_for_an_unrecognized_person(client):
    resp = await client.get("/app/v1/persons/stranger")
    assert resp.status == 404


async def test_game_server_routes_are_absent(client):
    assert (await client.get("/app/v1/status")).status == 200
    assert (await client.get("/app/v1/games/servers")).status == 404


async def test_presence_route_returns_empty_list_when_presence_is_disabled(client):
    resp = await client.get("/app/v1/presence?personId=miles")
    assert resp.status == 200
    assert await resp.json() == []


async def test_presence_history_route_is_404_when_presence_is_disabled(client):
    """No circle at all → nobody is a known circle member — a 404, not an
    empty history, matching build_presence_history_payload's own contract."""
    resp = await client.get("/app/v1/presence/miles/history?personId=miles")
    assert resp.status == 404


@pytest.fixture
async def client_with_presence(aiohttp_client):
    circle = Circle(circle_id="c1", person_ids=frozenset({"miles", "ada"}), people=(MILES, ADA))
    other = Circle(circle_id="c2", person_ids=frozenset({"sam"}), people=(SAM,))
    fix = PresenceFix(
        circle_id="c1",
        person_id="miles",
        ts=1_755_000_000_000,
        lat=22.3,
        lng=114.2,
        wire_state=WireState.LIVE,
        recorded_at=1_755_000_000_000,
        battery=80.0,
    )
    older = PresenceFix(
        circle_id="c1",
        person_id="miles",
        ts=1_754_999_000_000,
        lat=22.30001,
        lng=114.20001,
        wire_state=WireState.LIVE,
        recorded_at=1_754_999_000_000,
    )
    sam_fix = PresenceFix(
        circle_id="c2",
        person_id="sam",
        ts=1_755_000_000_000,
        lat=22.28,
        lng=114.16,
        wire_state=WireState.LIVE,
        recorded_at=1_755_000_000_000,
    )
    reader = FakePresenceReader(
        fixes={"miles": fix, "sam": sam_fix},
        windows={"miles": [older, fix], "sam": [sam_fix]},
    )
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(
            by_person={"miles": MILES, "ada": ADA, "sam": SAM, "daisy": DAISY}, index={},
        ),
        ai_peer="nora",
        started_at=0.0,
        presence=reader,
        presence_circles=(circle, other),
        skills_dir=NO_SKILLS_DIR,
        events=_fake_events(),
        doctrine_store=build_test_doctrine_store(),
    )
    return await aiohttp_client(app)


async def test_presence_route_lists_every_circle_member(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence?personId=miles")
    assert resp.status == 200
    body = await resp.json()
    by_id = {entry["personId"]: entry for entry in body}
    assert set(by_id) == {"miles", "ada"}
    assert by_id["miles"]["current"]["lat"] == 22.3
    assert by_id["miles"]["current"]["wireState"] == "live"
    assert by_id["miles"]["current"]["batteryPercent"] == 80.0
    assert by_id["ada"]["current"] is None


async def test_presence_shows_only_the_viewers_own_circles(client_with_presence):
    """Miles shares c1 with Ada, not c2 with Sam: Sam is not on his map and
    Sam's route is the same 404 as a stranger's."""
    body = await (await client_with_presence.get("/app/v1/presence?personId=miles")).json()
    assert {entry["personId"] for entry in body} == {"miles", "ada"}
    resp = await client_with_presence.get("/app/v1/presence/sam/history?personId=miles")
    assert resp.status == 404
    body = await (await client_with_presence.get("/app/v1/presence?personId=sam")).json()
    assert [entry["personId"] for entry in body] == ["sam"]


async def test_presence_shows_nothing_to_a_known_person_in_no_circle(client_with_presence):
    """Daisy can sign in (she is in identity.toml) but shares no circle: every
    presence route answers empty or 404, never a circle member's position."""
    resp = await client_with_presence.get("/app/v1/presence?personId=daisy")
    assert resp.status == 200
    assert await resp.json() == []
    resp = await client_with_presence.get("/app/v1/presence/places?personId=daisy")
    assert resp.status == 200
    assert await resp.json() == []
    for member in ("miles", "ada", "sam"):
        resp = await client_with_presence.get(
            f"/app/v1/presence/{member}/history?hours=24&personId=daisy"
        )
        assert resp.status == 404


async def test_presence_routes_refuse_a_missing_or_unknown_viewer(client_with_presence):
    for path in (
        "/app/v1/presence",
        "/app/v1/presence/places",
        "/app/v1/presence/miles/history",
    ):
        assert (await client_with_presence.get(path)).status == 400
        assert (await client_with_presence.get(f"{path}?personId=mallory")).status == 400


async def test_presence_history_route_snaps_stay_points_within_10m(client_with_presence):
    """The two fixes in the fake's window are ~1.4m apart — well under the
    10m stay-point radius — so the route must collapse them to ONE point,
    proving the snapping actually runs at the route layer, not just in a
    unit test of the pure function."""
    resp = await client_with_presence.get("/app/v1/presence/miles/history?hours=24&personId=miles")
    assert resp.status == 200
    body = await resp.json()
    assert len(body["points"]) == 1
    assert body["points"][0]["lat"] == 22.30001


async def test_presence_history_route_404s_for_a_non_circle_member(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence/stranger/history?personId=miles")
    assert resp.status == 404


async def test_presence_history_route_rejects_a_non_numeric_hours(client_with_presence):
    resp = await client_with_presence.get(
        "/app/v1/presence/miles/history?hours=nope&personId=miles"
    )
    assert resp.status == 400


async def test_presence_history_route_rejects_a_non_positive_hours(client_with_presence):
    resp = await client_with_presence.get("/app/v1/presence/miles/history?hours=0&personId=miles")
    assert resp.status == 400


async def test_presence_places_route_returns_empty_list_when_presence_is_disabled(client):
    resp = await client.get("/app/v1/presence/places?personId=miles")
    assert resp.status == 200
    assert await resp.json() == []


@pytest.fixture
async def client_with_events(aiohttp_client):
    """Same fixture shape as `client`, but wired with a real `GatheringService`
    over a NON-empty `FakeGatheringStore` — one seeded event with a real RSVP —
    so the events routes exercise the actual request -> handler -> service
    -> store path, not a fixture whose store is always empty regardless of
    what a test asks for (CLAUDE.md's fake-fidelity rule; FakeGatheringStore's
    own docstring makes the same commitment for its store-level contract)."""
    events_service = GatheringService(
        store=FakeGatheringStore(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
    )
    event = await events_service.create_event(
        conversation_id="signal:group-1",
        requester_person_id="miles",
        title="Dinner",
        platform="signal",
    )
    await events_service.rsvp(
        event_id=event.id,
        conversation_id="signal:group-1", platform="signal",
        person_id="daisy",
        response=RSVPResponse.YES,
    )
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(
            by_person={"miles": MILES, "daisy": DAISY, "sam": SAM},
            index={},
        ),
        ai_peer="nora",
        started_at=0.0,
        presence=None,
        presence_circles=(),
        skills_dir=NO_SKILLS_DIR,
        events=events_service,
        doctrine_store=build_test_doctrine_store(),
    )
    web_client = await aiohttp_client(app)
    return web_client, event


async def test_the_events_routes_show_a_member_every_event_of_their_room(aiohttp_client):
    """The route passes the viewer's rooms and the visibility rule: a room member
    who neither made nor answered an event sees it in the list and the detail,
    and someone outside the room sees neither."""
    from nora.agent.lineage import LineagePolicy
    from nora.app.gatherings import room_event_shown

    events_service = GatheringService(
        store=FakeGatheringStore(), route_source=None, honcho=None, ai_peer="nora",
    )
    event = await events_service.create_event(
        conversation_id="group-1", requester_person_id="cognition:u",
        title="Saturday dinner", platform="signal",
    )
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES, "sam": SAM}, index={}),
        ai_peer="nora", started_at=0.0, presence=None, presence_circles=(),
        skills_dir=NO_SKILLS_DIR, events=events_service,
        doctrine_store=build_test_doctrine_store(),
        rooms_of=lambda p: frozenset({("signal", "group-1")}) if p == "miles" else frozenset(),
        room_event_shown=lambda e, v, pl, c: room_event_shown(
            LineagePolicy(), {"group-1": frozenset({("", "signal")})}, e, v, pl, c,
        ),
    )
    web_client = await aiohttp_client(app)
    listed = await (await web_client.get("/app/v1/events?personId=miles")).json()
    assert [e["title"] for e in listed] == ["Saturday dinner"]
    detail = await web_client.get(f"/app/v1/events/{event.id}?personId=miles")
    assert detail.status == 200
    assert await (await web_client.get("/app/v1/events?personId=sam")).json() == []
    outside = await web_client.get(f"/app/v1/events/{event.id}?personId=sam")
    assert outside.status == 404


async def test_the_events_routes_do_not_show_a_rooms_old_event_after_it_moved_unit(aiohttp_client):
    """The room membership grant also checks the event's stored owner. When
    `policy.toml` gives the same room id to another unit, the room's member must
    not inherit the previous unit's events in the list or the detail — the event
    keeps the unit that wrote it."""
    from nora.agent.lineage import LineagePolicy
    from nora.app.gatherings import room_event_shown

    events_service = GatheringService(
        store=FakeGatheringStore(), route_source=None, honcho=None, ai_peer="nora",
    )
    event = await events_service.create_event(
        conversation_id="group-1", requester_person_id="cognition:u",
        title="Work offsite", platform="signal", unit_id="work",
    )
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeMemoryStore(), reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES, "sam": SAM}, index={}),
        ai_peer="nora", started_at=0.0, presence=None,
        presence_circles=(), skills_dir=NO_SKILLS_DIR, events=events_service,
        doctrine_store=build_test_doctrine_store(),
        rooms_of=lambda p: frozenset({("signal", "group-1")}) if p == "miles" else frozenset(),
        room_event_shown=lambda e, v, pl, c: room_event_shown(
            LineagePolicy(), {"group-1": frozenset({("friends", "signal")})},
            e, v, pl, c,
        ),
    )
    web_client = await aiohttp_client(app)
    assert await (await web_client.get("/app/v1/events?personId=miles")).json() == []
    detail = await web_client.get(f"/app/v1/events/{event.id}?personId=miles")
    assert detail.status == 404


async def test_events_list_route_returns_the_seeded_event(client_with_events):
    """`miles` created it — creator is always a participant."""
    web_client, event = client_with_events
    resp = await web_client.get("/app/v1/events?personId=miles")
    assert resp.status == 200
    body = await resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(event.id)
    assert body[0]["title"] == "Dinner"
    assert body[0]["status"] == "proposed"


async def test_events_list_route_includes_events_ive_only_rsvpd_to(client_with_events):
    """`daisy` never created anything — she RSVP'd `yes`, which is enough."""
    web_client, event = client_with_events
    resp = await web_client.get("/app/v1/events?personId=daisy")
    assert resp.status == 200
    body = await resp.json()
    assert [e["id"] for e in body] == [str(event.id)]


async def test_events_list_route_excludes_events_im_not_part_of(client_with_events):
    """`sam` neither created nor RSVP'd — participant-scoping, not just
    auth: a valid personId that isn't a participant sees an empty list,
    not a 400 and not someone else's event."""
    web_client, _event = client_with_events
    resp = await web_client.get("/app/v1/events?personId=sam")
    assert resp.status == 200
    body = await resp.json()
    assert body == []


async def test_events_list_route_without_person_id_is_400(client_with_events):
    """No `personId` at all — a caller with no session-resolved identity
    must not read anything, matching `/app/v1/memory`'s convention."""
    web_client, _event = client_with_events
    resp = await web_client.get("/app/v1/events")
    assert resp.status == 400
    body = await resp.json()
    assert body["status"] == "refused"


async def test_events_list_route_with_an_unknown_person_id_is_400(client_with_events):
    """A `personId` that does not resolve to a known person (this
    deployment's `identity.toml`) is refused exactly like a missing one —
    an unresolvable identity is not a real, session-backed caller."""
    web_client, _event = client_with_events
    resp = await web_client.get("/app/v1/events?personId=not-a-real-person")
    assert resp.status == 400
    body = await resp.json()
    assert body["status"] == "refused"


async def test_events_detail_route_returns_the_event_with_rsvps(client_with_events):
    web_client, event = client_with_events
    resp = await web_client.get(f"/app/v1/events/{event.id}?personId=miles")
    assert resp.status == 200
    body = await resp.json()
    assert body["id"] == str(event.id)
    assert body["title"] == "Dinner"
    assert body["rsvps"] == [{"person_id": "daisy", "response": "yes"}]


async def test_events_detail_route_404s_when_im_not_a_participant(client_with_events):
    """`sam` is a valid, known person — just not part of THIS event. Must
    read exactly like the event doesn't exist (404), never a distinct
    "forbidden" that would confirm the id is real."""
    web_client, event = client_with_events
    resp = await web_client.get(f"/app/v1/events/{event.id}?personId=sam")
    assert resp.status == 404


async def test_events_detail_route_without_person_id_is_400(client_with_events):
    """Same personId-required minimum as the list route — this is the
    route that leaks a specific event's RSVPs (a list of person_ids) to
    anyone who can guess or enumerate an event id."""
    web_client, event = client_with_events
    resp = await web_client.get(f"/app/v1/events/{event.id}")
    assert resp.status == 400
    body = await resp.json()
    assert body["status"] == "refused"


async def test_events_detail_route_404s_for_a_missing_event(client_with_events):
    web_client, _event = client_with_events
    resp = await web_client.get(f"/app/v1/events/{uuid4()}?personId=miles")
    assert resp.status == 404


async def test_events_detail_route_400s_for_a_malformed_id(client_with_events):
    web_client, _event = client_with_events
    resp = await web_client.get("/app/v1/events/not-a-uuid?personId=miles")
    assert resp.status == 400
