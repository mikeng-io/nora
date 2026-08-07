"""`whereis` and its R-21 fail-closed disclosure seam (design/19 §5, DEC-54).

The load-bearing test is `test_whereis_refuses_to_disclose_outside_the_circle`
(R-31): a conversation that is NOT a subset of the circle gets a REFUSAL, not a
position and not a silent omission. Revert `DisclosureGuard.permits` to «always
true» and it goes red — presence would leak into a room containing an outsider,
which is the exact inverse of R-21 the reviews said nothing else enforces.

The room fixtures mirror the live `Ridgeline` group (same shape as
`tests/unit/test_agent_room.py`). The presence reader is the REAL `PresenceStore`
against testcontainer Postgres, so degradation and age are read the way
production reads them.
"""

from __future__ import annotations

import pytest

from nora.agent.room import Room
from nora.agent.whereis import Circle, Whereis, check_disclosure_invariant
from nora.domain.presence import PresenceFix, WireState
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.presence import PresenceStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeRosterSource

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
NORA = frozenset({"+85290000100"})
MILES = frozenset({"+85290000101"})
GRANT = frozenset({"+85290000104"})
STRANGER = frozenset({"+85260000000"})
CIRCLE_ID = "circle-ridgeline"
NOW = 1_000_000_000_000

IDENTITY = """
[persons.miles_novak]
first_name = "Miles"
last_name  = "Novak"
aliases = ["Miles"]
[[persons.miles_novak.signal]]
phone = "+85290000101"

[persons.grant_carver]
first_name = "Grant"
last_name  = "Carver"
aliases = ["Chi"]
[[persons.grant_carver.signal]]
phone = "+85290000104"

[persons.outsider]
first_name = "Sam"
last_name  = "Outsider"
[[persons.outsider.signal]]
phone = "+85260000000"
"""


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    s = PresenceStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


# The circle roster: Miles and Grant. The disclosure seam checks a conversation
# is a SUBSET of these person_ids.
CIRCLE = Circle(circle_id=CIRCLE_ID, person_ids=frozenset({"miles_novak", "grant_carver"}))


def _room(persons, *, members, fail=False, tagger=None):
    return Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: tuple(members)}, fail=fail),
        persons=persons, self_ids=NORA, tagger=tagger,
    )


def _whereis(persons, store, *, members, fail=False, tagger=None, metrics=None):
    return Whereis(
        room=_room(persons, members=members, fail=fail, tagger=tagger),
        circle=CIRCLE, presence=store, metrics=metrics,
    )


async def _record(store, *, person, ts, address=None):
    await store.record_fix(fix=PresenceFix(
        circle_id=CIRCLE_ID, person_id=person, ts=ts, lat=22.44, lng=114.02,
        wire_state=WireState.LIVE, recorded_at=ts, address=address,
    ))


async def test_whereis_returns_the_latest_fix_with_its_age(persons, store):
    await _record(store, person="miles_novak", ts=NOW - 20 * 60_000, address="Yoho Mall")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert out["person"] == "Miles Novak"
    assert out["age_seconds"] == 20 * 60          # the fix's age reaches the model
    assert out["address"] == "Yoho Mall"
    assert out["wire_state"] == "live"


async def test_whereis_refuses_to_disclose_outside_the_circle(persons, store):
    """R-31 / design/19 §5 — the NEW fail-closed seam. The conversation contains
    an outsider (person outside the circle), so NO member's position may be
    disclosed here. A REFUSAL, not a silent omission, counted for observability.
    Revert `DisclosureGuard.permits` to «always true» and this goes red."""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    w = _whereis(persons, store, members=(MILES, GRANT, STRANGER, NORA), metrics=metrics)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "refused"
    assert "lat" not in out and "address" not in out       # nothing leaked
    assert metrics.get("nora_presence_disclosure_refusals_total") == 1


async def test_whereis_refuses_when_the_room_holds_an_unmapped_account(persons, store):
    """Fail-closed on UNCERTAINTY, not just on a RESOLVED outsider (both #37
    reviews, 2026-08-07). An account in the room that resolves to NO person_id — a
    stranger not in identity.toml, design/19 §5's own trigger — is unverifiable
    against the circle, so `view.unnamed > 0` must refuse. The resolvable subset
    alone (miles, grant) would PASS the subset check; revert the `view.unnamed`
    clause and presence leaks past the unmapped outsider — this goes red."""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    unmapped = frozenset({"+85299999999"})     # NOT in identity.toml → view.unnamed
    w = _whereis(
        persons, store, members=(MILES, GRANT, unmapped, NORA), metrics=metrics,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "refused"
    assert "lat" not in out and "address" not in out
    assert metrics.get("nora_presence_disclosure_refusals_total") == 1


async def test_whereis_is_unavailable_and_never_null_island_without_a_fix(persons, store):
    """No stored fix → `unavailable`, never a (0,0) null-island or a guess (§7.6)."""
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"
    assert "lat" not in out and "lng" not in out and "address" not in out


async def test_whereis_is_unavailable_when_the_room_cannot_be_read(persons, store):
    await _record(store, person="miles_novak", ts=NOW)
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), fail=True)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"


async def test_whereis_refuses_on_a_degraded_room_it_cannot_verify(persons, store):
    """A degraded (tagger-only) view cannot VERIFY group ⊆ circle, so the
    fail-closed guard refuses rather than disclose on an unverifiable room."""
    await _record(store, person="miles_novak", ts=NOW)
    tagger = persons.resolve("signal", "+85290000101")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), fail=True, tagger=tagger)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "refused"


async def test_whereis_not_found_for_a_name_not_in_the_room(persons, store):
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Nobody", now_ms=NOW)
    assert out["status"] == "not_found"


# ── the startup invariant (design/19 §5, §7.7) ──────────────────────────────

def test_the_startup_invariant_holds_when_group_is_a_subset_of_the_circle():
    metrics = Metrics()
    ok = check_disclosure_invariant(
        circle=CIRCLE, group_person_ids=frozenset({"miles_novak", "grant_carver"}),
        metrics=metrics,
    )
    assert ok is True
    assert metrics.get("nora_presence_circle_subset_ok", circle=CIRCLE_ID) == 1.0


def test_the_startup_invariant_alarms_the_day_group_is_not_a_subset():
    metrics = Metrics()
    ok = check_disclosure_invariant(
        circle=CIRCLE,
        group_person_ids=frozenset({"miles_novak", "grant_carver", "outsider"}),
        metrics=metrics,
    )
    assert ok is False
    assert metrics.get("nora_presence_circle_subset_ok", circle=CIRCLE_ID) == 0.0


# ── the DEC-59 wiring: `whereis` as a native tool over this seam ─────────────
#
# The seam above is what the tool CLOSES OVER; these prove the closure in
# `nora.agent.tools.build_tool_specs` composes it honestly: unavailable (never
# a crash, never a guess) when presence is not wired, the seam's own answer
# when it is, and — with several circles — no answer from a circle the room is
# not a verified subset of (R-31 held through the wiring, not just the seam).

from nora.agent.tools import build_tool_specs  # noqa: E402
from nora.agent.whereis import circles_from_persons  # noqa: E402


def _whereis_tool(persons, *, room, presence_source=None, presence_circles=(),
                  metrics=None):
    specs = build_tool_specs(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=room, transit_source=None,
        presence_source=presence_source, presence_circles=presence_circles,
    )
    return next(s for s in specs if s.name == "whereis")


async def test_the_whereis_tool_is_unavailable_when_presence_is_not_wired(persons):
    """Offline/eval compositions and `[presence] enabled=false` build the same
    tool with no reader — it must say «I could not check», never crash and
    never guess (R-5)."""
    spec = _whereis_tool(persons, room=_room(persons, members=(MILES, NORA)))
    out = await spec.call("Miles")
    assert out["status"] == "unavailable"


async def test_the_whereis_tool_answers_through_the_wired_seam(persons, store):
    await _record(store, person="miles_novak", ts=NOW - 300_000, address="Acme Tower")
    spec = _whereis_tool(
        persons, room=_room(persons, members=(MILES, GRANT, NORA)),
        presence_source=store, presence_circles=(CIRCLE,),
    )
    out = await spec.call("Miles")
    assert out["status"] == "ok"
    assert out["address"] == "Acme Tower"


async def test_the_whereis_tool_never_discloses_via_a_circle_the_room_is_not_in(
    persons, store,
):
    """Two circles wired, and the room is a subset of only the second: the
    first must refuse inside the seam and the answer must come from the
    qualifying circle — R-31 survives the multi-circle wiring."""
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Yoho Mall")
    outsiders_only = Circle(circle_id="c-other", person_ids=frozenset({"outsider"}))
    spec = _whereis_tool(
        persons, room=_room(persons, members=(MILES, GRANT, NORA)),
        presence_source=store, presence_circles=(outsiders_only, CIRCLE),
    )
    out = await spec.call("Miles")
    assert out["status"] == "ok"
    assert out["address"] == "Yoho Mall"


async def test_the_whereis_tool_refuses_when_no_wired_circle_contains_the_room(
    persons, store,
):
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Yoho Mall")
    outsiders_only = Circle(circle_id="c-other", person_ids=frozenset({"outsider"}))
    spec = _whereis_tool(
        persons, room=_room(persons, members=(MILES, GRANT, NORA)),
        presence_source=store, presence_circles=(outsiders_only,),
    )
    out = await spec.call("Miles")
    assert out["status"] == "refused"
    assert "Yoho" not in str(out)


def test_circles_from_persons_groups_by_circle_and_skips_unplaced(tmp_path):
    """The tool-side roster derivation: same grouping as the collector's
    `_circles_from_identity`, but LENIENT — an unplaced block is skipped (the
    collector is what refuses over it) and an empty identity yields no circles
    (whereis degrades to unavailable rather than Nora refusing to start)."""
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY + """
[[persons.miles_novak.life360]]
member_id = "m-miles"
circle_id = "c1"

[[persons.grant_carver.life360]]
member_id = "m-gab"
circle_id = "c1"

[[persons.outsider.life360]]
member_id = "m-sam"
""")
    circles = circles_from_persons(load_persons(path))
    assert circles == (
        Circle(circle_id="c1", person_ids=frozenset({"miles_novak", "grant_carver"})),
    )


def test_circles_from_persons_is_empty_for_an_identity_with_no_life360_blocks(persons):
    assert circles_from_persons(persons) == ()


# ── the at-place answer + saved-place resolution scope (design/23 §4.1) ──────

from nora.agent.whereis import resolvable_places  # noqa: E402
from nora.domain.presence import SavedPlace  # noqa: E402


def _saved(name, *, lat=22.44, lng=114.02, radius=300.0, circle=CIRCLE_ID):
    return SavedPlace(circle_id=circle, place_id=f"p-{name}", name=name,
                      lat=lat, lng=lng, radius_m=radius, updated_ms=NOW)


async def test_whereis_names_the_saved_place_the_fix_sits_inside(persons, store):
    """A fix inside a place's geofence answers with the household's own name
    for the spot, AND the address — both, so the model never guesses (operator
    ask 2026-08-07). Geometry runs server-side; the model sees only names."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Jvvilxgd Road")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert out["place"] == "Home"
    assert out["address"] == "Jvvilxgd Road"
    assert "lat" not in out and "lng" not in out


async def test_whereis_outside_every_geofence_answers_without_a_place(persons, store):
    """~1.1 km from the saved place with a 300 m radius: no place name, address
    only. A place with no radius cannot contain."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[
        _saved("Home", lat=22.45, lng=114.02, radius=300.0),
        _saved("NoRadius", lat=22.44, lng=114.02, radius=None),
    ])
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Jvvilxgd Road")
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert "place" not in out
    assert out["address"] == "Jvvilxgd Road"


async def test_resolvable_places_returns_the_subset_circles_places(persons, store):
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    room = _room(persons, members=(MILES, GRANT, NORA))
    places = await resolvable_places(room=room, circles=(CIRCLE,), presence=store)
    assert [p.name for p in places] == ["Home"]


async def test_a_non_subset_room_resolves_no_saved_place(persons, store):
    """R-35 (design/23 §4.1): a room containing anyone outside the circle — or
    anyone unmapped — resolves NOTHING, silently. Empty, not a refusal: a
    refusal would confirm the guarded name exists."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    outsider_room = _room(persons, members=(MILES, GRANT, STRANGER, NORA))
    assert await resolvable_places(
        room=outsider_room, circles=(CIRCLE,), presence=store,
    ) == ()
    unmapped_room = _room(persons, members=(MILES, frozenset({"+85299999999"}), NORA))
    assert await resolvable_places(
        room=unmapped_room, circles=(CIRCLE,), presence=store,
    ) == ()


async def test_an_unreadable_roster_resolves_no_saved_place(persons, store):
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    room = _room(persons, members=(MILES, NORA), fail=True)
    assert await resolvable_places(
        room=room, circles=(CIRCLE,), presence=store,
    ) == ()
