from pathlib import Path

import pytest
from aiohttp import web

from nora.agent.whereis import Circle
from nora.domain.models import PersonRef
from nora.domain.presence import PresenceFix, WireState
from nora.persons import Persons
from nora.testing import FakeRecordStore
from nora.web.api import register_web_routes
from tests.conftest import build_test_doctrine_store
from tests.web.test_memory import _claim, _Claims

MILES = PersonRef(person_id="miles", name="Miles")
ADA = PersonRef(person_id="ada", name="Ada")
DAISY = PersonRef(person_id="daisy", name="Daisy")
SAM = PersonRef(person_id="sam", name="Sam")
# Deliberately absent — build_skills_payload's own "no skills configured"
# state, not a fixture that needs real files on disk for tests unrelated
# to /app/v1/skills.
NO_SKILLS_DIR = Path("/nonexistent-skills-dir-for-tests")




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

        persons=Persons(by_person={"miles": MILES}, index={}),
        started_at=0.0,
        presence=None,
        presence_circles=(),
        skills_dir=NO_SKILLS_DIR,

        doctrine_store=build_test_doctrine_store(),
        profile=_Claims({
            ("friends", "miles"): (_claim("m1", "cycles to work"),),
            ("friends", "ada"): (_claim("a1", "plays guitar"),),
        }),
        profile_scopes_of=lambda _pid: (("friends", "67"),),
    )
    return await aiohttp_client(app)


async def test_status_route_returns_200_with_the_expected_shape(client):
    resp = await client.get("/app/v1/status")
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"uptime_seconds", "store"}


async def test_doctrine_route_returns_the_shipped_sections_in_order(client):
    resp = await client.get("/app/v1/doctrine")
    assert resp.status == 200
    body = await resp.json()
    assert [section["name"] for section in body] == [
        "identity",
        "belonging",
        "voice",
        "conduct",
        "engagement",
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


async def test_memory_route_returns_only_the_requested_persons_profile_accounts(client):
    resp = await client.get("/app/v1/memory?personId=miles")
    assert resp.status == 200
    body = await resp.json()
    [account] = body
    assert account["unit"] == "friends"
    assert [line["text"] for line in account["lines"]] == ["cycles to work"]
    assert set(account) == {"unit", "lines", "disputes"}


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

        persons=Persons(
            by_person={"miles": MILES, "ada": ADA, "sam": SAM, "daisy": DAISY}, index={},
        ),
        started_at=0.0,
        presence=reader,
        presence_circles=(circle, other),
        skills_dir=NO_SKILLS_DIR,

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


async def test_the_legacy_event_routes_are_removed(client):
    assert (await client.get("/app/v1/status")).status == 200
    for path in ("/app/v1/events", "/app/v1/events/00000000-0000-4000-8000-000000000001"):
        assert (await client.get(path, params={"personId": "miles"})).status == 404
