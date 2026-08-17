from __future__ import annotations

from nora.agent.whereis import Circle
from nora.domain.models import PersonRef
from nora.domain.presence import PresenceFix, SavedPlace, WireState
from nora.web.presence import (
    build_presence_history_payload,
    build_presence_payload,
    build_presence_places_payload,
)

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")
CIRCLE = Circle(circle_id="c1", person_ids=frozenset({"miles", "ada"}), people=(MILES, ADA))


class FakePresenceReader:
    def __init__(
        self, *, fixes: dict | None = None, windows: dict | None = None,
        places: dict[str, list[SavedPlace]] | None = None,
    ) -> None:
        self._fixes = fixes or {}
        self._windows = windows or {}
        self._places = places or {}

    async def latest_fix(self, *, circle_id: str, person_id: str) -> PresenceFix | None:
        return self._fixes.get(person_id)

    async def places_for(self, *, circle_id: str) -> list[SavedPlace]:
        return self._places.get(circle_id, [])

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]:
        return self._windows.get(person_id, [])


def _fix(person_id: str, *, ts: int, lat: float, lng: float, **kw) -> PresenceFix:
    return PresenceFix(
        circle_id="c1", person_id=person_id, ts=ts, lat=lat, lng=lng,
        wire_state=WireState.LIVE, recorded_at=ts, **kw,
    )


async def test_presence_disabled_returns_an_empty_list_not_an_error():
    assert await build_presence_payload(presence=None, circles=(CIRCLE,)) == []


async def test_presence_dedupes_a_person_in_more_than_one_circle():
    """Real bug, real fix: identity.toml can place one person in more than
    one Life360 circle (Miles's own account is in both "Ridgeline" and
    "TEAM_42") — a naive per-circle loop lists them twice."""
    other_circle = Circle(circle_id="c2", person_ids=frozenset({"miles"}), people=(MILES,))
    reader = FakePresenceReader(fixes={"miles": _fix("miles", ts=1000, lat=1.0, lng=2.0)})
    entries = await build_presence_payload(presence=reader, circles=(CIRCLE, other_circle))
    assert [e["personId"] for e in entries].count("miles") == 1


async def test_presence_lists_every_resolved_circle_member():
    reader = FakePresenceReader(fixes={"miles": _fix("miles", ts=1000, lat=1.0, lng=2.0)})
    entries = await build_presence_payload(presence=reader, circles=(CIRCLE,))
    by_id = {e["personId"]: e for e in entries}
    assert set(by_id) == {"miles", "ada"}
    assert by_id["miles"]["current"]["lat"] == 1.0
    assert by_id["ada"]["current"] is None


async def test_presence_current_omits_absent_optional_fields():
    reader = FakePresenceReader(fixes={"miles": _fix("miles", ts=1000, lat=1.0, lng=2.0)})
    entries = await build_presence_payload(presence=reader, circles=(CIRCLE,))
    current = next(e for e in entries if e["personId"] == "miles")["current"]
    assert "batteryPercent" not in current
    assert "isDriving" not in current
    assert "speedKmh" not in current
    assert "address" not in current


async def test_presence_current_carries_optional_fields_when_present():
    fix = _fix(
        "miles", ts=1000, lat=1.0, lng=2.0, battery=55.0,
        is_driving=True, speed_kmh=42.0, address="Home",
    )
    reader = FakePresenceReader(fixes={"miles": fix})
    entries = await build_presence_payload(presence=reader, circles=(CIRCLE,))
    current = next(e for e in entries if e["personId"] == "miles")["current"]
    assert current["batteryPercent"] == 55.0
    assert current["isDriving"] is True
    assert current["speedKmh"] == 42.0
    assert current["address"] == "Home"


async def test_places_disabled_returns_an_empty_list_not_an_error():
    assert await build_presence_places_payload(presence=None, circles=(CIRCLE,)) == []


async def test_places_lists_every_circles_saved_places():
    home = SavedPlace(circle_id="c1", place_id="p1", name="Home", lat=1.0, lng=2.0, updated_ms=0)
    reader = FakePresenceReader(places={"c1": [home]})
    places = await build_presence_places_payload(presence=reader, circles=(CIRCLE,))
    assert places == [{"name": "Home", "lat": 1.0, "lng": 2.0}]


async def test_places_dedupes_the_same_named_place_across_circles():
    """The same real-world household place can be saved under more than one
    circle a shared account belongs to — dedupe by name so "Home" doesn't
    render twice on the map."""
    other_circle = Circle(circle_id="c2", person_ids=frozenset({"miles"}), people=(MILES,))
    home1 = SavedPlace(circle_id="c1", place_id="p1", name="Home", lat=1.0, lng=2.0, updated_ms=0)
    home2 = SavedPlace(circle_id="c2", place_id="p2", name="Home", lat=1.0, lng=2.0, updated_ms=0)
    reader = FakePresenceReader(places={"c1": [home1], "c2": [home2]})
    places = await build_presence_places_payload(presence=reader, circles=(CIRCLE, other_circle))
    assert len(places) == 1


async def test_history_is_none_for_an_unknown_person_a_404_not_an_empty_list():
    result = await build_presence_history_payload(
        presence=None, circles=(CIRCLE,), person_id="stranger", hours=24,
    )
    assert result is None


async def test_history_is_a_real_empty_list_for_a_known_member_with_no_fixes():
    result = await build_presence_history_payload(
        presence=None, circles=(CIRCLE,), person_id="miles", hours=24,
    )
    assert result == {"hours": 24, "from": result["from"], "to": result["to"], "points": []}


async def test_history_snaps_fixes_within_10m_to_the_same_point():
    # ~1.4m apart at this latitude — well under the 10m stay-point radius.
    fixes = [
        _fix("miles", ts=1000, lat=22.30000, lng=114.20000),
        _fix("miles", ts=2000, lat=22.30001, lng=114.20001),
        _fix("miles", ts=3000, lat=22.30001, lng=114.20002),
    ]
    reader = FakePresenceReader(windows={"miles": fixes})
    result = await build_presence_history_payload(
        presence=reader, circles=(CIRCLE,), person_id="miles", hours=24,
    )
    assert result["points"] == [{"lat": 22.30000, "lng": 114.20000, "ts": 1000}]


async def test_history_emits_a_new_point_past_the_10m_radius():
    fixes = [
        _fix("miles", ts=1000, lat=22.30000, lng=114.20000),
        # ~157m north — a real move, not jitter.
        _fix("miles", ts=2000, lat=22.30141, lng=114.20000),
    ]
    reader = FakePresenceReader(windows={"miles": fixes})
    result = await build_presence_history_payload(
        presence=reader, circles=(CIRCLE,), person_id="miles", hours=24,
    )
    assert len(result["points"]) == 2
    assert [p["ts"] for p in result["points"]] == [1000, 2000]


async def test_history_resets_the_anchor_only_on_a_real_move():
    """A drifts-away-then-drifts-back sequence must not silently re-collapse
    into the ORIGINAL anchor — once a new vertex is emitted, IT becomes the
    anchor for what follows."""
    fixes = [
        _fix("miles", ts=1000, lat=22.30000, lng=114.20000),
        _fix("miles", ts=2000, lat=22.30141, lng=114.20000),   # real move #1
        _fix("miles", ts=3000, lat=22.30142, lng=114.20000),   # jitter off move #1's anchor
    ]
    reader = FakePresenceReader(windows={"miles": fixes})
    result = await build_presence_history_payload(
        presence=reader, circles=(CIRCLE,), person_id="miles", hours=24,
    )
    assert len(result["points"]) == 2
