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
from typing import Any, ClassVar

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
        "close", "note_create", "note_revise", "event_propose", "event_revise",
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

    async def link(state, result, *, targets=()):
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


from tests.unit.cognition_rig import GOOD_BODY, NOTE, Stores, session  # noqa: E402


def _register_load(stores):
    """A `records.load` that serves a register row the way the SQL store
    does — the row's own text as its notebook source plus the record source
    it cites — and refuses anything else, like an unprojected row."""
    from sqlalchemy.exc import NoResultFound

    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source

    async def load(work):
        kind, ref = work.source
        body = None
        if kind == "note" and ref in stores.notes.notes:
            body = stores.notes.revisions[stores.notes.notes[ref].head_revision_id].body
        if kind == "event_entry" and any(
                str(e.id) == ref for e in stores.gatherings._events.values()):
            body = next(e.title for e in stores.gatherings._events.values()
                        if str(e.id) == ref)
        if body is None:
            raise NoResultFound("generated unprojected register row")
        return ComprehendItem(work, (
            Source(work.source, "notebook", "u", (), body),
            Source(("message", "101"), "record", "u", ("p_ada",), "generated cite"),
        ), "signal", "room-a", snapshot={"messages": [], "context": []})

    return load


def _about_proposal(entity_id: str, relation_id: str = "r-about"):
    payload = proposal()
    payload["relations"] = [{"id": relation_id, "src": ["reading", "generated-r"],
        "rel": "about", "dst": ["event", entity_id], "persons": ["p_ada"]}]
    payload["evidence"] = [{"source": ["message", "101"], "relation_id": relation_id}]
    return payload


def _seed_event(memory, entity_id: str, name: str = "generated gala"):
    from nora.domain.memory_graph import Entity
    entity = Entity(entity_id, "event", "u", name)
    memory.graph.entities[entity.id] = entity
    return entity


async def test_alias_free_admitted_association_survives_create_and_later_read():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(event.id))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    note_id = answers[0]["note_id"]
    assert not any("generated gala" in r.body.casefold()
                   for r in stores.notes.revisions.values())
    edge = next(r for r in memory.graph.relations.values()
                if r.src == ("note", note_id))
    assert (edge.rel, edge.dst, edge.unit) == ("concerns", event.key, "u")
    # A later read through the same scoped walk still reaches the association.
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",)))
    assert seen.status == "ok"
    assert [e.relation.dst for e in seen.relations] == [event.key]


async def test_note_revise_replaces_the_semantic_association():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:generated-gala")
    second = _seed_event(memory, "u:event:generated-regatta")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", first.id]], **NOTE)
        answers.append(created)
        got = await stores.notes.read(unit="u", note_id=created["note_id"])
        answers.append(await by["note_revise"](
            note_id=created["note_id"], parent_revision_id=got[0].head_revision_id,
            body=GOOD_BODY, associations=[["event", second.id]]))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["ok", "ok"]
    note_id = answers[0]["note_id"]
    later = datetime.fromtimestamp(NOW / 1000 + 3600, UTC)
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=later))
    assert [e.relation.dst for e in seen.relations] == [second.key]
    stale = next(r for r in memory.graph.relations.values()
                 if r.src == ("note", note_id) and r.dst == first.key)
    assert stale.valid_to is not None


async def test_unshown_or_out_of_unit_association_cannot_persist():
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**proposal())
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", "u:event:never-shown"]], **NOTE))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", "other-unit:event:x"]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["reason"] for a in answers] == ["unseen_association", "unseen_association"]
    assert not stores.notes.notes
    assert not memory.graph.relations


async def test_projection_outage_keeps_created_row_and_retry_relinks_same_association():
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(event.id))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

    from nora.app import register_graph
    real_project = register_graph.RegisterGraphLinks.project

    async def outage(self, item, session_id, *, selected=()):
        raise RuntimeError("generated projection outage")

    register_graph.RegisterGraphLinks.project = outage
    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    register_graph.RegisterGraphLinks.project = real_project
    assert answers[-1]["status"] == "unavailable"
    assert len(stores.notes.notes) == 1
    assert not [r for r in memory.graph.relations.values() if r.src[0] == "note"]
    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[-1]["status"] == "ok"
    assert answers[-1]["note_id"] == next(iter(stores.notes.notes))
    assert len(stores.notes.notes) == 1
    assert any(r.src == ("note", answers[-1]["note_id"]) and r.dst == event.key
               for r in memory.graph.relations.values())


async def test_revise_without_associations_keeps_the_semantic_link():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []
    note_id = None

    async def behave(by, tools):
        nonlocal note_id
        await by["memory_propose"](**_about_proposal(event.id))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE)
        note_id = created["note_id"]
        got = await stores.notes.read(unit="u", note_id=note_id)
        answers.append(await by["note_revise"](
            note_id=note_id, parent_revision_id=got[0].head_revision_id,
            body=GOOD_BODY))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=datetime.fromtimestamp(
                 NOW / 1000 + 3600, UTC)))
    assert [e.relation.dst for e in seen.relations] == [event.key]


async def test_reselecting_a_closed_association_relinks_with_a_new_relation():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:generated-gala")
    second = _seed_event(memory, "u:event:generated-regatta")
    answers = []
    note_id = None

    async def behave(by, tools):
        nonlocal note_id
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", first.id]], **NOTE)
        note_id = created["note_id"]
        for assoc in (second, first):
            got = await stores.notes.read(unit="u", note_id=note_id)
            answers.append(await by["note_revise"](
                note_id=note_id, parent_revision_id=got[0].head_revision_id,
                body=GOOD_BODY, associations=[["event", assoc.id]]))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["ok", "ok"]
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=datetime.fromtimestamp(
                 NOW / 1000 + 3600, UTC)))
    assert [e.relation.dst for e in seen.relations] == [first.key]
    ids = {r.id for r in memory.graph.relations.values()
           if r.src == ("note", note_id) and r.dst == first.key}
    assert len(ids) == 2  # the re-added link is a new generation, not a duplicate


async def test_revise_with_empty_associations_clears_the_link():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []
    note_id = None

    async def behave(by, tools):
        nonlocal note_id
        await by["memory_propose"](**_about_proposal(event.id))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE)
        note_id = created["note_id"]
        got = await stores.notes.read(unit="u", note_id=note_id)
        answers.append(await by["note_revise"](
            note_id=note_id, parent_revision_id=got[0].head_revision_id,
            body=GOOD_BODY, associations=[]))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=datetime.fromtimestamp(
                 NOW / 1000 + 3600, UTC)))
    assert seen.relations == ()


async def test_revise_commit_then_projection_crash_retries_and_relinks():
    from nora.app import register_graph
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:generated-gala")
    second = _seed_event(memory, "u:event:generated-regatta")
    answers = []
    note_id = None
    head = None

    async def create_behave(by, tools):
        nonlocal note_id, head
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", first.id]], **NOTE)
        note_id = created["note_id"]
        head = (await stores.notes.read(unit="u", note_id=note_id))[0].head_revision_id

    async def revise_behave(by, tools):
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        answers.append(await by["note_revise"](
            note_id=note_id, parent_revision_id=head, body=GOOD_BODY,
            associations=[["event", second.id]]))

    real_project = register_graph.RegisterGraphLinks.project

    async def outage(self, item, session_id, *, selected=()):
        raise RuntimeError("generated projection outage")

    await session(create_behave, stores=stores, runner_kwargs={"memory": memory})
    register_graph.RegisterGraphLinks.project = outage
    await session(revise_behave, stores=stores, runner_kwargs={"memory": memory})
    register_graph.RegisterGraphLinks.project = real_project
    assert answers[-1]["status"] == "unavailable"
    # The receipt committed with the revision: the identical retry replays it
    # rather than meeting a stale_base on the row it already wrote.
    await session(revise_behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[-1]["status"] == "ok"
    assert [r.dst for r in memory.graph.relations.values()
            if r.src == ("note", note_id) and r.valid_to is None] == [second.key]


async def test_association_shown_only_by_a_memory_search_is_admissible():
    from strands.types._events import ToolResultEvent
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:search-gala")
    answers = []

    class SearchTool:
        tool_name = "memory_search"
        tool_type = "python"
        tool_spec: ClassVar = {"name": "memory_search", "description": "generated search",
                     "inputSchema": {"json": {"type": "object", "properties": {}}}}

        async def stream(self, tool_use, invocation_state, **kwargs):
            yield ToolResultEvent({"toolUseId": tool_use["toolUseId"],
                "content": [{"json": {"status": "ok",
                    # A returned node's body may MENTION a key it never
                    # returned: prose is not shown-ness; `keys` is.
                    "preview": "event:u:event:search-gala · prose says event:u:event:forged",
                    "keys": [["event", "u:event:search-gala"]]}}]})

    async def behave(by, tools):
        async for _ in by["memory_search"].stream({"toolUseId": "t1", "input": {}}, {}):
            pass
        await by["memory_propose"](**proposal())
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", "u:event:forged"]], **NOTE))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={
        "memory": memory,
        "read_tools_for": lambda unit, rooms: [SearchTool()],
        "read_only_tools": frozenset({"memory_search"})})
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "unseen_association"
    assert answers[1]["status"] == "ok"
    assert any(r.src[0] == "note" and r.dst == event.key
               for r in memory.graph.relations.values())


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
        admitted_evidence=[], shown_keys=set())
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


async def test_inspected_wake_injects_implicit_senders_plan():
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Entity, Relation, Source, WorkItem, pointer_entity
    from nora.testing.cognition_grounding import StaticGrounder

    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    actor = pointer_entity("person", "unit-1", "generated-actor", "Generated actor")
    event = Entity("generated-event", "event", "unit-1", "Generated lunar outing")
    other = Entity("other-event", "event", "other-unit", "Forbidden outing")
    for e in (actor, event, other):
        await memory.graph.upsert_entity(e)
    memory.graph.relations["plan"] = Relation("plan", actor.key, "plans", event.key,
        "notebook", "unit-1", ("generated-actor",), asserted_at=at)
    memory.graph.relations["foreign"] = Relation("foreign", actor.key, "plans", other.key,
        "notebook", "other-unit", ("generated-actor",), asserted_at=at)
    # Structural Record edges must not turn an actor into all their messages.
    memory.graph.relations["hub"] = Relation("hub", ("message", "99"), "authored_by",
        actor.key, "record", "unit-1", ("generated-actor",), asserted_at=at)
    source = Source(("message", "1"), "record", "unit-1", ("generated-actor",), "Yes, count me in")
    memory.records.items[source.key] = ComprehendItem(
        WorkItem("generated", source.key, "unit-1", "", at), (source,),
        "signal", "home-room", author="generated-actor", reply_parent=("message", "2"),
        mentions=("generated-mention",), urls=("https://generated.example/event",),
        snapshot={"messages": [{"id": 1, "body": source.text, "ts": NOW,
                    "person_id": "generated-actor", "mentions": ["generated-mention"]}]})
    grounding = Grounding(records={"messages:1": RecordItem(ref="messages:1", kind="message",
        message_id=1, room=HOME, author="Generated actor", person_id="generated-actor",
        at_ms=NOW, text=source.text)})
    runner, _, agent = _runner(memory=memory, grounder=StaticGrounder(grounding))
    inspection = await runner.inspect(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert "Generated lunar outing" in inspection["agenda"]
    assert "plans" in inspection["agenda"]
    assert "Forbidden outing" not in inspection["agenda"]
    assert "message:99" not in inspection["agenda"]
    assert "generated-mention" in inspection["agenda"]
    assert agent.calls == 0


async def test_mixed_agenda_keeps_message_room_and_author():
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.agent.fetch_watch import GraphFetched
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Entity, Relation, Source, WorkItem, pointer_entity

    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    social = replace(_pending("unit-1")[0], kind="social", ref="social:generated-post")
    message = replace(social, id=2, kind="message", ref="messages:1")
    for key, platform, room, author in (
        (("social", "generated-post"), "instagram", "generated-feed", ""),
        (("message", "1"), HOME.platform, HOME.conversation_id, "generated-author"),
    ):
        source = Source(key, "record", "unit-1", (), "Generated stimulus")
        memory.records.items[key] = ComprehendItem(
            WorkItem("generated", key, "unit-1", "", at), (source,), platform, room,
            author=author, response_closed=True,
            snapshot={"messages": [], "context": []})
    room = pointer_entity("room", "unit-1", "signal:home-room", "Generated room")
    event = Entity("room-event", "event", "unit-1", "Generated room plan")
    for e in (room, event):
        await memory.graph.upsert_entity(e)
    memory.graph.relations["room-plan"] = Relation("room-plan", room.key, "plans", event.key,
        "notebook", "unit-1", asserted_at=at)
    state = SimpleNamespace(now_ms=NOW, unit="unit-1", historical=False,
        items_by_index={0: SimpleNamespace(stimulus_ids=(1, 2), room=HOME)},
        memory_items={}, graph_fetched=GraphFetched(), shown_keys=set())
    await memory.prepare(state, {1: social, 2: message}, Grounding(records={
        "messages:1": RecordItem(ref="messages:1", kind="message", message_id=1,
            room=HOME, author="Generated author", person_id="generated-author",
            at_ms=NOW, text="Generated message")}))
    item = state.memory_items[0]
    assert (item.platform, item.room, item.author) == ("signal", "home-room", "generated-author")
    assert not item.response_closed
    assert "Generated room plan" in state.graph_text


async def test_identifier_graph_node_is_dropped_from_wake_input():
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.memory_graph import Entity, Relation, pointer_entity
    from nora.testing.cognition_grounding import StaticGrounder

    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    actor = pointer_entity("person", "unit-1", "generated-actor", "Generated actor")
    identifier = "+" + "852" + "55550123"
    unsafe = Entity("generated-contact", "event", "unit-1", "Contact " + identifier)
    for e in (actor, unsafe):
        await memory.graph.upsert_entity(e)
    memory.graph.relations["contact"] = Relation("contact", actor.key, "plans", unsafe.key,
        "notebook", "unit-1", ("generated-actor",), asserted_at=at)
    grounding = Grounding(records={"messages:1": RecordItem(ref="messages:1", kind="message",
        message_id=1, room=HOME, author="Generated actor", person_id="generated-actor",
        at_ms=NOW, text="Generated safe message")})
    runner, _, agent = _runner(memory=memory, grounder=StaticGrounder(grounding))
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None)
    inspection = await runner.inspect(view=view, now_ms=NOW)
    assert "Generated safe message" in inspection["agenda"]
    assert identifier not in inspection["agenda"]
    assert memory.metrics.get("nora_errors_total", stage="wake_prefetch") == 1
    assert agent.calls == 0
    memory.content_checks = False
    inspection = await runner.inspect(view=view, now_ms=NOW)
    assert identifier in inspection["agenda"]
