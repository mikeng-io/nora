"""Generated turns exercise the recorded boundaries, including mixed-room reads."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.prefill import render_transcript
from nora.agent.room import Room
from nora.agent.tools import _transcript_messages
from nora.agent.turn_record import turn_record_view
from nora.domain.lineage import LineageEntry, RenderRoom, RoomFacts, message
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.note_body import NoteBodyError, Shown, ShownMessage, cited_line
from nora.domain.tag_trace import TagTrace
from nora.domain.turn_record import DisclosureRequirement, turn_items
from nora.persons import Persons
from nora.testing import FakeRosterSource

A = RoomFacts(platform="signal", ids=frozenset({"room-a"}), unit="u1",
              persons=frozenset({"p1", "p2"}))
B = RoomFacts(platform="discord", ids=frozenset({"room-b"}), unit="u1",
              persons=frozenset({"p1"}), location=True)
C = RoomFacts(platform="signal", ids=frozenset({"room-c"}), unit="u1",
              persons=frozenset({"p2"}))
D = replace(B, unit="u2")
REQ = DisclosureRequirement("signal:room-a", (message("signal:room-a"),))


def _trace(requirements, *, occasion="cognition"):
    messages = []
    for i in range(len(requirements)):
        call = f"call-{i}"
        messages.extend([
            {"role": "assistant", "content": [
                {"reasoningContent": {"reasoningText": {"text": "discard this"}}},
                {"text": "narration"},
                {"toolUse": {"toolUseId": call, "name": "web_search", "input": {"query": str(i)}}},
            ]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": call, "status": "success", "content": [{"text": f"result-{i}"}],
            }}]},
        ])
    return TagTrace(
        tag_id="generated-turn", conversation_id="room-a", platform="signal", unit="u1",
        messages=tuple(messages), transcript=(), outcome="silent", created_at=1,
        occasion=occasion, turn_id=7, bound_rooms=("signal:room-a", "signal:room-c"),
        requirements={f"call-{i}": req for i, req in enumerate(requirements)},
    )


def _room(*, fail=False, unnamed=False):
    ref = PersonRef(person_id="p1", name="Member 1", honcho_peer_id="p1")
    return Room(
        platform="discord", conversation_id="room-b",
        roster_source=FakeRosterSource(
            groups={"room-b": (frozenset({"unknown" if unnamed else "account-1"}),)}, fail=fail,
        ),
        persons=Persons(by_person={"p1": ref}, index={("discord", "account-1"): "p1"}),
    )


def _target(room=B):
    return RenderRoom(room=room, rooms=(A, B, C), circles=(frozenset({"p1", "p2"}),))


async def test_mixed_room_record_reapplies_each_recorded_requirement():
    trace = _trace([
        [REQ.to_json()],
        [replace(REQ, entries=(message("signal:room-c"),)).to_json()],
        [replace(REQ, exclude_persons=("p1",)).to_json()],
        [replace(REQ, location=True, location_subjects=("p2",)).to_json()],
        [replace(REQ, entries=(LineageEntry("unknown", origin="signal:room-a"),)).to_json()],
        [{"origin": "signal:room-a"}],
    ])
    view = await turn_record_view(trace, _target(replace(B, location=False)), _room())
    assert [x["result"]["content"][0]["text"] for x in view["items"]] == ["result-0"]
    assert view["withheld"] == 5
    assert "discard this" not in str(view)
    assert "narration" not in str(view)


async def test_location_requirements_use_the_profile_gate_and_current_roster():
    trace = _trace([[replace(REQ, location=True, location_subjects=("p2",)).to_json()]])
    assert len((await turn_record_view(trace, _target(), _room()))["items"]) == 1
    unreadable = await turn_record_view(trace, _target(), _room(fail=True))
    assert unreadable["withheld"] == 1 and unreadable["status"] == "unavailable"
    assert (await turn_record_view(trace, _target(), _room(unnamed=True)))["withheld"] == 1
    missing = _trace([[replace(REQ, location=True).to_json()]])
    assert (await turn_record_view(missing, _target(), _room()))["withheld"] == 1


async def test_other_unit_and_unknown_handle_have_identical_answers():
    trace = _trace([[REQ.to_json()]])
    assert await turn_record_view(trace, _target(D), _room()) == await turn_record_view(
        None, _target(D), _room(),
    )
    assert len(turn_items(trace.messages, trace.requirements)) == 1
    assert (await turn_record_view(trace, _target(), _room()))["withheld"] == 0


def test_own_rows_carry_the_same_handle_in_prefill_and_transcript_tools():
    from nora.agent.reaction import render_recent
    row = TranscriptRow(
        id=12, sender_id="self", sender_name=None, ts=1, body="checked",
        is_nora=True, delivery_status=DeliveryStatus.SENT, raw_json=None, turn_id=7,
    )
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert 'turn="t7"' in render_transcript((row,), display_name="Nora", now=now)
    assert _transcript_messages((row,), now=now)[0][0]["turn"] == "t7"
    assert "Nora (turn t7): checked" in render_recent((row,))
    other = _transcript_messages((replace(row, is_nora=False),), now=now)[0][0]
    assert "turn" not in other


@pytest.mark.parametrize("line", [
    "checked turn(t7)", "checked [turn(t7)]", "checked [12, turn(t7)]",
])
def test_turn_citation_requires_a_handle_this_writer_saw_or_ran(line):
    from nora.domain.note_body import citations_in
    shown = Shown(rendered=(ShownMessage(12, "Nora", is_nora=True, turn_handle="t7"),))
    assert cited_line("Options", line, shown, may_cite_nora=True).turn_handles == ("t7",)
    assert "turn(t7)" in citations_in(line)
    with pytest.raises(NoteBodyError, match="unknown_turn"):
        cited_line("Options", line, Shown(), may_cite_nora=True)
    assert cited_line("Options", "checked turn(t7)", Shown(turn_handles=frozenset({"t7"})))


@pytest.mark.parametrize("occasion", ["tag", "cognition"])
async def test_the_real_sdk_retains_the_exchange_and_requirements(occasion):
    from nora.agent.guards import ReplyGuard
    from nora.agent.ports import ToolSpec, adapt
    from nora.agent.turn_recording import in_room
    from nora.app.agent_runner import LiveAgentRunner
    from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
    from nora.app.cognition_session import AdaptorBudget
    from nora.domain.models import NormalizedMessage, TagContext
    from nora.obs.metrics import Metrics
    from tests.fakes import FixedModelSource
    from tests.test_agent_runner_stop_reason import RETRY, _doctrine, _StubModel

    async def now() -> str:
        """Read the current clock."""
        return datetime.now(UTC).isoformat()

    tool = adapt(in_room(ToolSpec(name="now", description="", schema={}, call=now),
                         platform="signal", conversation_id="room-a"))
    model = _StubModel(tokens_per_call=10, tool_use_calls=1)
    if occasion == "tag":
        ctx = TagContext(
            tag_id="generated-turn", message=NormalizedMessage(
                platform="signal", conversation_id="room-a", conversation_type="group",
                sender_id="generated-account", sender_name="Member 1", timestamp=1,
                body="check", raw={},
            ), tagger=None, transcript=(), peer_card=(), started_at=0,
        )
        _, _, trace = await LiveAgentRunner(models=FixedModelSource(model), retry=RETRY).invoke(
            doctrine=_doctrine(), tools=[tool], pre_fill=[],
            guard=ReplyGuard(conversation_id="room-a", budgets={"now": 2},
                             terminal=frozenset(), metrics=Metrics()),
            tag_message="check", limits={"turns": 4, "total_tokens": 1000, "output_tokens": 1000},
            ctx=ctx, trace_attributes={},
        )
        messages, requirements = trace.messages, trace.requirements
    else:
        result = await LiveCognitionAgentRunner(
            models=FixedModelSource(model), retry=RETRY,
        ).run(
            system_prompt="check", prompt="check", tools=[tool],
            budget=AdaptorBudget(turns=4, total_tokens=1000, output_tokens=1000, timeout_seconds=5),
            trace_attributes={},
        )
        messages, requirements = result.messages, result.requirements
    items = turn_items(messages, requirements)
    assert len(items) == 1 and items[0].result is not None
    assert items[0].requirements == (REQ,)


async def test_character_read_records_exclusions_and_location_subjects():
    from types import SimpleNamespace

    from nora.agent.room import Room
    from nora.agent.tools import build_tools
    from nora.agent.turn_recording import TurnRecording
    from nora.domain.profile import ProfileLine
    from nora.infra.transit import TransitClient
    from nora.obs.metrics import Metrics
    from nora.policy import Conversation, Policy
    from nora.testing import FakeMemoryStore, FakeRecordStore
    from tests.fakes import SinkSender
    from tests.unit.test_profile_disclosure import _Heads

    ref = PersonRef(person_id="p1", name="Member 1", honcho_peer_id="p1")
    persons = Persons(by_person={"p1": ref}, index={("signal", "account-1"): "p1"})
    convo = Conversation(platform="signal", ids=frozenset({"room-a"}),
                         primary_id="room-a", unit="u1", label="Room", location=True,
                         persons=frozenset({"p1"}))
    policy = Policy(conversations=(convo,), _by_id={("signal", "room-a"): convo})
    from nora.agent.whereis import Circle
    lineage = LineagePolicy.from_policy(policy, circles=(
        Circle(circle_id="generated-circle", person_ids=frozenset({"p1"})),
    ))
    line = ProfileLine(
        id="generated-line", profile_key="room-a", part="traits", kind="topic",
        text="generated observation", mode="induced", citations=(), state="current",
        unit="u1", written_at=1, written_by_session="generated-session",
        exclude_persons=("p2",), location=True, strength={"location_subjects": ["p1"]},
        content={"statement": "generated observation", "context": "unknown",
                 "interest": "books", "depth": "unknown", "stance": "unknown"},
    )
    tools = build_tools(
        store=FakeRecordStore(), conversation_id="room-a", conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(), metrics=Metrics(),
        room=Room(platform="signal", conversation_id="room-a", persons=persons,
                  roster_source=FakeRosterSource(groups={"room-a": (frozenset({"account-1"}),)})),
        transit_source=TransitClient(kmb_base_url="http://127.0.0.1:1",
                                    mtr_schedule_url="http://127.0.0.1:1/s",
                                    mtr_routes_url="http://127.0.0.1:1/r", timeout_seconds=.5),
        lineage=lineage, profile_read=_Heads({"room-a": (line,)}),
        clock=lambda: datetime(2026, 10, 1, tzinfo=UTC),
    )
    tool = next(t for t in tools if t.tool_name == "character_read")
    recording = TurnRecording()
    event = SimpleNamespace(tool_use={"toolUseId": "call-1"})
    recording.before(event)
    try:
        result = await tool._tool_func(entity="room", subject="Room")
    finally:
        recording.after(event)
    assert result["status"] == "ok"
    captured = recording.to_json()["call-1"]
    assert any(r["exclude_persons"] == ["p2"] and r["location"]
               and r["location_subjects"] == ["p1"] for r in captured)
