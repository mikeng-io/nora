"""`route` takes a presence `place_ref` (design/27 §4.4 M3): a place with no
Life360 name («a home of theirs») becomes routable, resolved server side
behind the SAME R-31 guard as the routine that handed it out, and its
coordinate never reaches the model, the reply or a link."""

from __future__ import annotations

from datetime import UTC

import pytest

from nora.agent.tools import build_tool_specs
from nora.domain.mobility import DerivedPlace
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from tests.test_where_been import GRANT, IDENTITY, MILES, STRANGER, _circle, _room
from tests.unit.test_routine_read import NOW

SECOND_HOME = DerivedPlace(12, "grant_carver", "c", 22.3513, 114.0633, 100.0,
                           "home", 1.0, None, None)


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


class _Mobility:
    async def place_by_id(self, *, place_id):
        return SECOND_HOME if place_id == SECOND_HOME.id else None


class _Routes:
    def __init__(self):
        self.calls: list[dict] = []

    async def route(self, origin, destination, **kw):
        self.calls.append({"origin": origin, "destination": destination, **kw})
        return {"status": "ok", "origin": origin, "destination": destination,
                "mode": kw.get("mode", "TRANSIT"), "duration_seconds": 1500,
                "distance_meters": 9000, "legs": [], "note": "Say the time."}


def _route(persons, routes, *, members=(MILES, GRANT)):
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=members), transit_source=None,
        presence_circles=(_circle(persons),), location_trusted=False,
        mobility=_Mobility(), route_source=routes, clock=lambda: NOW.astimezone(UTC),
    )
    return next(s for s in specs if s.name == "route").call


async def test_a_place_ref_routes_from_an_unnamed_home(persons):
    routes = _Routes()
    out = await _route(persons, routes)(origin="place:12", destination="Olympian City")
    assert out["status"] == "ok"
    [call] = routes.calls
    local = call["local_places"]["place:12"]
    assert (local.lat, local.lng, local.name) == (22.3513, 114.0633, "a home of theirs")
    assert out["origin"] == "a home of theirs"
    assert "maps_url" not in out  # a link is built from spoken names only
    assert "22.28" not in str(out) and "114.17" not in str(out)


async def test_a_room_with_an_outsider_cannot_route_from_their_place(persons):
    routes = _Routes()
    out = await _route(persons, routes, members=(MILES, GRANT, STRANGER))(
        origin="place:12", destination="Olympian City")
    assert out["status"] == "refused" and out["note"]
    assert routes.calls == []


async def test_an_unknown_place_ref_is_refused_without_a_lookup(persons):
    routes = _Routes()
    out = await _route(persons, routes)(origin="place:999", destination="Olympian City")
    assert out["status"] == "refused" and routes.calls == []


async def test_a_place_ref_is_not_mistaken_for_an_identifier_or_a_coordinate(persons):
    routes = _Routes()
    out = await _route(persons, routes)(origin="Olympian City", destination="place:12")
    assert out["status"] == "ok" and out["destination"] == "a home of theirs"
