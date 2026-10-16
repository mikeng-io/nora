"""R-57: a live position `where_is` disclosed this turn may seed a `route`
argument only where the room already trusts you with location.

The block on a `where_is`-derived address entering `route` must live in CODE,
not only in `nora/skills/navigation/SKILL.md` prose — the skill might not even
load that turn, so prose alone would not stop a live position from reaching
Google. `route`'s R-28 screen already catches an
identifier, a raw coordinate, and a known person's name; this adds the fourth
case it never covered — a plain address string, which cannot be told apart
from a place the user typed by SHAPE alone. The only ground truth is
provenance: did `where_is` actually say this address, THIS turn. `where_is`
and `route` share one turn-scoped set for exactly that reason.
"""

from __future__ import annotations

from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.agent.whereis import Circle
from nora.domain.models import PersonRef
from nora.domain.presence import PresenceFix, SavedPlace, WireState
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeReverseGeocoder, FakeRosterSource

GROUP = "g1"
FUNG_PHONE = "+85298765432"
CIRCLE_ID = "circle-ridgeline"
FIX_TS = 1_000_000_000_000


class _CannedPresence:
    """`PresenceReader` — one LIVE fix for Fung, no saved places (so the
    address path, not the place-name path, is exercised) and no fix-of-its-own
    address (so the geocoder is reached, mirroring a Life360 fix that carries
    no address of its own)."""

    async def latest_fix(self, *, circle_id: str, person_id: str) -> PresenceFix | None:
        return PresenceFix(
            circle_id=circle_id, person_id=person_id, ts=FIX_TS,
            lat=22.3721, lng=114.1138, wire_state=WireState.LIVE,
            recorded_at=FIX_TS, address=None,
        )

    async def places_for(self, *, circle_id: str) -> list[SavedPlace]:
        return []

    async def window(self, *, circle_id, person_id, since_ts, until_ts):
        return []


class _RecordingClient:
    """Records whether `route` reached it — the probe that a refusal reached
    NOTHING, the same shape `tests/test_routes.py` uses for R-28."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def route(self, origin, destination, mode="TRANSIT", arrive_by="", **kw):
        self.calls.append((origin, destination))
        return {"status": "ok", "origin": origin, "destination": destination,
                "mode": mode, "duration_seconds": 100, "distance_meters": 500,
                "legs": [], "note": "ok"}


def _tools(*, location_trusted: bool):
    fung = PersonRef(
        person_id="p_fung", name="Fung", honcho_peer_id="p_fung",
        first_name="Fung", aliases=("Fung",),
    )
    persons = Persons(
        by_person={"p_fung": fung}, index={("signal", FUNG_PHONE): "p_fung"},
    )
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (frozenset({FUNG_PHONE}),)}),
        persons=persons,
    )
    circle = Circle(
        circle_id=CIRCLE_ID, person_ids=frozenset({"p_fung"}), people=(fung,),
    )
    client = _RecordingClient()
    tools = build_tools(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=room, transit_source=None, route_source=client,
        presence_source=_CannedPresence(), presence_circles=(circle,),
        location_trusted=location_trusted, geocoder=FakeReverseGeocoder("Tsuen Wan Road"),
        question="幾點翻到屋企", context_window=40,
    )
    by_name = {t.tool_name: t for t in tools}
    return by_name["where_is"], by_name["reverse_geocode"], by_name["route"], client


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


async def _disclose_live_address(where_is, reverse_geocode):
    """The two-step journey R-57 screens (DEC-156).

    `where_is` answers a position (saved place + age + lat/lng) and no address.
    Naming the street is an explicit second call to `reverse_geocode`, and that
    tool is what fills the turn-scoped disclosed set `route` screens against.

    Going through BOTH calls is the point, not ceremony: the coordinate has to
    come from the live fix for the disclosure to be real. Handing
    `reverse_geocode` a literal lat/lng would prove only that the tool talks to
    the geocoder, never that a LIVE POSITION is what reached `route`.
    """
    position = await _call(where_is, person="Fung")
    assert position["status"] == "ok"
    # No address in this payload — that IS the contract, and a regression that
    # put it back would make the screen below reachable by a path this file does
    # not cover.
    assert "address" not in position
    named = await _call(reverse_geocode, lat=position["lat"], lng=position["lng"])
    assert named["status"] == "ok"
    assert named["address"] == "Tsuen Wan Road"
    return named["address"]


async def test_a_disclosed_live_position_is_refused_as_a_route_argument_in_an_untrusted_room():
    where_is, reverse_geocode, route, client = _tools(location_trusted=False)
    address = await _disclose_live_address(where_is, reverse_geocode)

    out = await _call(route, origin=address, destination="Yoho Mall")
    assert out["status"] == "refused"
    assert client.calls == []                       # never reached Google
    # ADR 0015 §3: what to do instead rides in the refusal (it left the
    # description): do not retry, report the position without a travel time.
    assert "Do not retry" in out["note"]
    assert "—" not in out["note"] and "–" not in out["note"]


async def test_a_disclosed_live_position_may_route_in_a_trusted_room():
    where_is, reverse_geocode, route, client = _tools(location_trusted=True)
    address = await _disclose_live_address(where_is, reverse_geocode)

    out = await _call(route, origin=address, destination="Yoho Mall")
    assert out["status"] == "ok"
    assert client.calls == [("Tsuen Wan Road", "Yoho Mall")]


async def test_an_ordinary_place_name_is_never_screened_by_r57():
    """A place name nobody's live position ever disclosed routes normally in
    BOTH trust states — R-57 only ever matches an address `where_is` actually
    said this turn, never an arbitrary string that merely looks like one."""
    for trusted in (False, True):
        _where_is, _reverse_geocode, route, client = _tools(location_trusted=trusted)
        out = await _call(route, origin="天水圍站", destination="Yoho Mall")
        assert out["status"] == "ok", trusted
        assert client.calls == [("天水圍站", "Yoho Mall")], trusted


async def test_a_bound_place_label_that_is_a_disclosed_live_address_is_still_refused():
    """A presence read binds a place whose label is the same street `where_is`
    just disclosed: the bound name must not skip R-57."""
    from datetime import UTC, datetime

    from nora.domain.mobility import DerivedPlace, Stay

    class _Mobility:
        place = DerivedPlace(5, "p_fung", CIRCLE_ID, 22.3721, 114.1138, 100.0,
                             "unlabeled", 0.0, "Tsuen Wan Road", None)

        async def stays_for_subject(self, *, subject_id):
            day = datetime(2026, 9, 2, tzinfo=UTC)
            return [
                Stay("p_fung", "p_fung", CIRCLE_ID,
                     int(day.timestamp() * 1000) + w * 604_800_000,
                     int(day.timestamp() * 1000) + w * 604_800_000 + 86_400_000,
                     22.3721, 114.1138, 30.0, 9, place_id=5)
                for w in range(6)
            ]

        async def places_for_subject(self, *, subject_id):
            return [self.place]

        async def place_by_id(self, *, place_id):
            return self.place if place_id == 5 else None

    fung = PersonRef(
        person_id="p_fung", name="Fung", honcho_peer_id="p_fung",
        first_name="Fung", aliases=("Fung",),
    )
    persons = Persons(by_person={"p_fung": fung}, index={("signal", FUNG_PHONE): "p_fung"})
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (frozenset({FUNG_PHONE}),)}),
        persons=persons,
    )
    circle = Circle(circle_id=CIRCLE_ID, person_ids=frozenset({"p_fung"}), people=(fung,))
    client = _RecordingClient()
    tools = build_tools(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=room, transit_source=None, route_source=client,
        presence_source=_CannedPresence(), presence_circles=(circle,),
        location_trusted=False, geocoder=FakeReverseGeocoder("Tsuen Wan Road"),
        question="幾點翻到屋企", context_window=40, mobility=_Mobility(),
        clock=lambda: datetime(2026, 10, 14, 4, 0, tzinfo=UTC),
    )
    by = {t.tool_name: t for t in tools}
    read = await _call(by["routine_read"], date="2026-10-14", from_hour=0, to_hour=23,
                       name="Fung")
    # Unsaved (the common case), so its label is
    # the geocoded street and the bound words are «… near Tsuen Wan Road».
    words = "a place they go near Tsuen Wan Road"
    assert words in str(read)
    await _disclose_live_address(by["where_is"], by["reverse_geocode"])
    for origin in ("Tsuen Wan Road", words):
        out = await _call(by["route"], origin=origin, destination="Yoho Mall")
        assert out["status"] == "refused" and client.calls == [], origin
