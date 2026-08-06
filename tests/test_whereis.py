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
