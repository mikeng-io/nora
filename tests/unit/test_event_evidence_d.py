"""The pure half of an event's evidence (fix PR D, migration `0086_event_citations`;
design/cognition-loop/10-tiers.md §3, 40-register.md §3-§4): the lineage computed
from citations, the citation checker, per-field reasoning, the set of statuses a
session may still move, the heartbeat's gathering screen, and the cognition
grounding's withdrawn-event guard. No database: `tests/infra/test_event_citations.py`
is the same behaviours against real Postgres.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-d.json`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from nora.app.cognition_wiring import open_events_reader
from nora.app.gathering_screen import GatheringScreen
from nora.app.heartbeat import HeartbeatLoop
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
    merge_field_meta,
)
from nora.domain.lineage import Lineage, LineageEntry, message, unresolved
from nora.domain.note_body import NoteBodyError, Shown, ShownMessage, check_citations
from nora.domain.reminders import Reminder, ReminderState
from nora.testing.gatherings import FakeGatheringStore

T0 = dt.datetime(2026, 10, 2, 11, 0, tzinfo=dt.UTC)


def _event(**over: Any) -> Gathering:
    base: dict[str, Any] = {
        "id": uuid.uuid4(), "conversation_id": "ga", "title": "dinner", "created_by": "p_ada",
        "when": None, "venue_text": None, "venue_place_id": None, "venue_name": None,
        "venue_address": None, "venue_rating": None, "venue_price_level": "",
        "venue_maps_url": None, "venue_resolved_at": None,
        "status": GatheringStatus.PROPOSED, "reminder_id": None, "created_at": T0,
        "unit_id": "u",
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


# -- a lineage an event can now be rendered by (m6) --------------------------

def test_an_event_with_a_computed_lineage_renders_where_its_evidence_may_and_not_elsewhere():
    """The defect: no writer set an event's lineage, so under `[lineage] mode =
    on` every event had an empty one, which renders only in its origin room. A
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
    # Unwritten, as every event was: nothing distinguishes it from any other,
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
    made = {s: await store.create(_event(status=s, title=s.value)) for s in GatheringStatus}
    read = open_events_reader(store)
    assert {e.title for e in await read("ga")} == {"staged", "proposed", "confirmed"}
    store.mark_unsupported(made[GatheringStatus.CONFIRMED].id, 5)
    assert {e.title for e in await read("ga")} == {"staged", "proposed"}


# -- the heartbeat drops a withdrawn gathering's reminder -------------------

async def test_the_heartbeat_cancels_a_withdrawn_gatherings_reminder_and_fires_a_standing_one():
    from nora.obs.metrics import Metrics
    from nora.testing.reminders import FakeReminderStore
    from tests.unit.test_heartbeat_loop import _RecordingAllowlist

    events = FakeGatheringStore()
    reminders = FakeReminderStore()
    rows: dict[str, Reminder] = {}
    for name in ("withdrawn", "standing"):
        rid = uuid.uuid4()
        rows[name] = Reminder(
            id=rid, requester_person_id="miles", platform="signal", conversation_id="ga",
            conversation_type="group", text=f"Event: {name}", created_at=T0 - dt.timedelta(hours=1),
            due_at=T0 - dt.timedelta(minutes=1), state=ReminderState.PENDING, fired_at=None,
            outcome=None, kind="gathering", payload={"title": name},
        )
        await reminders.create(rows[name])
        await events.create(_event(title=name, reminder_id=rid, conversation_id=f"c-{name}"))
    events.mark_unsupported((await events.get_by_reminder(rows["withdrawn"].id)).id, 5)
    fired: list[list[Reminder]] = []

    async def fire(batch: list[Reminder]) -> str:
        fired.append(list(batch))
        return "sent"

    from nora.engagement.slots import ConversationSlots

    loop = HeartbeatLoop(
        store=reminders, slots=ConversationSlots(maxlen=16, spawn=lambda c: None),
        policy=_RecordingAllowlist(allow=True), clock=lambda: T0, metrics=Metrics(),
        tick_seconds=60.0, fire=fire, gatherings=GatheringScreen(events=events),
    )
    await loop.tick()
    assert [[r.id for r in batch] for batch in fired] == [[rows["standing"].id]]
    gone = await reminders.get(rows["withdrawn"].id)
    assert gone.state is ReminderState.CANCELLED and gone.outcome == "cancelled_unsupported"
