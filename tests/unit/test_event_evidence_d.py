"""The pure half of an event's evidence (migration `0086_event_citations`): the
lineage computed from citations, the citation checker, per-field reasoning, the
set of statuses a session may still move. No database:
`tests/infra/test_event_citations.py` is the same behaviours against real
Postgres.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-d.json`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from typing import Any

import pytest

from nora.app.cognition_wiring import open_events_reader
from nora.domain.gatherings import (
    EventCitation,
    EventEvidence,
    FieldMeta,
    Gathering,
    GatheringStatus,
    InvalidEventFieldModeError,
    event_lineage,
    field_meta_from_json,
    field_meta_to_json,
    field_mode,
    merge_field_meta,
    render_field,
)
from nora.domain.lineage import Lineage, LineageEntry, message, unresolved
from nora.domain.note_body import NoteBodyError, Shown, ShownMessage, check_citations
from nora.testing.gatherings import FakeGatheringStore

T0 = dt.datetime(2026, 10, 2, 11, 0, tzinfo=dt.UTC)


def _event(**over: Any) -> Gathering:
    base: dict[str, Any] = {
        "id": uuid.uuid4(), "conversation_id": "ga", "title": "dinner", "created_by": "p_ada",
        "when": None, "venue_text": None, "venue_place_id": None, "venue_name": None,
        "venue_address": None, "venue_rating": None, "venue_price_level": "",
        "venue_maps_url": None, "venue_resolved_at": None,
        "status": GatheringStatus.PROPOSED, "reminder_id": None, "created_at": T0,
        "unit_id": "u", "platform": "signal",
    }
    base.update(over)
    return Gathering(**base)


def _cite(part: str, ref: str, store: str = "message") -> EventCitation:
    return EventCitation(part=part, store=store, ref=ref)


# -- lineage from citations --------------------------------------------------

def test_each_stated_field_rests_on_the_room_its_own_citations_were_said_in():
    lineage = event_lineage(
        Lineage(), EventEvidence(
            parts=frozenset({"what", "place"}),
            citations=(_cite("what", "1"), _cite("place", "2")),
        ),
        message_rooms={1: "signal:ga", 2: "signal:gb"}, rollup_lineages={}, origin="signal:ga",
    )
    assert lineage.section("what") == (message("signal:ga"),)
    assert lineage.section("place") == (message("signal:gb"),)
    assert lineage.section("start") == ()


def test_a_restated_field_is_replaced_and_an_untouched_one_is_kept():
    held = Lineage.of({"what": [message("signal:ga")], "place": [message("signal:ga")]})
    lineage = event_lineage(
        held, EventEvidence(parts=frozenset({"place"}), citations=(_cite("place", "2"),)),
        message_rooms={2: "signal:gb"}, rollup_lineages={}, origin="signal:ga",
    )
    assert lineage.section("place") == (message("signal:gb"),)
    assert lineage.section("what") == (message("signal:ga"),)


def test_what_cannot_be_resolved_is_unresolved_at_the_origin_never_widened():
    lineage = event_lineage(
        Lineage(), EventEvidence(
            parts=frozenset({"what", "end", "people"}),
            citations=(
                _cite("what", "999"), _cite("end", "not-a-number"),
                _cite("people", "colocation|u|a,b|x/y", "rollup"),
            ),
        ),
        message_rooms={}, rollup_lineages={"colocation|u|a,b|x/y": None}, origin="signal:ga",
    )
    for part in ("what", "end", "people"):
        assert lineage.section(part) == (unresolved("signal:ga"),)


def test_a_rollup_row_contributes_the_entries_its_own_lineage_holds():
    presence = Lineage.of({"row": [LineageEntry("presence", "p_ada", None)]})
    lineage = event_lineage(
        Lineage(), EventEvidence(
            parts=frozenset({"people"}), citations=(_cite("people", "k", "rollup"),),
        ),
        message_rooms={}, rollup_lineages={"k": presence}, origin="signal:ga",
    )
    assert lineage.section("people") == (LineageEntry("presence", "p_ada", None),)


def test_the_room_a_turn_was_said_in_is_its_evidence_when_it_cites_nothing():
    lineage = event_lineage(
        Lineage(), EventEvidence(parts=frozenset({"what", "status"}), room="signal:ga"),
        message_rooms={}, rollup_lineages={}, origin="signal:ga",
    )
    assert lineage.section("what") == lineage.section("status") == (message("signal:ga"),)


def test_a_field_with_nothing_behind_it_is_unresolved_not_empty():
    lineage = event_lineage(
        Lineage(), EventEvidence(parts=frozenset({"what"})),
        message_rooms={}, rollup_lineages={}, origin="signal:ga",
    )
    assert lineage.section("what") == (unresolved("signal:ga"),)


# -- a lineage an event can be rendered by --------------------------

def test_an_event_with_a_computed_lineage_renders_where_its_evidence_may_and_not_elsewhere():
    """Without a writer that sets an event's lineage, under `[lineage] mode =
    on` every event has an empty one, which renders only in its origin room. A
    computed lineage is the real predicate's input: it passes in the room the
    evidence was said in and fails in an unrelated one."""
    from nora.agent.lineage import LineagePolicy, visible_sections
    from nora.policy import Conversation

    def room(cid: str, persons: tuple[str, ...]) -> Conversation:
        return Conversation(
            platform="signal", ids=frozenset({cid}), persons=frozenset(persons),
            unit="u", label=cid, location=False, proactive=False,
        )

    policy = LineagePolicy.from_policy(
        type("P", (), {"conversations": (room("ga", ("p_ada",)), room("gb", ("p_ben",)))})(),
        mode="on",
    )
    lineage = event_lineage(
        Lineage(), EventEvidence(parts=frozenset({"what"}), citations=(_cite("what", "1"),)),
        message_rooms={1: "signal:ga"}, rollup_lineages={}, origin="signal:ga",
    )
    here = policy.render_room(platform="signal", conversation_id="ga")
    there = policy.render_room(platform="signal", conversation_id="gb")
    assert visible_sections(lineage, ("what",), here, origin="signal:ga") == {"what": True}
    assert visible_sections(lineage, ("what",), there, origin="signal:ga") == {"what": False}
    # Unwritten: nothing distinguishes it from any other,
    # and the room that holds it is the only one that may show it.
    assert visible_sections(Lineage(), ("what",), there, origin="signal:ga") == {"what": False}


# -- citations, held to what was shown --------------------------------------

SHOWN = Shown(
    rendered=(ShownMessage(1, "Ada", "p_ada"), ShownMessage(2, "Nora", None, is_nora=True)),
    carried=frozenset({7}), rollup_keys=frozenset({"stay|p|a/b"}),
)


def test_a_shown_message_a_carried_one_and_a_read_rollup_row_pass():
    assert check_citations(["1", " 7 ", "rollup(stay|p|a/b)", "1"], SHOWN, what="w") == (
        (1, 7), ("stay|p|a/b",),
    )


@pytest.mark.parametrize(("cited", "rule"), [
    ([], "uncited"), (["x"], "uncited"), (["rollup()"], "uncited"),
    (["99"], "unknown_id"), (["2"], "cites_nora"), (["rollup(stay|q|a/b)"], "unknown_rollup_key"),
])
def test_anything_else_is_refused_with_the_rule_a_note_would_name(cited, rule):
    with pytest.raises(NoteBodyError) as caught:
        check_citations(cited, SHOWN, what="event_propose")
    assert caught.value.rule == rule


# -- per-field reasoning -----------------------------------------------------

def test_a_field_carries_only_a_mode_and_a_confidence_the_event_tier_allows():
    with pytest.raises(InvalidEventFieldModeError):
        FieldMeta(field="place", reasoning="induced")
    with pytest.raises(InvalidEventFieldModeError):
        FieldMeta(field="place", confidence="certain")
    with pytest.raises(InvalidEventFieldModeError):
        FieldMeta(field="colour")
    assert FieldMeta(field="place", reasoning="abduced", confidence="low").reasoning == "abduced"


def test_a_write_restates_its_own_fields_and_leaves_the_rest():
    held = (FieldMeta("what", "told"), FieldMeta("place", "told", "high"))
    merged = merge_field_meta(held, (FieldMeta("place", "deduced", "medium"),
                                     FieldMeta("people", "deduced")))
    assert [(m.field, m.reasoning) for m in merged] == [
        ("what", "told"), ("place", "deduced"), ("people", "deduced"),
    ]


def test_the_column_round_trips_and_a_bad_entry_reads_as_not_stated():
    meta = (FieldMeta("place", "told", "high", 5), FieldMeta("people", "deduced"))
    assert field_meta_from_json(field_meta_to_json(meta)) == meta
    assert field_meta_from_json({"place": {"reasoning": "induced"}, "colour": {}}) == (
        FieldMeta("place"),
    )
    assert field_meta_from_json(None) == () and field_meta_from_json([1]) == ()


# -- the events a session may still move ------------------------------------

async def test_a_session_is_shown_and_may_move_every_event_before_happened():
    store = FakeGatheringStore()
    for s in GatheringStatus:
        await store.create(_event(status=s, title=s.value))
    read = open_events_reader(store)
    assert {e.title for e in await read("u", "signal", "ga")} == {
        "staged", "proposed", "confirmed",
    }


async def test_a_session_cannot_read_another_units_event_with_the_same_room_id():
    """Conversation ids are an id space per platform. Filtering only by the id
    let a second unit's event with the same id into this unit's agenda."""
    store = FakeGatheringStore()
    await store.create(_event(title="ours", unit_id="unit-a"))
    await store.create(_event(title="theirs", unit_id="unit-b"))

    read = open_events_reader(store)
    assert {e.title for e in await read("unit-a", "signal", "ga")} == {"ours"}


async def test_a_session_cannot_read_the_other_platforms_event_with_the_same_room_id():
    """The same conversation string on two platforms is two rooms. The stored
    platform, not the id, decides which one a signal read sees."""
    store = FakeGatheringStore()
    await store.create(_event(title="signal", unit_id="unit-a", platform="signal"))
    await store.create(_event(title="whatsapp", unit_id="unit-a", platform="whatsapp"))

    read = open_events_reader(store)
    assert {e.title for e in await read("unit-a", "signal", "ga")} == {"signal"}
    assert {e.title for e in await read("unit-a", "whatsapp", "ga")} == {"whatsapp"}


async def test_a_legacy_event_with_no_stored_platform_is_quarantined():
    """A pre-`0128` row has no durable platform owner. It is invisible even to
    the unit and room it was written in, because current policy cannot say what
    the policy was then. Nothing infers one."""
    store = FakeGatheringStore()
    await store.create(_event(title="legacy", unit_id="", platform=""))

    read = open_events_reader(store)
    assert not await read("unit-a", "signal", "ga")
    assert not await read("", "signal", "ga")


# -- how an abduced field is voiced -------------------------------

def test_an_abduced_field_is_voiced_as_a_question_and_no_assent_upgrades_it():
    event = _event(venue_text="place two", field_meta=(
        FieldMeta("what", "told", "high"), FieldMeta("place", "abduced", "low"),
    ))
    assert render_field(event, "what", "dinner") == "what: dinner"
    asked = render_field(event, "place", "place two")
    assert asked.startswith("place: place two?") and "guess" in asked
    # A person's «yeah» is no write, and a write that states OTHER fields (the
    # status moving on their word) leaves this one abduced: only a write that
    # restates the place, on evidence of its own, can change how it was reached.
    after = replace(event, field_meta=merge_field_meta(
        event.field_meta, (FieldMeta("status", "told", "high"),),
    ))
    assert field_mode(after, "place") == "abduced"
    assert render_field(after, "place", "place two") == asked
    restated = replace(event, field_meta=merge_field_meta(
        event.field_meta, (FieldMeta("place", "told", "high"),),
    ))
    assert render_field(restated, "place", "place two") == "place: place two"


def test_a_field_with_no_mode_of_its_own_reads_the_rows_and_none_is_stated():
    assert field_mode(_event(reasoning="abduced"), "place") == "abduced"
    assert field_mode(_event(), "place") is None
    assert render_field(_event(), "place", "x") == "place: x"
