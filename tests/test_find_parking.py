"""`find_parking` — fetch nearby car parks from Google + TD cross-reference.

Pins the nearby-parse unit (the data layer), and each status's result
`note` through the tool over faked collaborators (ADR 0015 §3). The real
resolver + nearby call through the real registry is covered by the live
journey (eval).
"""
from __future__ import annotations

from types import SimpleNamespace

from nora.infra.parking.nearby import _parse_nearby


def _payload():
    # the FARTHER car park comes first, so only the sort can put the nearer one at [0]
    return {"places": [
        {"id": "g2", "displayName": {"text": "Olympian City 2 Car Park"},
         "location": {"latitude": 22.4180, "longitude": 114.1245},
         "formattedAddress": "18 Thistle Marlow Rd, Mong Kok",
         "types": ["parking_garage", "parking"]},
        {"id": "g1", "displayName": {"text": "Langham Place Car Park"},
         "location": {"latitude": 22.4110, "longitude": 114.1245},
         "formattedAddress": "31 Ashcombe St, Mong Kok",
         "types": ["parking", "parking_lot"]},
    ]}


def test_parse_nearby_sorts_nearest_first():
    out = _parse_nearby(_payload(), lat=22.4106, lng=114.1245)
    assert len(out) == 2
    assert out[0].name == "Langham Place Car Park"
    assert out[0].distance_m < out[1].distance_m


def test_parse_nearby_drops_row_without_coordinates():
    payload = {"places": [{"id": "x", "displayName": {"text": "No Loc"}}]}
    assert _parse_nearby(payload, lat=22.0, lng=114.0) == ()


def test_parse_nearby_empty():
    assert _parse_nearby({"places": []}, lat=22.0, lng=114.0) == ()


# ---- the TOOL: ADR 0015 §3, how to report a status rides in the result ----

_DASHES = ("—", "–")


class _Resolver:
    def __init__(self, answer):
        self.answer = answer

    async def resolve(self, query):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Places:
    """A route client with the two things `find_parking` reads off it."""

    def __init__(self, resolved=None, nearby=()):
        self._resolver = _Resolver(resolved)
        self.nearby = nearby

    async def nearby_parking(self, *, lat, lng):
        return self.nearby


class _Street:
    async def near(self, *, lat, lng):
        return SimpleNamespace(total=12, vacant=3, streets=("Nathan Road",))


class _NoFix:
    async def latest_fix(self, *, circle_id, person_id):
        return None


_HERE = SimpleNamespace(status="ok", place=SimpleNamespace(lat=22.28, lng=114.18),
                        could_be=())


def _find_parking(route_source, *, parking_source=None, street=None, presence=None):
    from nora.agent.room import Room
    from nora.agent.tools import build_tools
    from nora.agent.whereis import Circle
    from nora.domain.models import PersonRef
    from nora.obs.metrics import Metrics
    from nora.persons import Persons

    tyler = PersonRef(person_id="p_tyler", name="Tyler", honcho_peer_id="p_tyler",
                      first_name="Tyler", aliases=("陳生",))
    persons = Persons(by_person={"p_tyler": tyler}, index={})
    tools = build_tools(
        store=None, conversation_id="g1", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=persons),
        transit_source=None, route_source=route_source,
        parking_source=parking_source, street_parking_source=street,
        presence_source=presence,
        presence_circles=(Circle(circle_id="c1", person_ids=frozenset()),)
        if presence is not None else (),
    )
    return {t.tool_name: t for t in tools}["find_parking"]._tool_func


async def test_every_non_ok_find_parking_carries_a_dash_free_note():
    """The description only NAMES the statuses; what to say for each is the
    result's `note`. Every non-ok branch is driven, so one that loses its
    sentence goes red."""
    near = [SimpleNamespace(name="Lot A", address="1 Road", distance_m=120)]
    cases = {
        "unavailable/unwired": (_find_parking(None), "銅鑼灣"),
        "not_found/identifier": (_find_parking(_Places(_HERE)), "+85290000101"),
        "not_found/person": (_find_parking(_Places(_HERE)), "陳生"),
        "unavailable/resolve_raised": (_find_parking(_Places(RuntimeError("x"))), "銅鑼灣"),
        "unavailable/resolve_none": (_find_parking(_Places(None)), "銅鑼灣"),
        "ambiguous": (_find_parking(_Places(SimpleNamespace(
            status="ambiguous", place=None, could_be=("A", "B")))), "Central"),
        "not_found/place": (_find_parking(_Places(SimpleNamespace(
            status="not_found", place=None, could_be=()))), "nowhere"),
        "unavailable/no_presence": (_find_parking(_Places(_HERE, near)), ""),
        "unavailable/no_position": (_find_parking(_Places(_HERE, near),
                                                  presence=_NoFix()), ""),
        "unavailable/nearby": (_find_parking(_Places(_HERE, None)), "銅鑼灣"),
        "nothing_found": (_find_parking(_Places(_HERE, [])), "銅鑼灣"),
    }
    for case, (tool, place) in cases.items():
        out = await tool(place=place)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert not any(d in out["note"] for d in _DASHES), case


async def test_a_find_parking_ok_note_carries_each_clause_only_with_its_field(monkeypatch):
    """The vacancy, distance and street-meter rules left the description; each
    arrives only when the field it is about is in THIS answer. Live vacancy
    comes from the real `ParkingClient` over the feed's captured shape."""
    from nora.infra.parking.client import ParkingClient
    from tests.test_parking_client import _FakeSession

    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    lots = [SimpleNamespace(name="Sai Kung Government Offices Carpark",
                            address="1 Road", distance_m=120),
            SimpleNamespace(name="Lot B", address="2 Road", distance_m=300)]

    bare = await _find_parking(_Places(_HERE, lots))(place="銅鑼灣")
    assert bare["status"] == "ok"
    assert "distance_m" in bare["note"]
    assert "without vacancy" in bare["note"]
    assert "is the live answer" not in bare["note"]
    assert "street_parking" not in bare["note"]

    covered = await _find_parking(
        _Places(_HERE, lots[:1]),
        parking_source=ParkingClient(),
        street=_Street(),
    )(place="銅鑼灣")
    assert covered["car_parks"][0]["vacancy"] == 30
    assert "street_parking" in covered
    assert "is the live answer" in covered["note"]
    assert "without vacancy" not in covered["note"]
    assert "street_parking" in covered["note"]
    for out in (bare, covered):
        assert not any(d in out["note"] for d in _DASHES)
