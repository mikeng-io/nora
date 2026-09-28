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


# ---- independent review 2026-09-28: the guard across circles ---------------

from nora.agent.whereis import Circle  # noqa: E402

PAT_PLACE = DerivedPlace(21, "pat_ho", "c2", 22.3, 114.1, 100.0, "home", 1.0, None, None)
GRANT_PLACE = DerivedPlace(22, "grant_carver", "c", 22.3513, 114.0633, 100.0,
                             "home", 1.0, None, None)


class _TwoPlaces:
    async def place_by_id(self, *, place_id):
        return {21: PAT_PLACE, 22: GRANT_PLACE}.get(place_id)


def _circle_of(persons, circle_id, ids):
    return Circle(
        circle_id=circle_id, person_ids=frozenset(ids),
        people=tuple(persons.by_person[i] for i in ids),
    )


def _route_circles(persons, routes, circles):
    specs = build_tool_specs(
        store=None, conversation_id="g", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, GRANT)), transit_source=None,
        presence_circles=circles, location_trusted=False,
        mobility=_TwoPlaces(), route_source=routes, clock=lambda: NOW.astimezone(UTC),
    )
    return next(s for s in specs if s.name == "route").call


async def test_a_room_that_passes_one_circle_cannot_open_anothers_places(persons):
    """Miles and Grant pass circle A; Pat's place is in circle B only, which
    this room is not inside. Only the circle holding the place's person may
    answer for it."""
    routes = _Routes()
    circles = (
        _circle_of(persons, "a", ("grant_carver", "miles_novak")),
        _circle_of(persons, "b", ("pat_ho", "miles_novak")),
    )
    out = await _route_circles(persons, routes, circles)(
        origin="place:21", destination="Olympian City")
    assert out["status"] == "refused" and routes.calls == []


async def test_the_circle_holding_the_place_is_found_past_the_first(persons):
    routes = _Routes()
    circles = (
        _circle_of(persons, "a", ("pat_ho", "miles_novak")),
        _circle_of(persons, "b", ("grant_carver", "miles_novak")),
    )
    out = await _route_circles(persons, routes, circles)(
        origin="place:22", destination="Olympian City")
    assert out["status"] == "ok" and len(routes.calls) == 1
