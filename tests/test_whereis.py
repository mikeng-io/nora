"""`whereis` and its R-21 fail-closed disclosure seam (DEC-54).

The load-bearing test is `test_whereis_refuses_to_disclose_outside_the_circle`
(R-31): a conversation that is NOT a subset of the circle gets a REFUSAL, not a
position and not a silent omission. Revert `DisclosureGuard.permits` to «always
true» and it goes red — presence would leak into a room containing an outsider,
the inverse of R-21, which nothing else enforces.

The room fixtures mirror the live `Ridgeline` group (same shape as
`tests/unit/test_agent_room.py`). The presence reader is the REAL `PresenceStore`
against testcontainer Postgres, so degradation and age are read the way
production reads them.
"""

from __future__ import annotations

import json

import pytest

from nora.agent.room import Room
from nora.agent.whereis import Circle, DisclosureGuard, Whereis, check_disclosure_invariant
from nora.domain.presence import PresenceFix, WireState
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.presence import PresenceStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeReverseGeocoder, FakeRosterSource

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


def _whereis(persons, store, *, members, fail=False, tagger=None, metrics=None,
             refresher=None, geocoder=None, location_trusted=False):
    return Whereis(
        room=_room(persons, members=members, fail=fail, tagger=tagger),
        circle=CIRCLE, presence=store, refresher=refresher, geocoder=geocoder,
        location_trusted=location_trusted, metrics=metrics,
    )


class _FakeRefresher:
    """Records who was asked for, and optionally writes a FRESH fix on refresh —
    the collector's job in production. `ok` is what the HTTP round trip returns."""

    def __init__(self, store=None, *, fresh_ts=None, ok=True):
        self.calls: list[str] = []
        self._store = store
        self._fresh_ts = fresh_ts
        self._ok = ok

    async def refresh(self, *, person_id: str) -> bool:
        self.calls.append(person_id)
        if self._store is not None and self._fresh_ts is not None:
            await _record(self._store, person=person_id, ts=self._fresh_ts, address="Live Spot")
        return self._ok


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
    # DEC-156 follow-through: NO address in the payload — lat/lng instead,
    # for `reverse_geocode` to fetch one on demand.
    assert "address" not in out
    assert out["lat"] == 22.44 and out["lng"] == 114.02
    assert out["wire_state"] == "live"
    # Twenty minutes is inside one Life360 heartbeat: a live record.
    assert "stale" not in out
    assert out["recorded_iso"].startswith("2001-09-09T")


async def test_a_fix_past_the_record_edge_says_the_record_stopped(persons, store):
    """Past `STALE_AFTER_MS` the answer says what it is: the last recorded
    position, and that nothing came after. A stale fix is never presented as
    where the person is now, even when the refresh silently fails."""
    await _record(store, person="miles_novak", ts=NOW - 2 * 3_600_000)
    refresher = _FakeRefresher(ok=False)
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), refresher=refresher)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert out["stale"] is True
    assert out["recorded_iso"] in out["note"]
    assert "LAST RECORDED" in out["note"]
    assert "live refresh just now got no answer" in out["note"]


async def test_whereis_forces_a_live_refresh_before_reading(persons, store):
    """DEC-67: `where_is` is asking WHERE they ARE. With a refresher wired it
    pings for a fresh fix (which the collector writes) BEFORE the read, so the
    answer is the live one — not the stale stored fix. The refresh is asked only
    for the RESOLVED person, after disclosure passed."""
    await _record(store, person="miles_novak", ts=NOW - 20 * 60_000, address="Old Spot")
    refresher = _FakeRefresher(store, fresh_ts=NOW)      # writes a fresh fix on refresh
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), refresher=refresher)
    out = await w.locate("Miles", now_ms=NOW)
    assert refresher.calls == ["miles_novak"]                # asked for the resolved person
    assert out["status"] == "ok"
    assert out["age_seconds"] == 0                        # the LIVE fix, not the 20-min-old one
    assert "address" not in out


async def test_whereis_falls_back_to_the_stored_fix_when_the_refresh_fails(persons, store):
    """Never load-bearing: a refresh that times out / cannot reach the collector
    returns False and writes nothing, and `where_is` still answers from the last
    stored fix rather than «unavailable»."""
    await _record(store, person="miles_novak", ts=NOW - 20 * 60_000, address="Yoho Mall")
    refresher = _FakeRefresher(ok=False)                 # refresh failed, no fresh write
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), refresher=refresher)
    out = await w.locate("Miles", now_ms=NOW)
    assert refresher.calls == ["miles_novak"]
    assert out["status"] == "ok"
    assert "address" not in out                          # no address at all (DEC-156)


# ── on-demand reverse-geocode ────────────────────────────────────────────────
#
# Geocoding is billed per call, so it happens here, lazily, only when locate()
# has nothing cheaper to say — the call-count assertions below are the point of
# these tests, not incidental.

async def test_no_address_is_ever_geocoded_or_carried(persons, store):
    """DEC-156: locate() NEVER
    geocodes and NEVER carries an address — not Life360's own field, not an
    on-demand lookup. The geocoder stays wired but untouched; the coordinate
    is the fetch surface (`reverse_geocode` resolves it when asked)."""
    await _record(store, person="miles_novak", ts=NOW - 5 * 60_000, address="Grand Century Place")
    geocoder = FakeReverseGeocoder("should never be returned")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), geocoder=geocoder)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert "address" not in out
    assert out["lat"] == 22.44 and out["lng"] == 114.02
    assert geocoder.calls == []


async def test_a_saved_place_hit_never_reaches_the_geocoder(persons, store):
    """A household place name is the cheapest, best answer — checked BEFORE
    the geocoder is ever considered, address or no address."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    await _record(store, person="miles_novak", ts=NOW - 5 * 60_000, address=None)
    geocoder = FakeReverseGeocoder("should never be returned")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), geocoder=geocoder)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["place"] == "Home"
    assert geocoder.calls == []


async def test_no_geocoder_wired_falls_back_to_raw_coordinates(persons, store):
    """Unwired (offline/eval, or an operator who leaves it off): the
    last-resort shape holds — the model gets `lat`/`lng` rather than
    nothing, and nothing crashes for lacking a geocoder."""
    await _record(store, person="miles_novak", ts=NOW - 5 * 60_000, address=None)
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))  # geocoder=None
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert "address" not in out
    assert out["lat"] == 22.44
    assert out["lng"] == 114.02


async def test_a_geocode_failure_falls_back_to_raw_coordinates_not_a_crash(persons, store):
    """NEVER raises into the tool call — a geocoder outage costs an address,
    never the whole answer."""
    class _RaisingGeocoder:
        async def address_for(self, *, lat, lng):
            raise ConnectionError("geocode unreachable")

    await _record(store, person="miles_novak", ts=NOW - 5 * 60_000, address=None)
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), geocoder=_RaisingGeocoder())
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert "address" not in out
    assert out["lat"] == 22.44
    assert out["lng"] == 114.02


async def test_whereis_refuses_to_disclose_outside_the_circle(persons, store):
    """R-31 — the fail-closed seam. The conversation contains
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
    """Fail-closed on UNCERTAINTY, not just on a RESOLVED outsider. An account in
    the room that resolves to NO person_id — a stranger not in identity.toml — is unverifiable
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


async def test_a_location_trusted_room_discloses_outside_the_circle(persons, store):
    """DEC-74: with `location = true` on the conversation, the SUBSET check is
    relaxed — the operator trusts THIS room with the circle's positions even
    though a mapped member here (the stranger) is outside the circle. The exact
    room that REFUSES in `test_whereis_refuses_to_disclose_outside_the_circle`
    now DISCLOSES. Revert the `self._location_trusted or …` clause and this reds."""
    await _record(store, person="miles_novak", ts=NOW)
    w = _whereis(
        persons, store, members=(MILES, GRANT, STRANGER, NORA), location_trusted=True,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"          # disclosed, not refused


async def test_a_location_trusted_room_discloses_past_an_unmapped_account(persons, store):
    """DEC-79: `location = true` relaxes the `view.unnamed > 0` refusal too, so
    the room is one switch rather than two.

    A real group can permanently hold sealed-sender accounts (no phone number on
    the wire, never spoken, so nothing to key identity.toml on). Refusing on
    them would refuse EVERY `where_is` call in a room already marked
    `location = true`. Trust that cannot be expressed is not a safety property,
    it is a dead tool.

    The same room as `test_whereis_refuses_when_the_room_holds_an_unmapped_account`
    — that one keeps the strict default and must stay green beside this."""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    unmapped = frozenset({"+85299999999"})
    w = _whereis(
        persons, store, members=(MILES, GRANT, unmapped, NORA),
        location_trusted=True, metrics=metrics,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"          # disclosed, not refused
    assert metrics.get("nora_presence_disclosure_refusals_total", reason="unnamed") == 0


async def test_a_degraded_room_answers_unavailable_and_never_refused(persons, store):
    """R-27: an ERROR must not be dressed as a policy decision. `refused` tells
    the model «someone here is outside the circle» — a claim about who is in the
    room — but a degraded view means signal-cli could not be read at all, and
    under DEC-79 that is the ONLY refusal a trusted room can reach. A wedged
    daemon would otherwise render to the group as 「有人唔喺個 circle 入面」,
    which is false.

    The metric keeps `reason="degraded"` so the event stays diagnosable."""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    tagger = persons.resolve("signal", "+85290000101")
    w = _whereis(
        persons, store, members=(MILES, GRANT, NORA), fail=True,
        metrics=metrics, tagger=tagger,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"          # not "refused"
    assert "circle" not in out["note"]             # no false claim about the room
    assert "lat" not in out and "address" not in out
    assert metrics.get("nora_presence_disclosure_refusals_total", reason="degraded") == 1


async def test_a_waived_unnamed_guard_is_counted_and_never_silent(persons, store):
    """Relaxing the `unnamed` condition records the waiver. Without it a zero
    refusal counter could not tell «the flag works» from «the room filled up
    with strangers». The startup pre-flight warns once at boot and never again,
    so an account joining later would otherwise produce no signal at all.

    Delete the waiver counter and this reds while everything else stays green —
    which is exactly the failure mode it guards."""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    w = _whereis(
        persons, store, members=(MILES, GRANT, frozenset({"+85299999999"}), NORA),
        location_trusted=True, metrics=metrics,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"                                    # disclosed
    assert metrics.get("nora_presence_disclosure_waived_total", reason="unnamed") == 1
    assert metrics.get("nora_presence_disclosure_refusals_total", reason="unnamed") == 0


async def test_a_fully_mapped_trusted_room_waives_nothing(persons, store):
    """The counter must mean «a guard was overridden», not «the flag is on» —
    otherwise it cannot answer the question it exists for. A trusted room with
    nobody unmapped overrides nothing, so it records nothing."""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    w = _whereis(
        persons, store, members=(MILES, GRANT, NORA),
        location_trusted=True, metrics=metrics,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert metrics.get("nora_presence_disclosure_waived_total", reason="unnamed") == 0


async def test_a_location_trusted_room_still_discloses_nothing_on_a_degraded_view(
    persons, store,
):
    """The line DEC-79 does NOT cross, and the reason the flag is not simply
    «skip the guard»: `degraded` means the roster could not be READ, so there is
    no measurement to overrule — an R-27 `unavailable`, which is never a PASS.

    `unnamed` is the opposite: a roster Nora read successfully and found an
    unknown in. The operator can speak to what he read; he cannot vouch for a
    room nobody could see. Delete the `view.degraded` term and this reds.

    (The STATUS is `unavailable` rather than `refused` — see
    `test_a_degraded_room_answers_unavailable_and_never_refused`. What this test
    pins is that trust does not reach it and nothing is disclosed.)"""
    await _record(store, person="miles_novak", ts=NOW)
    metrics = Metrics()
    tagger = persons.resolve("signal", "+85290000101")
    w = _whereis(
        persons, store, members=(MILES, GRANT, NORA), fail=True,   # roster unreadable
        location_trusted=True, metrics=metrics, tagger=tagger,
    )
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"   # trust cannot substitute for a reading
    assert "lat" not in out and "address" not in out
    assert metrics.get("nora_presence_disclosure_refusals_total", reason="degraded") == 1
    # and it did NOT record a waiver — nothing was overridden, it could not read
    assert metrics.get("nora_presence_disclosure_waived_total", reason="unnamed") == 0


async def test_disclosure_refusal_metric_names_which_condition(persons, store):
    """`nora_presence_disclosure_refusals_total` carries
    WHICH of the three conditions fired, so a refusal is diagnosable from the
    metric alone instead of inferred. `not_subset` and `unnamed` are distinct."""
    await _record(store, person="miles_novak", ts=NOW)
    # not_subset — a mapped member (the stranger) is outside the circle, room untrusted
    m1 = Metrics()
    await _whereis(
        persons, store, members=(MILES, GRANT, STRANGER, NORA), metrics=m1,
    ).locate("Miles", now_ms=NOW)
    assert m1.get("nora_presence_disclosure_refusals_total", reason="not_subset") == 1
    assert m1.get("nora_presence_disclosure_refusals_total", reason="unnamed") == 0
    # unnamed — an account in the room resolves to no person_id
    m2 = Metrics()
    await _whereis(
        persons, store, members=(MILES, GRANT, frozenset({"+85299999999"}), NORA),
        metrics=m2,
    ).locate("Miles", now_ms=NOW)
    assert m2.get("nora_presence_disclosure_refusals_total", reason="unnamed") == 1
    assert m2.get("nora_presence_disclosure_refusals_total", reason="not_subset") == 0


async def test_whereis_is_unavailable_and_never_null_island_without_a_fix(persons, store):
    """No stored fix → `unavailable`, never a (0,0) null-island or a guess."""
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"
    assert "lat" not in out and "lng" not in out and "address" not in out


async def test_whereis_is_unavailable_when_the_room_cannot_be_read(persons, store):
    await _record(store, person="miles_novak", ts=NOW)
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), fail=True)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"


async def test_whereis_discloses_nothing_on_a_degraded_room_it_cannot_verify(
    persons, store,
):
    """A degraded (tagger-only) view cannot VERIFY group ⊆ circle, so the
    fail-closed guard never discloses on an unverifiable room.

    The STATUS is `unavailable`, not `refused` (R-27): the property that matters
    is that no position escapes, and calling an unread roster a policy refusal
    would put a false sentence about the room in Nora's mouth. See
    `test_a_degraded_room_answers_unavailable_and_never_refused`."""
    await _record(store, person="miles_novak", ts=NOW)
    tagger = persons.resolve("signal", "+85290000101")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA), fail=True, tagger=tagger)
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "unavailable"
    assert "lat" not in out and "address" not in out


async def test_whereis_not_found_for_a_name_not_in_the_room(persons, store):
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Nobody", now_ms=NOW)
    assert out["status"] == "not_found"


# ── the startup invariant ────────────────────────────────────────────────────

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
                  metrics=None, location_trusted=False):
    specs = build_tool_specs(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=room, transit_source=None,
        presence_source=presence_source, presence_circles=presence_circles,
        location_trusted=location_trusted,
    )
    return next(s for s in specs if s.name == "where_is")


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
    assert "address" not in out        # DEC-156: no address — lat/lng is the fetch surface


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
    assert "address" not in out        # DEC-156: no address — lat/lng is the fetch surface


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


# ── ADR 0015 §3: how to report a status rides in the result ─────────────────

PAT = frozenset({"+85268886688"})
_WITH_PAT = IDENTITY + """
[persons.pat_ho]
first_name = "Pat"
last_name  = "Ho"
aliases = ["Miles"]
[[persons.pat_ho.signal]]
phone = "+85268886688"
"""


async def test_every_non_ok_status_carries_a_note(persons, store, tmp_path):
    """ADR 0015 §3/§6: the description only NAMES the statuses; what to say
    for each is the result's `note`. Every non-ok code path is driven here, so
    a path that loses its note (the reporting rule for that status) goes red."""
    tagger = persons.resolve("signal", "+85290000101")
    path = tmp_path / "with_pat.toml"
    path.write_text(_WITH_PAT)
    with_pat = load_persons(path)
    both = Circle(circle_id=CIRCLE_ID, person_ids=frozenset({"miles_novak", "grant_carver",
                                                            "pat_ho"}))
    cases = {
        "unavailable/unwired": _whereis_tool(
            persons, room=_room(persons, members=(MILES, NORA)),
        ).call("Miles"),
        "unavailable/room": _whereis(
            persons, store, members=(MILES, NORA), fail=True,
        ).locate("Miles", now_ms=NOW),
        "unavailable/degraded": _whereis(
            persons, store, members=(MILES, NORA), fail=True, tagger=tagger,
        ).locate("Miles", now_ms=NOW),
        "unavailable/no_fix": _whereis(
            persons, store, members=(MILES, NORA),
        ).locate("Miles", now_ms=NOW),
        "refused": _whereis(
            persons, store, members=(MILES, STRANGER, NORA),
        ).locate("Miles", now_ms=NOW),
        "not_found": _whereis(
            persons, store, members=(MILES, NORA),
        ).locate("Nobody", now_ms=NOW),
        "ambiguous": Whereis(
            room=_room(with_pat, members=(MILES, PAT, NORA)), circle=both,
            presence=store,
        ).locate("Miles", now_ms=NOW),
    }
    for case, pending in cases.items():
        out = await pending
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case


async def test_an_ok_note_carries_the_rules_its_answer_needs(persons, store):
    """The reporting rules that left the description (carry the age, never
    read lat/lng aloud) arrive with the `ok` they are about, and the place
    and movement clauses only when those fields are there."""
    await _record(store, person="miles_novak", ts=NOW - 20 * 60_000)
    out = await _whereis(persons, store, members=(MILES, GRANT, NORA)).locate(
        "Miles", now_ms=NOW,
    )
    assert out["status"] == "ok"
    assert "age_seconds" in out["note"]
    assert "reverse_geocode" in out["note"]
    assert "`place`" not in out["note"]           # no saved place in this answer
    assert "promise" not in out["note"]           # and no movement either


async def test_location_trust_threads_through_build_tool_specs_to_the_tool(persons, store):
    """DEC-74 middle-hop coverage: the flag must survive the
    `build_tool_specs → Whereis` hop, NOT just the two endpoints. Same non-subset
    room (the stranger is mapped but outside the circle) built two ways: trusted
    DISCLOSES, untrusted REFUSES. This reds if `build_tool_specs` drops the
    argument OR hardcodes it either direction — the exact silent regressions the
    endpoint-only tests could not catch."""
    await _record(store, person="miles_novak", ts=NOW, address="Trust Tower")
    room = _room(persons, members=(MILES, GRANT, STRANGER, NORA))
    trusted = await _whereis_tool(
        persons, room=room, presence_source=store, presence_circles=(CIRCLE,),
        location_trusted=True,
    ).call("Miles")
    assert trusted["status"] == "ok" and "address" not in trusted
    strict = await _whereis_tool(
        persons, room=room, presence_source=store, presence_circles=(CIRCLE,),
        location_trusted=False,
    ).call("Miles")
    assert strict["status"] == "refused" and "Trust Tower" not in str(strict)


async def test_location_trust_threads_through_the_registry_build(persons, store):
    """The registry hop (`ToolRegistry.build(location_trusted=…) → specs()`): the
    same policy-driven bool must reach the built `where_is` tool. Trusted builds
    disclose, the default (strict) build refuses — catches a drop in `build()`/
    `specs()`."""
    from nora.agent.registry import ToolRegistry

    await _record(store, person="miles_novak", ts=NOW, address="Registry Rd")
    reg = ToolRegistry(presence=store, circles=(CIRCLE,))
    room = _room(persons, members=(MILES, GRANT, STRANGER, NORA))
    common = {
        "store": None, "conversation_id": GROUP, "conversation_type": "group",
        "persons": persons, "honcho": None, "deliverer": None, "room": room,
        "metrics": Metrics(),
    }
    def _where_is(tools):
        return next(t for t in tools if t.tool_name == "where_is")

    trusted = _where_is(reg.build(**common, location_trusted=True))
    strict = _where_is(reg.build(**common))            # default location_trusted=False
    assert (await trusted._tool_func(person="Miles"))["status"] == "ok"
    assert (await strict._tool_func(person="Miles"))["status"] == "refused"


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
    (circle,) = circles
    assert circle.circle_id == "c1"
    assert circle.person_ids == frozenset({"miles_novak", "grant_carver"})
    # R-47: the circle carries its members RESOLVED, not just their ids — that
    # is what lets `where_is` match a NAME against the circle rather than
    # against room membership. Ids alone cannot answer "who did they mean?".
    assert {p.person_id for p in circle.people} == {"miles_novak", "grant_carver"}


def test_circles_from_persons_is_empty_for_an_identity_with_no_life360_blocks(persons):
    assert circles_from_persons(persons) == ()


# ── the at-place answer + saved-place resolution scope ───────────────────────

from nora.agent.whereis import resolvable_places  # noqa: E402
from nora.domain.presence import SavedPlace  # noqa: E402


def _saved(name, *, lat=22.44, lng=114.02, radius=300.0, circle=CIRCLE_ID):
    return SavedPlace(circle_id=circle, place_id=f"p-{name}", name=name,
                      lat=lat, lng=lng, radius_m=radius, updated_ms=NOW)


async def test_whereis_names_the_saved_place_the_fix_sits_inside(persons, store):
    """A fix inside a place's geofence answers with the household's own name
    for the spot, AND the address — both, so the model never guesses. Geometry
    runs server-side; the model sees only names."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Jvvilxgd Road")
    w = _whereis(persons, store, members=(MILES, GRANT, NORA))
    out = await w.locate("Miles", now_ms=NOW)
    assert out["status"] == "ok"
    assert out["place"] == "Home"
    # DEC-156 follow-through: no address — the coordinate is the fetch surface.
    assert "address" not in out
    assert "lat" in out and "lng" in out
    assert "`place`" in out["note"]      # ADR 0015 §3: prefer the household's name


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
    assert "address" not in out        # DEC-156: no address — lat/lng is the fetch surface


async def test_resolvable_places_returns_the_subset_circles_places(persons, store):
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    room = _room(persons, members=(MILES, GRANT, NORA))
    places = await resolvable_places(room=room, circles=(CIRCLE,), presence=store)
    assert [p.name for p in places] == ["Home"]


async def test_a_non_subset_room_resolves_no_saved_place(persons, store):
    """R-35: a room containing anyone outside the circle — or
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


async def test_a_location_trusted_room_resolves_saved_places_past_an_unmapped_account(
    persons, store,
):
    """DEC-79 reaches the SECOND disclosure surface too: `saved_places` must not
    answer «there are no saved places» in a room where `where_is` discloses.
    Both refuse on the same unmappable accounts, and it would silently kill the
    `where_work` journey (DEC-77/78), which routes onto `saved_places`. Drop the
    `location_trusted` term from `resolvable_places` and this reds."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    room = _room(persons, members=(MILES, GRANT, frozenset({"+85299999999"}), NORA))
    places = await resolvable_places(
        room=room, circles=(CIRCLE,), presence=store, location_trusted=True,
    )
    assert [p.name for p in places] == ["Home"]


async def test_a_location_trusted_room_still_resolves_nothing_on_an_unreadable_roster(
    persons, store,
):
    """The same line `where_is` holds: trust overrules what a roster SAYS, never
    the absence of one. `()` here rather than `None` — the room was read well
    enough to know it cannot be verified; `None` is reserved for a read that
    THREW, which is a different fact (see the next test)."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    room = _room(persons, members=(MILES, NORA), fail=True)
    assert await resolvable_places(
        room=room, circles=(CIRCLE,), presence=store, location_trusted=True,
    ) == ()


class _ThrowingPresence:
    """A presence store whose read RAISES — an unreachable Postgres, not an
    empty table. Behaves like the real store right up to the failure, which is
    the point: a fake that simply returned `[]` would make the bug invisible."""

    async def latest_fix(self, **_kw):
        raise RuntimeError("presence store unreachable")

    async def places_for(self, **_kw):
        raise RuntimeError("presence store unreachable")

    async def window(self, **_kw):
        raise RuntimeError("presence store unreachable")


async def test_a_failed_read_is_none_and_never_an_empty_list(persons):
    """R-27: `()` and a THROWN read are different values; collapsing them would
    render an unreachable presence store as 「你冇 save 過任何地方」 — a confident wrong
    answer — with `status="ok"` in the metrics. An ERROR is never a PASS: `()`
    means «no names you may have», `None` means «I could not check».

    Revert the `except` branch to `return ()` and this reds."""
    room = _room(persons, members=(MILES, GRANT, NORA))
    out = await resolvable_places(
        room=room, circles=(CIRCLE,), presence=_ThrowingPresence(),
    )
    assert out is None                      # not (), which would read as «none saved»


# ── the movement read (DEC-61) ───────────────────────────────────────────────

async def test_whereis_reports_measured_movement_towards_a_saved_place(persons, store):
    """Two fixes closing on the saved place: `moving` and `towards` — measured
    facts, not a forecast. The fix history is the evidence."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[
        _saved("Home", lat=22.46, lng=114.02, radius=300.0),
    ])
    await _record(store, person="miles_novak", ts=NOW - 300_000, address="Far Road")
    # ~1.1km nearer Home five minutes later.
    await store.record_fix(fix=PresenceFix(
        circle_id=CIRCLE_ID, person_id="miles_novak", ts=NOW - 60_000,
        lat=22.45, lng=114.02, wire_state=WireState.LIVE,
        recorded_at=NOW - 60_000, address="Near Road", is_driving=True,
    ))
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert out["moving"] is True
    assert out["towards"] == "Home"
    assert out["driving"] is True


async def test_whereis_reports_still_when_recent_fixes_are_together(persons, store):
    await _record(store, person="miles_novak", ts=NOW - 300_000, address="Same Road")
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Same Road")
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert out["moving"] is False
    assert "towards" not in out


async def test_one_fix_has_no_direction_and_claims_none(persons, store):
    """A single point cannot say moving or still — the keys are ABSENT, the
    honest «cannot tell» (R-5: absence, never a guessed boolean)."""
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Jvvilxgd Road")
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert "moving" not in out and "towards" not in out


async def test_inside_a_place_the_containment_wins_over_movement(persons, store):
    """«at Home» and «moving» cannot both be the headline: inside a geofence
    the movement read is skipped entirely."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    await _record(store, person="miles_novak", ts=NOW - 300_000, address="Far Road")
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Home Road")
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert out["place"] == "Home"
    assert "moving" not in out and "towards" not in out


# ── speed (Life360's own reported ground speed, converted to km/h at parse
# time by `nora.infra.life360.members`; `where_is` only relays it) ───────────

async def test_whereis_reports_speed_when_above_the_jitter_floor(persons, store):
    await store.record_fix(fix=PresenceFix(
        circle_id=CIRCLE_ID, person_id="miles_novak", ts=NOW - 60_000,
        lat=22.44, lng=114.02, wire_state=WireState.LIVE,
        recorded_at=NOW - 60_000, address="Tjnw Highway", speed_kmh=62.3,
    ))
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert out["speed_kmh"] == 62.3
    assert "never promise" in out["note"]   # ADR 0015 §3: measured, not intent


async def test_whereis_omits_speed_when_the_fix_carries_none(persons, store):
    """Life360's own `-1` "unknown" sentinel is already `None` by the time a
    fix reaches the store — `where_is` must not render that as stationary."""
    await _record(store, person="miles_novak", ts=NOW - 60_000, address="Jvvilxgd Road")
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert "speed_kmh" not in out


async def test_whereis_omits_speed_inside_a_saved_place(persons, store):
    """«at Home» and a speed reading cannot both be the headline — indoor GPS
    jitter routinely exceeds the floor, and containment already wins over
    `moving`/`towards` for the identical reason."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    await store.record_fix(fix=PresenceFix(
        circle_id=CIRCLE_ID, person_id="miles_novak", ts=NOW - 60_000,
        lat=22.44, lng=114.02, wire_state=WireState.LIVE,
        recorded_at=NOW - 60_000, address="Home Road", speed_kmh=8.0,
    ))
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert out["place"] == "Home"
    assert "speed_kmh" not in out


async def test_whereis_omits_speed_below_the_jitter_floor(persons, store):
    """A near-zero speed off a stationary phone's GPS noise is omitted, not
    rendered as `0` (R-5: absence, never a manufactured number)."""
    await store.record_fix(fix=PresenceFix(
        circle_id=CIRCLE_ID, person_id="miles_novak", ts=NOW - 60_000,
        lat=22.44, lng=114.02, wire_state=WireState.LIVE,
        recorded_at=NOW - 60_000, address="Jvvilxgd Road", speed_kmh=1.2,
    ))
    out = await _whereis(persons, store, members=(MILES, NORA)).locate(
        "Miles", now_ms=NOW
    )
    assert out["status"] == "ok"
    assert "speed_kmh" not in out


# ── the route tool's saved-place GLUE ────────────────────────────────────────
#
# The seam (`resolvable_places`) and the client (`local_places`) are tested
# separately; these two tests cover the glue at tools.py that turns
# resolvable_places into the client's map, so reverting it to
# `local_places = None` cannot leave R-35's end-to-end path inert under a green
# suite. Subset room → the client receives the Home
# map under the casefolded key; non-subset room → it receives None.

from nora.agent.tools import build_tools  # noqa: E402


class _RecordingRoute:
    """Records the `local_places` it was handed, then answers a fixed ok."""

    def __init__(self):
        self.local_places = "unset"

    async def route(self, origin, destination, *, mode="TRANSIT", arrive_by="",
                    by="", prefer="", depart_after="", local_places=None):
        self.local_places = local_places
        return {"status": "ok", "origin": origin, "destination": destination,
                "mode": "TRANSIT", "duration_seconds": 60, "legs": []}


def _route_tool(persons, store, *, members):
    rec = _RecordingRoute()
    tools = build_tools(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=members),
        transit_source=None, route_source=rec,
        presence_source=store, presence_circles=(CIRCLE,),
        question="點去", context_window=40,
    )
    return {t.tool_name: t for t in tools}["route"], rec


async def test_a_subset_room_hands_the_route_client_the_saved_place_map(persons, store):
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    tool, rec = _route_tool(persons, store, members=(MILES, GRANT, NORA))
    out = await tool._tool_func(origin="home", destination="Yoho Mall")
    assert out["status"] == "ok"
    assert rec.local_places is not None
    # Casefolded key, and it resolves to the stored Home coordinate.
    assert "home" in rec.local_places
    assert rec.local_places["home"].name == "Home"


async def test_a_non_subset_room_hands_the_route_client_no_map(persons, store):
    """R-35 end-to-end through the tool: an outsider in the room means the glue
    passes None, so «Home» falls through to Google's public lookup — the guarded
    name is never resolved locally."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    tool, rec = _route_tool(persons, store, members=(MILES, GRANT, STRANGER, NORA))
    out = await tool._tool_func(origin="home", destination="Yoho Mall")
    assert out["status"] == "ok"
    assert rec.local_places is None


# ── saved_places tool: list names, model picks (the model matches
#    «屋企»→"Home" semantically, no casefold filter) ──────────────────────────

def _saved_places_tool(persons, store, *, members, location_trusted=False,
                       metrics=None):
    tools = build_tools(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=_room(persons, members=members),
        transit_source=None,
        presence_source=store, presence_circles=(CIRCLE,),
        location_trusted=location_trusted,
        question="", context_window=40,
    )
    return {t.tool_name: t for t in tools}["saved_places"]


async def test_saved_places_lists_names_for_a_subset_room(persons, store):
    """The model gets NAMES (never coordinates) and picks the match itself."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[
        _saved("Home"), _saved("Miles Home"), _saved("Acme Tower"),
    ])
    tool = _saved_places_tool(persons, store, members=(MILES, GRANT, NORA))
    out = await tool._tool_func()
    assert out["status"] == "ok"
    assert out["names"] == ["Acme Tower", "Home", "Miles Home"]
    # No coordinate anywhere in the payload.
    assert "lat" not in json.dumps(out) and "22.4" not in json.dumps(out)


async def test_saved_places_is_empty_for_a_non_subset_room(persons, store):
    """R-35 fall-through: an outsider in the room means NO names surface —
    silently, so the guarded names' existence is never confirmed."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    tool = _saved_places_tool(persons, store, members=(MILES, GRANT, STRANGER, NORA))
    out = await tool._tool_func()
    assert out["status"] == "ok"
    assert out["names"] == []


async def test_saved_places_lists_names_in_a_location_trusted_room(persons, store):
    """DEC-79's second surface, end-to-end through the TOOL rather than the seam.

    This is the `where_work` journey DEC-77/78 routed onto `saved_places`: in a
    room with unmapped accounts and `location = true` it must not answer 「冇 save
    過任何地方」. The room here is the same one
    `test_saved_places_is_empty_for_a_non_subset_room` proves stays empty
    untrusted — the flag is the only difference."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("返工")])
    tool = _saved_places_tool(
        persons, store, members=(MILES, GRANT, frozenset({"+85299999999"}), NORA),
        location_trusted=True,
    )
    out = await tool._tool_func()
    assert out["status"] == "ok"
    assert out["names"] == ["返工"]


async def test_saved_places_says_unavailable_when_the_read_fails(persons):
    """R-27 end-to-end. A thrown read must not render as `{"status": "ok",
    "names": []}` — with the presence store unreachable Nora would say
    「你冇 save 過任何地方」, a confident wrong answer, while
    `nora_tool_total{tool="saved_places",status="ok"}` read healthy. The model
    must be told she could not look, not that there is nothing.

    Revert `resolvable_places`' `except` branch to `return ()` and this reds."""
    metrics = Metrics()
    tool = _saved_places_tool(
        persons, _ThrowingPresence(), members=(MILES, GRANT, NORA), metrics=metrics,
    )
    out = await tool._tool_func()
    assert out["status"] == "unavailable"
    assert "names" not in out                    # never an empty list on a failure
    m = "nora_tool_calls_total"
    assert metrics.get(m, tool="saved_places", status="unavailable") == 1
    assert metrics.get(m, tool="saved_places", status="ok") == 0


async def test_saved_places_unavailable_when_presence_not_wired(persons):
    tools = build_tools(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, NORA)),
        transit_source=None, question="", context_window=40,
    )
    tool = {t.tool_name: t for t in tools}["saved_places"]
    out = await tool._tool_func()
    assert out["status"] == "unavailable"


async def test_every_non_ok_saved_places_carries_a_dash_free_note(persons):
    """ADR 0015 §3/§6: the description only NAMES `unavailable`; what to say
    is the result's `note`. Both unavailable paths (unwired, failed read)."""
    unwired = {t.tool_name: t for t in build_tools(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=Metrics(),
        room=_room(persons, members=(MILES, NORA)),
        transit_source=None, question="", context_window=40,
    )}["saved_places"]
    failed = _saved_places_tool(persons, _ThrowingPresence(), members=(MILES, GRANT, NORA))
    for case, tool in (("unwired", unwired), ("failed", failed)):
        out = await tool._tool_func()
        assert out["status"] == "unavailable", case
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case


async def test_a_saved_places_ok_note_carries_the_rules_its_list_needs(persons, store):
    """How to use the list left the description and rides with it: the
    match-and-pass clause only when there are names, the ask-instead clause
    only when there are none, and the address clause only with `addresses`."""
    await store.replace_places(circle_id=CIRCLE_ID, places=[_saved("Home")])
    named = await _saved_places_tool(persons, store, members=(MILES, GRANT, NORA))._tool_func()
    assert named["names"] == ["Home"]
    assert "pass that name to route" in named["note"]
    assert "no saved names" not in named["note"]
    assert "street address" not in named["note"]          # no `addresses` here

    empty = await _saved_places_tool(
        persons, store, members=(MILES, GRANT, STRANGER, NORA),
    )._tool_func()
    assert empty["names"] == []
    assert "no saved names" in empty["note"]
    assert "pass that name to route" not in empty["note"]

    await store.replace_places(circle_id=CIRCLE_ID, places=[
        SavedPlace(circle_id=CIRCLE_ID, place_id="p-work", name="Work", lat=22.44,
                   lng=114.02, radius_m=300.0, updated_ms=NOW, address="1 Main St"),
    ])
    addressed = await _saved_places_tool(persons, store, members=(MILES, GRANT, NORA))._tool_func()
    assert addressed["addresses"] == {"Work": "1 Main St"}
    assert "street address" in addressed["note"]
    for out in (named, empty, addressed):
        assert "—" not in out["note"] and "–" not in out["note"]


# ---------------------------------------------------------------------------
# R-47 — the circle is who may be ASKED ABOUT; the room is only the audience
# ---------------------------------------------------------------------------


async def test_a_circle_member_who_is_not_in_the_room_can_still_be_asked_about(
    persons, store,
):
    """Only an identified person can use where_is, and it can ask about anyone
    in the circle.

    The target is resolved against the circle, not `RoomView.find` (room
    membership): a friend who shares her location with Nora but is not in THIS
    group must not answer «nobody in this conversation goes by that», a false
    sentence about a person Nora knows.

    The room still gates the AUDIENCE — it is checked in full before this
    resolution runs — but who may be asked about is the CIRCLE.
    """
    circle = Circle(
        circle_id=CIRCLE_ID,
        person_ids=frozenset({"miles_novak", "grant_carver"}),
        people=(persons.by_person["grant_carver"],),
    )
    await _record(store, person="grant_carver", ts=NOW)
    # A room WITHOUT Grant in it — only Nora and Miles.
    whereis = Whereis(
        room=_room(persons, members=(MILES, NORA)),
        circle=circle, presence=store, location_trusted=True,
    )

    out = await whereis.locate("Grant", now_ms=NOW)

    assert out["status"] == "ok", (
        "a circle member must be askable about even when absent from the room"
    )


async def test_somebody_in_the_room_but_outside_the_circle_still_resolves(
    persons, store,
):
    """The union's other half, and why the room is not simply dropped.

    Someone present but NOT in the circle has no fix to report — but they are a
    person Nora knows, so the honest answer is «I have no recorded location for
    them», not «nobody goes by that». Resolving against the circle ALONE would
    turn that into a `not_found` lie.
    """
    circle = Circle(
        circle_id=CIRCLE_ID,
        person_ids=frozenset({"miles_novak"}),
        people=(persons.by_person["miles_novak"],),
    )
    whereis = Whereis(
        room=_room(persons, members=(MILES, GRANT, NORA)),
        circle=circle, presence=store, location_trusted=True,
    )

    out = await whereis.locate("Grant", now_ms=NOW)

    assert out["status"] == "unavailable"
    assert "Grant" in json.dumps(out, ensure_ascii=False)


async def test_r47_holds_in_an_ordinary_untrusted_room(persons, store):
    """R-47 in the default configuration.

    R-47's other tests pass `location_trusted=True`, which short-circuits BOTH
    guards R-47 sits behind, so they cannot distinguish "resolves against the
    circle" from "resolves against the circle only when the disclosure gate is
    waived". Every conversation without an explicit `location = true` is
    untrusted, so this is the configuration that needs its own test.

    Here the room is a genuine subset of the circle and holds no unmapped
    account, so the gate passes on its own merits and the widened target
    resolution is what is actually under test.
    """
    circle = Circle(
        circle_id=CIRCLE_ID,
        person_ids=frozenset({"miles_novak", "grant_carver"}),
        people=(persons.by_person["grant_carver"],),
    )
    await _record(store, person="grant_carver", ts=NOW)
    whereis = Whereis(
        room=_room(persons, members=(MILES, NORA)),
        circle=circle, presence=store, location_trusted=False,
    )

    out = await whereis.locate("Grant", now_ms=NOW)

    assert out["status"] == "ok", (
        "a circle member absent from the room must be askable about in an "
        "ORDINARY room, not only in a location-trusted one"
    )


def test_the_disclosure_guard_never_permits_a_room_with_no_resolved_people():
    """`frozenset() <= circle` is true of every circle, and an empty roster is what
    a room of only unmapped accounts reads as: vacuous truth verified nothing.
    Every caller passes its roster through this guard."""
    guard = DisclosureGuard(Circle(circle_id="c", person_ids=frozenset({"p1", "p2"})))
    assert guard.permits(frozenset({"p1"}))
    assert not guard.permits(frozenset())
