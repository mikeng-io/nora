"""Generated SQL/directory and real route HTTP adapters verify selected saved names."""

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.managed import PersonLifecycle
from nora.domain.presence import SavedPlace
from nora.infra.routes import RouteClient
from nora.infra.routes.places import PlaceResolver
from nora.location.adapters.tools import build
from tests.integration.test_managed_presence import CIRCLE, PERSON
from tests.integration.test_managed_presence import case as case
from tests.integration.test_managed_presence import location as location

HOME = "Generated Saved Home"
DEST = "Generated Public Station"


def _point(step):
    return tuple(round(value + step / 100, 5) for value in (22.3, 114.1))


def _waypoint(step):
    lat, lng = _point(step)
    return {"latitude": lat, "longitude": lng}


@pytest.fixture
async def routing(case, location):
    await case.store.replace_places(circle_id=CIRCLE, places=[
        SavedPlace(CIRCLE, "generated-home", HOME, *_point(0), 1),
        SavedPlace(CIRCLE, "generated-unused", "Generated Unused Saved Alias", *_point(1), 1),
    ])
    env = SimpleNamespace(queries=[], requests=[], hold=False, lose_directory=False)
    points = {HOME: _point(21), DEST: _point(11), "Generated Public Park": _point(12)}

    async def places(request):
        assert request.headers["X-Goog-Api-Key"] == "generated-test-key"
        query = (await request.json())["textQuery"]
        env.queries.append(query)
        if query not in points:
            return web.json_response({"places": []})
        lat, lng = points[query]
        return web.json_response({"places": [{"id": "generated-public-place",
            "displayName": {"text": query}, "formattedAddress": "Generated Public Address",
            "location": {"latitude": lat, "longitude": lng}}]})

    async def routes(request):
        body = await request.json()
        assert body["origin"]["location"]["latLng"]
        assert body["destination"]["location"]["latLng"]
        env.requests.append(body)
        if env.hold:
            await case.bindings.hold_identity(person_id=PERSON, operation_id="generated-route-hold")
        if env.lose_directory:
            case.status = 503
            await case.collector.collect_once(case.circle, now_ms=int(time.time() * 1000))
        return web.json_response({"routes": [{"duration": "120s", "distanceMeters": 500,
                                               "legs": [{"steps": []}]}]})

    app = web.Application()
    app.router.add_post("/places", places)
    app.router.add_post("/routes", routes)
    async with TestServer(app) as server:
        source = RouteClient(api_key="generated-test-key",
            routes_url=str(server.make_url("/routes")),
            resolver=PlaceResolver(api_key="generated-test-key",
                search_url=str(server.make_url("/places")), timeout_seconds=2))
        env.deps = replace(location.deps, route_source=source)
        env.tools = {tool.name: tool.call for tool in build(env.deps)}
        yield env


def _origin(routing):
    return routing.requests[-1]["origin"]["location"]["latLng"]


async def test_plain_saved_name_uses_private_coordinate_without_public_name_lookup(
    case, location, routing,
):
    listed = await routing.tools["saved_places"]()
    assert listed["status"] == "ok" and HOME in listed["names"]
    result = await routing.tools["route"]("  " + HOME.upper() + "  ", DEST)
    assert result["status"] == "ok", result
    assert routing.queries == [DEST]
    assert _origin(routing) == _waypoint(0)
    assert "22.3" not in str(result) and "114.1" not in str(result)


@pytest.mark.parametrize("change", ["revoked", "held", "suspended", "directory-outage"])
async def test_later_route_never_reuses_private_coordinate_after_current_authority_changes(
    case, location, routing, change,
):
    assert (await routing.tools["route"](HOME, DEST))["status"] == "ok"
    assert _origin(routing) == _waypoint(0)
    if change == "revoked":
        await case.bindings.revoke_binding(account=case.ident)
    elif change == "held":
        await case.bindings.hold_identity(person_id=PERSON, operation_id="generated-route-revoke")
    elif change == "suspended":
        await case.bindings.set_lifecycle(person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    else:
        case.status = 503
        await case.collector.collect_once(case.circle, now_ms=int(time.time() * 1000))
    routing.queries.clear()
    result = await routing.tools["route"](HOME, DEST)
    assert result["status"] == "ok", result
    assert set(routing.queries) == {HOME, DEST}
    assert _origin(routing) == _waypoint(21)


async def test_route_withholds_private_result_after_a_late_hold(case, location, routing):
    routing.hold = True
    result = await routing.tools["route"](HOME, DEST)
    assert routing.requests and _origin(routing) == _waypoint(0)
    assert result["status"] == "unavailable" and "duration_seconds" not in result
    await case.bindings.release_identity_hold(person_id=PERSON, operation_id="generated-route-hold")
    routing.hold = False
    assert (await routing.tools["route"](HOME, DEST))["status"] == "ok"


async def test_public_route_does_not_retain_an_unrelated_private_circle_dependency(
    case, location, routing,
):
    routing.lose_directory = True
    result = await routing.tools["route"]("Generated Public Park", DEST)
    assert result["status"] == "ok", result
    assert _origin(routing) == _waypoint(12)
    assert set(routing.queries) == {"Generated Public Park", DEST}
    assert (await routing.tools["saved_places"]())["status"] == "unavailable"
    case.status = 200
    await case.collector.collect_once(case.circle, now_ms=int(time.time() * 1000))
    routing.lose_directory = False
    assert (await routing.tools["route"](HOME, DEST))["status"] == "ok"
    assert _origin(routing) == _waypoint(0)


async def test_untrusted_room_with_outsider_never_receives_saved_coordinates(
    case, location, routing,
):
    from nora.agent.room import Room
    from nora.testing import FakeRosterSource

    room = Room(platform="signal", conversation_id="generated-location-room",
        access=location.access,
        roster_source=FakeRosterSource(groups={"generated-location-room": (
            frozenset({"+85290000140"}), frozenset({"generated-unmapped-sender"}))}))
    tools = {tool.name: tool.call for tool in build(
        replace(routing.deps, room=room, location_trusted=False))}
    result = await tools["route"](HOME, DEST)
    assert result["status"] == "ok", result
    assert _origin(routing) == _waypoint(21)
    assert (await routing.tools["route"](HOME, DEST))["status"] == "ok"
    assert _origin(routing) == _waypoint(0)
