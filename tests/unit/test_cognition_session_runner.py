"""`nora.app.cognition_session.CognitionSessionRunner` — the session loop
that turns a would-wake into ONE Cognition session in shadow.

`FakeCognitionAgentRunner` is the test seam
(`nora.app.cognition_session.CognitionAgentRunner`): it never touches
Strands or a model, and its `.run()` calls the closures it is HANDED by
name — exactly what a real model would do through Strands' tool-calling
loop, with none of the SDK machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
    MissingHomeRoomError,
)
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000


def iso(ms: int) -> str:
    """A session verb's time: ISO 8601 with an offset (M5)."""
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()

HOME = Room("signal", "home-room")
BUDGET = AdaptorBudget(turns=4, total_tokens=20_000, output_tokens=2_000, timeout_seconds=30.0)


@dataclass
class FakeCognitionAgentRunner:
    """Scripted: on `.run()`, calls `script` (a list of `(tool_name, kwargs)`
    pairs) against the tools it was handed, in order, then returns
    `stop_reason`."""

    script: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    calls: int = 0
    results: list[object] = field(default_factory=list)
    last_tool_names: list[str] = field(default_factory=list)

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.calls += 1
        by_name = {getattr(t, "__name__", None): t for t in tools}
        self.last_tool_names = sorted(n for n in by_name if n)
        for name, kwargs in self.script:
            self.results.append(await by_name[name](**kwargs))
        return CognitionRunResult(stop_reason="end_turn")


def _pending(unit: str, n: int = 1) -> tuple[Stimulus, ...]:
    return tuple(
        Stimulus(
            id=i, unit=unit, kind="message", item="message", ref=f"messages:{i}",
            at_ms=NOW, recorded_at_ms=NOW, platform="signal", conversation_id="home-room",
        )
        for i in range(1, n + 1)
    )


def _seeded(*wakes: tuple[str, int]) -> InMemoryStimulusStore:
    """A stimulus store holding the wake these tests hand the hook (an outcome
    names a wake that exists); by default wake 1 of `unit-1`."""
    stimuli = InMemoryStimulusStore()
    for unit, wake_id in wakes or (("unit-1", 1),):
        stimuli.seed_wake(unit, wake_id)
    return stimuli


def _runner(
    *, agent_runner: FakeCognitionAgentRunner | None = None,
    store: InMemoryCognitionStore | None = None,
    stimuli: InMemoryStimulusStore | None = None,
    memory: Any = None, grounder: Any = None,
) -> tuple[CognitionSessionRunner, InMemoryCognitionStore, FakeCognitionAgentRunner]:
    store = store or InMemoryCognitionStore()
    fake = agent_runner or FakeCognitionAgentRunner()
    runner = CognitionSessionRunner(
        store=store, stimulus_store=stimuli or _seeded(), memory=memory,
        agent_runner=fake, units=("unit-1",), session_timeout_seconds=900.0,
        home_rooms={"unit-1": HOME}, read_tools_for=lambda unit, rooms: [],
        skill_text_for=lambda kinds: "", grounder=grounder or EmptyGrounder(),
        doctrine_text="DOCTRINE",
        doctrine_sha256="sha", open_note_titles=_async_const(()),
        open_events=_async_const2(()), prior_standing=_async_const2(None),
        budget=BUDGET, cooldown_seconds=300.0, max_items=12,
        max_strikes=3, mint_ref=_counting_ref(), clock=lambda: NOW / 1000,
    )
    return runner, store, fake


def _async_const(value):
    async def f(*args, **kwargs):
        return value
    return f


def _async_const2(value):
    async def f(*args, **kwargs):
        return value
    return f


def _counting_ref():
    n = 0
    def f():
        nonlocal n
        n += 1
        return f"ref{n}"
    return f


# --------------------------------------------------------------------------
# off / empty agenda: zero model calls
# --------------------------------------------------------------------------



async def test_an_empty_agenda_makes_no_model_call():
    stimuli = _seeded()
    runner, store, fake = _runner(stimuli=stimuli)
    view = UnitView(unit="unit-1", pending=(), last_activity_ms=None, last_wake=None)
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert outcome is None
    assert fake.calls == 0
    assert store.sessions == []
    assert (stimuli.outcomes[1].outcome, stimuli.outcomes[1].detail) == ("closed", "empty agenda")


async def test_a_unit_with_no_home_room_makes_no_model_call_and_is_recorded_failed():
    stimuli = _seeded(("ghost-unit", 2))
    runner, _store, fake = _runner(stimuli=stimuli)
    view = UnitView(
        unit="ghost-unit", pending=_pending("ghost-unit"), last_activity_ms=None, last_wake=None,
    )
    with pytest.raises(MissingHomeRoomError):
        await runner.on_wake(unit="ghost-unit", view=view, now_ms=NOW, wake_id=2)
    assert fake.calls == 0
    assert stimuli.outcomes[2].outcome == "failed"


# --------------------------------------------------------------------------
# a real session: records itself, runs the model exactly once, closes items
# --------------------------------------------------------------------------

async def test_a_session_with_a_nonempty_agenda_records_itself_and_calls_the_model_once():
    fake = FakeCognitionAgentRunner(script=[])
    runner, store, fake = _runner(agent_runner=fake, stimuli=_seeded(("unit-1", 42)))
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1", n=3), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=42)
    assert fake.calls == 1
    assert outcome is not None
    assert len(store.sessions) == 1
    session_id, session, _ = store.sessions[0]
    assert session.wake_id == 42
    assert session.mode == "live"
    assert session_id == outcome.session_id


async def test_an_item_never_closed_by_the_model_is_closed_waiting_by_code():
    runner, store, _fake = _runner(agent_runner=FakeCognitionAgentRunner(script=[]))
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert len(outcome.closed) == 1
    assert outcome.closed[0].reason == "waiting"
    assert len(store.closes) == 1


async def test_the_model_can_close_an_item_explicitly():
    fake = FakeCognitionAgentRunner(script=[("close", {"item_index": 0, "reason": "no_change"})])
    runner, _store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert len(outcome.closed) == 1
    assert outcome.closed[0].reason == "no_change"


async def test_the_tool_list_offers_every_write_pace_and_remind_verb():
    fake = FakeCognitionAgentRunner(script=[])
    runner, _store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert set(fake.last_tool_names) >= {
        "close", "note_create", "note_update", "event_propose", "event_revise",
        "event_set_status", "standing_revise", "wake_at", "wake_move", "wake_cancel",
        "remind", "remind_move", "remind_cancel",
    }




async def test_a_wake_at_call_is_recorded_with_a_minted_ref():
    fake = FakeCognitionAgentRunner(script=[(
        "wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3_600_000), "why": "check it"},
    )])
    runner, store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert len(store.paces) == 1
    _, pace, _ = store.paces[0]
    assert pace.outcome == "recorded"
    assert pace.wake_ref == "ref1"





def wake_memory():
    from nora.app.cognition_memory import WakeMemory
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph
    return WakeMemory(graph=FakeMemoryGraph(), records=InMemoryComprehendRecords(),
                      metrics=Metrics())


def proposal(items=None):
    return {"readings": [], "entities": [], "relations": [], "evidence": [],
            "corrections": [], "items": [0] if items is None else items}


async def test_cognition_01_act_refused_before_admitted_proposal():
    fake = FakeCognitionAgentRunner(script=[
        ("wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3600000), "why": "check"}),
        ("memory_propose", proposal()),
        ("wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3600000), "why": "check"}),
    ])
    runner, store, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results[0]["reason"] == "proposal_required"
    assert fake.results[1]["admitted"] == [0]
    assert len(store.paces) == 1


async def test_an_unprojected_message_still_runs_its_wake_on_its_own_text():
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.testing.cognition_grounding import StaticGrounder

    stimulus = _pending("unit-1")[0]
    record = RecordItem(ref=stimulus.ref, kind="message", message_id=1, room=HOME,
                        author="Miles", at_ms=NOW, text="downstairs now")
    fake = FakeCognitionAgentRunner(script=[("memory_propose", proposal())])
    runner, _store, _ = _runner(agent_runner=fake, memory=wake_memory(),
                                grounder=StaticGrounder(Grounding(records={stimulus.ref: record})))
    await runner.replay(view=UnitView(unit="unit-1", pending=(stimulus,),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results[0]["admitted"] == [0]


async def test_cognition_08_proposal_answer_lists_refusal_with_reason():
    payload = proposal()
    payload["relations"] = [{"id": "generated-wrong", "src": ["person", "generated"],
        "rel": "invented", "dst": ["topic", "generated-topic"]}]
    fake = FakeCognitionAgentRunner(script=[("memory_propose", payload)])
    runner, store, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert not fake.results[0]["admitted"]
    assert any(row["reason"] == "vocabulary" for row in fake.results[0]["refused"])
    assert not store.paces


async def test_cognition_09_backfill_withholds_act_tools():
    runner, store, fake = _runner(memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW, historical=True)
    assert {"memory_propose", "profile_revise", "close"} <= set(fake.last_tool_names)
    forbidden = ("note_", "memo_", "event_", "lesson_", "topic_", "decision_",
                 "commitment_", "standing_", "wake_", "remind", "send_")
    assert not any(n.startswith(forbidden) for n in fake.last_tool_names)
    assert not store.paces


@pytest.mark.parametrize("admit", [False, True])
async def test_stimulus_consumed_only_by_admitted_proposal_or_explicit_empty(admit):
    stimuli = _seeded()
    fake = FakeCognitionAgentRunner(script=[("memory_propose", proposal()),
        ("close", {"item_index": 0, "reason": "no_change"})] if admit else [])
    runner, _, _ = _runner(agent_runner=fake, stimuli=stimuli, memory=wake_memory())
    await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1",
        pending=_pending("unit-1"), last_activity_ms=None, last_wake=None), now_ms=NOW, wake_id=1)
    carried = [c.stimulus_id for _, _, c in stimuli.carries if c.state == "carried"]
    assert (1 not in carried) == admit


async def test_partial_proposal_consumes_only_covered_items():
    from dataclasses import replace
    pending = _pending("unit-1", 2)
    pending = (pending[0], replace(pending[1], conversation_id="other-generated-room"))
    fake = FakeCognitionAgentRunner(script=[("memory_propose", proposal()),
        ("close", {"item_index": 0, "reason": "no_change"})])
    stimuli = _seeded()
    runner, _, _ = _runner(agent_runner=fake, stimuli=stimuli, memory=wake_memory())
    await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1", pending=pending,
        last_activity_ms=None, last_wake=None), now_ms=NOW, wake_id=1)
    assert [c.stimulus_id for _, _, c in stimuli.carries if c.state == "carried"] == [2]


async def test_five_failures_park_with_operator_requeue():
    memory = wake_memory()
    pending = _pending("unit-1")
    for _ in range(4):
        await memory.failed("unit-1", [1], None)
    assert await memory.parked("unit-1", pending) == set()
    await memory.failed("unit-1", [1], None)
    assert await memory.parked("unit-1", pending) == {1}
    await memory.records.failures("unit-1", 1, reset=True)
    assert await memory.parked("unit-1", pending) == set()


async def test_agenda_carry_and_strike_keep_uncomprehended_stimuli_pending():
    from dataclasses import replace
    memory = wake_memory()
    stimuli = _seeded()
    runner, _, _ = _runner(stimuli=stimuli, memory=memory)
    runner._max_items = 1
    pending = _pending("unit-1", 2)
    pending = (pending[0], replace(pending[1], conversation_id="other-generated-room"))
    await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1", pending=pending,
        carry={1: 3, 2: 3}, last_activity_ms=None, last_wake=None), now_ms=NOW, wake_id=1)
    assert {c.stimulus_id for _, _, c in stimuli.carries if c.state == "carried"} == {1, 2}


async def test_refused_session_leaves_stimuli_pending():
    payload = proposal()
    payload["entities"] = [{"id": "generated-person", "kind": "person", "name": "Generated"}]
    stimuli = _seeded()
    fake = FakeCognitionAgentRunner(script=[("memory_propose", payload)])
    runner, _, _ = _runner(agent_runner=fake, stimuli=stimuli, memory=wake_memory())
    await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW, wake_id=1)
    assert not fake.results[0]["admitted"]
    assert [c.stimulus_id for _, _, c in stimuli.carries if c.state == "carried"] == [1]


async def test_failed_session_keeps_stimuli_pending_and_counts_failure():
    memory = wake_memory()
    stimuli = _seeded()
    fake = FakeCognitionAgentRunner(script=[("nonexistent_generated_tool", {})])
    runner, _, _ = _runner(agent_runner=fake, stimuli=stimuli, memory=memory)
    with pytest.raises(KeyError):
        await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1",
            pending=_pending("unit-1"), last_activity_ms=None, last_wake=None),
            now_ms=NOW, wake_id=1)
    assert stimuli.outcomes[1].outcome == "failed"
    assert not stimuli.carries
    assert await memory.records.failures("unit-1", 1) == 1


async def test_schema_refusal_is_returned_and_never_unlocks_acts():
    payload = proposal()
    payload["relations"] = [{"id": "generated-relation", "src": ["person", "generated"],
        "rel": "likes", "dst": ["place", "generated-place"], "valid_from": "bad-time"}]
    fake = FakeCognitionAgentRunner(script=[("memory_propose", payload),
        ("wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3600000), "why": "check"})])
    runner, store, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert any(r["reason"] == "schema" for r in fake.results[0]["refused"])
    assert fake.results[1]["reason"] == "proposal_required"
    assert not store.paces


async def test_retried_link_write_relinks_existing_register_row_by_id(monkeypatch):
    from tests.unit.cognition_rig import GOOD_BODY, NOTE, Stores, session
    memory = wake_memory()
    stores = Stores()
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    from tests.unit.cognition_rig import RECORDS
    for row in RECORDS.values():
        source = Source(("message", str(row.message_id)), "record", "u", (), row.text)
        work = WorkItem("generated-record", source.key, "u", "hash", datetime.now(UTC))
        memory.records.items[source.key] = ComprehendItem(work, (source,), "signal", "room-a",
            snapshot={"messages": [], "context": []})
    links = []

    async def link(state, result):
        links.append(result["note_id"])
        if len(links) == 1:
            raise RuntimeError("generated link outage after Register commit")
        assert result["note_id"] in stores.notes.notes

    monkeypatch.setattr(memory, "link_result", link)
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**proposal())
        answers.append(await by["note_create"](body=GOOD_BODY, **NOTE))
        answers.append(await by["note_create"](body=GOOD_BODY, **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["unavailable", "ok"]
    assert links[0] == links[1] == answers[1]["note_id"]
    assert len(stores.notes.notes) == 1


async def test_stays_and_plays_join_the_same_proposal_coverage():
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.agent.fetch_watch import GraphFetched
    from nora.domain.cognition_ground import Grounding
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    memory = wake_memory()
    play = replace(_pending("unit-1")[0], kind="play", ref="play:generated-play")
    stay = replace(play, id=2, kind="rollup", ref="arrival|generated-place|generated-person|1000")
    source = Source(("play", "generated-play"), "record", "unit-1", (), "Generated track")
    work = WorkItem("generated-play", source.key, "unit-1", "hash", datetime.now(UTC))
    memory.records.items[source.key] = ComprehendItem(work, (source,), "signal", "home-room",
        snapshot={"messages": [], "context": []})
    state = SimpleNamespace(now_ms=NOW, unit="unit-1", session_id=1, historical=False,
        items_by_index={0: SimpleNamespace(stimulus_ids=(1, 2), room=HOME)}, memory_items={},
        graph_fetched=GraphFetched(), admitted_indices=set(), admitted_relations={},
        admitted_evidence=[])
    await memory.prepare(state, {1: play, 2: stay}, Grounding())
    assert {s.key[0] for s in state.memory_items[0].sources} == {"play", "rollup"}
    assert "Generated track" in state.graph_text
    answer = await memory.propose(state, items=[0], payload={k: v for k, v in proposal().items()
                                                            if k != "items"})
    assert answer["admitted"] == [0]


async def test_inspection_renders_real_wake_clock_room_and_open_items_without_model():
    from nora.domain.cognition_ground import EventLine, Grounding, NoteLine, RecordItem
    from nora.testing.cognition_grounding import StaticGrounder

    grounding = Grounding(
        records={"messages:1": RecordItem(ref="messages:1", kind="message", message_id=1,
            room=HOME, author="Generated speaker", at_ms=NOW, text="I arrived")},
        notes=(NoteLine(id="generated-note", title="Hand over generated gifts",
                        description="Generated open handover"),),
        events=(EventLine(id="generated-event", room=HOME, title="Generated dinner",
                         when_ms=NOW, venue="Generated venue", status="proposed"),),
        room_labels={"signal:home-room": "Generated room"})
    runner, _, agent = _runner(grounder=StaticGrounder(grounding))
    inspection = await runner.inspect(view=UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None),
        now_ms=NOW)
    assert set(inspection) == {"system", "agenda"}
    assert "DOCTRINE" in inspection["system"]
    for value in ("Generated room", "I arrived", "Hand over generated gifts", "Generated dinner"):
        assert value in inspection["agenda"]
    assert "clock" in inspection["agenda"] and "2027" in inspection["agenda"]
    assert agent.calls == 0


async def test_an_inspection_records_no_session_outcome_or_carry():
    """An inspection is a read of what a session would be run on: it must
    leave the shared wake sandbox exactly as it found it — no session row,
    no wake outcome, no carry, and the stimuli still pending."""
    stimuli = _seeded()
    runner, store, agent = _runner(stimuli=stimuli)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    inspection = await runner.inspect(view=view, now_ms=NOW)
    assert inspection is not None and set(inspection) == {"system", "agenda"}
    assert store.sessions == []
    assert stimuli.outcomes == {}
    assert stimuli.carries == []
    assert agent.calls == 0


async def test_inspection_of_an_empty_agenda_is_none_and_calls_no_model():
    runner, _, agent = _runner()
    assert await runner.inspect(view=UnitView(
        unit="unit-1", pending=(), last_activity_ms=None, last_wake=None),
        now_ms=NOW) is None
    assert agent.calls == 0
