"""Generated turns exercise the recorded boundaries, including mixed-room reads."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nora.agent.prefill import render_transcript
from nora.agent.room import Room
from nora.agent.toolkit.transcript import _transcript_messages
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
    ref = PersonRef(person_id="p1", name="Member 1")
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
    assert _transcript_messages((row,), now=now, display_name="Nora")[0][0]["turn"] == "t7"
    assert "Nora (turn t7): checked" in render_recent((row,), display_name="Nora")
    other = _transcript_messages((replace(row, is_nora=False),), now=now, display_name="Nora")[0][0]
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
    from nora.agent.dreamer import AdaptorBudget
    from nora.agent.guards import ReplyGuard
    from nora.agent.ports import ToolSpec, adapt
    from nora.agent.turn_recording import in_room
    from nora.app.agent_runner import LiveAgentRunner
    from nora.app.cognition_agent_runner import DreamerAgentRunner
    from nora.domain.models import NormalizedMessage, TagContext
    from nora.obs.metrics import Metrics
    from tests.fakes import FixedModelSource, dreamer_runtime_for
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
            ), tagger=None, transcript=(), started_at=0,
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
        result = await DreamerAgentRunner(
            runtime=dreamer_runtime_for(
                models=FixedModelSource(model), retry=RETRY,
                metrics=Metrics(),
            ),
        ).run(
            system_prompt="check", prompt="check", tools=[tool],
            budget=AdaptorBudget(turns=4, total_tokens=1000, output_tokens=1000, timeout_seconds=5),
            trace_attributes={},
        )
        messages, requirements = result.messages, result.requirements
    items = turn_items(messages, requirements)
    assert len(items) == 1 and items[0].result is not None
    assert items[0].requirements == (REQ,)


class _RecordConnection:
    """Local SQL scaffolding: answer only matching table, handle and unit reads."""

    def __init__(self, trace, memo=None):
        self.trace = trace
        self.memo = memo

    async def execute(self, stmt):
        from types import SimpleNamespace

        params = stmt.compile().params
        tables = str(stmt)
        if tables.startswith("INSERT INTO tag_trace"):
            return None
        if "FROM tag_trace" in tables:
            found = self.trace if (
                params.get("turn_id_1") == self.trace.turn_id
                and params.get("unit_1") == self.trace.unit
            ) else None
            row = None if found is None else SimpleNamespace(**{
                    field: getattr(found, field) for field in found.__dataclass_fields__
                })
            return SimpleNamespace(first=lambda: row)
        if "FROM messages" in tables:
            return SimpleNamespace(scalar_one_or_none=lambda: "signal")
        if "FROM memo" in tables:
            return SimpleNamespace(one_or_none=lambda: self.memo, all=lambda: [self.memo])
        raise AssertionError(str(stmt))


def _record_engine(conn):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    @asynccontextmanager
    async def connect():
        yield conn

    return SimpleNamespace(connect=connect, begin=connect)


async def test_turn_citations_derive_note_and_memo_disclosure_and_refuse_unknown_records():
    from types import SimpleNamespace
    from uuid import UUID

    from nora.agent.lineage import section_permits
    from nora.domain.lineage import ROW, Lineage, LiveRoom
    from nora.infra.sql.memo import SqlMemoStore
    from nora.infra.sql.notes import _revision_lineage

    req = replace(REQ, location=True, location_subjects=("p2",), exclude_persons=("p2",))
    trace = _trace([[req.to_json()]])
    body = "## Open\n- checked turn(t7)\n"
    row = SimpleNamespace(
        mid=str(UUID(int=7)), unit="u1", platform="signal", conversation_id="room-a",
        created_at=1, id="generated-revision", memo_id="generated-memo", seq=1,
        title="checked fact", body="## Kept\n- checked turn(t7)", citations=["turn(t7)"],
        mode="told", state="kept", written_at=1, established_by="cognition:u1", model=None,
    )
    conn = _RecordConnection(trace, row)
    note_lineage, _ = await _revision_lineage(
        conn, body=body, origin="room-a", first=True, unit="u1",
    )
    live = LiveRoom(frozenset({"p1"}))
    allowed = _target().with_live(live)
    denied = _target(replace(B, location=False)).with_live(live)
    assert section_permits(note_lineage.section(ROW), allowed, origin="room-a")
    assert not section_permits(note_lineage.section(ROW), denied, origin="room-a")
    memo = await SqlMemoStore(_record_engine(conn)).get(
        unit="u1", memo_id=str(UUID(int=7)),
    )
    assert memo is not None
    for lineage in (note_lineage, memo.head.lineage):
        # The stored JSON round trip preserves exclusions and location together.
        entries = Lineage.from_json(lineage.to_json()).section(ROW)
        assert section_permits(entries, allowed, origin=None)
        assert not section_permits(entries, denied, origin=None)
        assert not section_permits(entries, _target(replace(B, persons=frozenset({"p2"})))
                                   .with_live(live), origin=None)


async def test_unresolved_record_items_are_unavailable_and_denied_items_stay_resolved():
    for requirement in (
        {},
        {"call-0": [replace(REQ, entries=(LineageEntry("unknown"),)).to_json()]},
        {"call-0": [replace(REQ, location=True).to_json()]},
    ):
        trace = replace(_trace([[REQ.to_json()]]), requirements=requirement)
        result = await turn_record_view(trace, _target(), _room())
        assert result["status"] == "unavailable" and result["unavailable"] == 1
        assert result["withheld"] == 1 and result["items"] == []
    named = _trace([[REQ.to_json()]])
    result = await turn_record_view(named, _target(), _room(unnamed=True))
    assert result["status"] == "unavailable" and result["unavailable"] == 1
    denied = replace(REQ, location=True, location_subjects=("p2",))
    result = await turn_record_view(_trace([[denied.to_json()]]),
                                    _target(replace(B, location=False)), _room())
    assert result["status"] == "ok" and result["withheld"] == 1
    assert result["unavailable"] == 0
    result = await turn_record_view(_trace([[REQ.to_json()], [{}]]), _target(), _room())
    assert result["status"] == "partial" and result["unavailable"] == 1
    assert len(result["items"]) == 1 and result["withheld"] == 1
