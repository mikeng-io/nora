"""Generated regression inputs for PR525's remaining engineering boundaries."""
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app import unified_backfill as ub
from nora.app.cognition_memory import WakeMemory, historical_graph
from nora.domain.cognition_session import Room, build_agenda
from nora.domain.memory_graph import (
    Admission,
    Entity,
    GraphEdge,
    GraphNode,
    Relation,
    Subgraph,
)
from nora.infra.sql import unified_backfill as sql
from tests.unit.test_unified_backfill import _FakeEngine, _patch_sql, _run_kwargs, _Runner, _s

AT = datetime(2026, 1, 1, tzinfo=UTC)


def test_historical_graph_withholds_unversioned_heads_and_future_edges():
    person = Entity("p1", "person", "u1", "future-name",
                    attributes={"occupation": "future-secret"})
    relation = Relation("old", person.key, "plans", ("event", "e1"),
                        "notebook", "u1", ("p1",), asserted_at=AT)
    graph = Subgraph("ok", (
        GraphNode(person.key, entity=person, fields={"body": "future-secret"}),
        GraphNode(("note", "n1"), fields={"body": "future-note"}),
    ), (GraphEdge(relation, 9), GraphEdge(replace(
        relation, id="future-edge", asserted_at=AT + timedelta(hours=1)), 1)))
    safe = historical_graph(graph, AT)
    assert [edge.relation.id for edge in safe.relations] == ["old"]
    assert safe.nodes == (GraphNode(person.key),)
    assert "future" not in str(asdict(safe))


@pytest.mark.asyncio
async def test_omitted_stimulus_never_certifies_completion(monkeypatch):
    stimuli = [_s(1, 100), _s(2, 100, kind="rollup")]
    runner = _Runner(work_by_batch=[[{
        "index": 0, "work_id": "stimuli:1", "input_hash": "h",
        "inputs": {"1": "h"},
    }]])
    _patch_sql(monkeypatch, stimuli=stimuli, pairs={("stimuli:1", "h")})
    result = await ub.run_units(_FakeEngine(object()), runner=runner, **_run_kwargs())
    assert result["units"]["u1"]["stopped"] == "unresolved"
    assert result["units"]["u1"]["failures"] == 1
    assert not runner.replay_calls


@pytest.mark.asyncio
async def test_source_receipts_survive_regrouping_and_changed_input_is_not_skipped(monkeypatch):
    stimuli = [_s(i, i) for i in range(1, 41)]
    pairs = {(f"input:u1:{i}", f"source-{i}") for i in range(1, 41)}
    _patch_sql(monkeypatch, stimuli=stimuli, pairs=pairs)
    work = [{"index": 0, "work_id": "stimuli:" + ",".join(str(i) for i in range(1, 41)),
             "input_hash": "new-group",
             "inputs": {str(i): f"source-{i}" for i in range(1, 41)}}]
    runner = _Runner(work_by_batch=[work])
    result = await ub.run_units(_FakeEngine(object()), runner=runner,
                                batch_stimuli=40, **_run_kwargs())
    assert result["units"]["u1"]["stopped"] == "done"
    assert not runner.replay_calls and result["model_batches"] == 0
    work[0]["inputs"]["40"] = "edited-source"
    runner = _Runner(work_by_batch=[work])
    result = await ub.run_units(_FakeEngine(object()), runner=runner,
                                batch_stimuli=40, **_run_kwargs())
    assert runner.replay_calls
    assert runner.replay_calls[0][0] == (40,)
    assert result["units"]["u1"]["stopped"] == "unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["enumeration", "initial_inspection", "reinspection"])
async def test_run_deadline_encloses_each_phase(monkeypatch, phase):
    import asyncio

    _patch_sql(monkeypatch, stimuli=[_s(1, 100)])
    runner = _Runner()
    finished = []
    if phase == "enumeration":
        async def slow(*args, **kwargs):
            await asyncio.sleep(0.04)
            finished.append(True)
            return [_s(1, 100)], {}
        monkeypatch.setattr(ub, "enumerate_unit", slow)
    else:
        original = runner.inspect
        calls = 0
        async def inspect(**kwargs):
            nonlocal calls
            calls += 1
            if calls == (1 if phase == "initial_inspection" else 2):
                await asyncio.sleep(0.04)
                finished.append(True)
            return await original(**kwargs)
        runner.inspect = inspect
    kwargs = _run_kwargs()
    kwargs["bounds"] = ub.Bounds(max_batches=1, max_tokens=1000, max_seconds=0.005)
    result = await ub.run_units(_FakeEngine(object()), runner=runner, **kwargs)
    assert result["stopped"] == "max_seconds"
    assert not finished
    assert not runner.replay_calls


@pytest.mark.asyncio
async def test_catch_up_preserves_reminder_unit_scope_and_selected_graph_anchors(monkeypatch):
    room = Room("signal", "room-1")
    _patch_sql(monkeypatch, stimuli=[_s(1, 100)], pairs={("input:u1:1", "h")})
    captured = []
    class Store:
        async def append(self, *, stimulus, recorded_at_ms):
            captured.append(stimulus)
            return 9
    async def reminders():
        return [SimpleNamespace(text="allowed-subject", platform="signal",
                                conversation_id="room-1"),
                SimpleNamespace(text="private-other-unit", platform="signal",
                                conversation_id="other")]
    async def selected(conn, unit, now_ms, *, remind_subjects):
        return {"unit": unit, "as_of_ms": now_ms,
                "relations": [{"id": "plan", "src": ["person", "p1"],
                               "dst": ["event", "e1"], "persons": ["p1"]}],
                "reminds": list(remind_subjects), "events": [], "commitments": []}
    monkeypatch.setattr(sql, "still_open", selected)
    result = await ub.catch_up(_FakeEngine(object()), stimulus_store=Store(),
        runner=_Runner(), units=["u1"], home_rooms={"u1": room},
        rooms_of_unit=lambda unit: {("signal", "room-1")}, unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=(), now_ms=100, pending_reminds=reminders)
    assert result["units"]["u1"]["status"] == "queued"
    assert "private-other-unit" not in captured[0].item
    assert "allowed-subject" in captured[0].item
    pending = _s(9, 100, kind="time", item=captured[0].item, ref=captured[0].ref)
    agenda = build_agenda([pending], home_room=room, max_items=12)
    assert "private-other-unit" not in str(asdict(agenda))
    assert "allowed-subject" in str(asdict(agenda))
    walked = []
    class Graph:
        async def aliases(self, **kwargs):
            return {}
        async def walk(self, seeds, query):
            walked.append((seeds, query))
            return Subgraph("ok")
    memory = WakeMemory(graph=Graph(), records=None, metrics=None, content_checks=False)
    state = SimpleNamespace(unit="u1", now_ms=100, historical=False,
        items_by_index=dict(enumerate(agenda.items)), memory_items={},
        graph_fetched=GraphFetched(), shown_keys=set())
    await memory.prepare(state, {9: pending}, SimpleNamespace(records={}, rollups={}))
    assert ("event", "e1") in walked[0][0]
    assert walked[0][1].persons == frozenset({"p1"})


@pytest.mark.asyncio
async def test_historical_proposal_result_uses_safe_graph_payload():
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem

    relation = Relation("plan", ("person", "p1"), "plans", ("event", "e1"),
                        "notebook", "u1", ("p1",), asserted_at=AT)

    class Records:
        async def readings(self, *args):
            pass
    class Graph:
        async def walk(self, seeds, query):
            assert seeds == (("person", "p1"), ("event", "e1"))
            assert query.known_at == AT
            return Subgraph("ok", (GraphNode(("event", "e1"),
                entity=Entity("e1", "event", "u1", "future-private-name")),),
                (GraphEdge(relation, 0),))
    class Admitter:
        async def _admit(self, *args):
            return (Admission(relations=(relation,)), [],
                    {"report": {0: {"status": "saved", "kind": "relation"}},
                     "claims": {}, "cited_items": {0: {0}}})
    memory = WakeMemory(records=Records(), graph=Graph(), metrics=None, content_checks=False)
    memory.admission = Admitter()
    state = SimpleNamespace(unit="u1", session_id=1, historical=True,
        memory_items={0: ComprehendItem(WorkItem("s", ("stimulus", "1"), "u1", "h", AT),
            (Source(("message", "1"), "record", "u1", ("p1",), "a plan"),),
            "signal", "c1", snapshot={"messages": []})},
        graph_fetched=GraphFetched(), shown_keys=set(),
        admitted_relations={}, admitted_evidence=[])
    state.graph_fetched.show(
        Source(("message", "1"), "record", "u1", ("p1",), "a plan"))
    statements = [{
        "type": "relation", "rel": "plans", "subject": "@person:p1",
        "object": "@event:e1", "cite": [{"source": 1, "quote": "plan"}]}]
    result = await memory.propose(state,  statements=statements)
    assert result["status"] == "ok"
    assert "future-private-name" not in str(result)
    assert result["connected"]["nodes"][0]["key"] == ["event", "e1"]


@pytest.mark.asyncio
async def test_prepared_input_hash_ignores_source_and_person_iteration_order():
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph

    async def prepare(reverse=False, text="Generated first source", persons=("p1", "p2")):
        sources = [Source(("social", "first"), "record", "u1", persons, text),
                   Source(("social", "second"), "record", "u1", persons, "Generated second")]
        if reverse:
            sources = [replace(source, persons=tuple(reversed(source.persons)))
                       for source in reversed(sources)]
        records = InMemoryComprehendRecords()
        records.items["social", "generated-read"] = ComprehendItem(
            WorkItem("read", ("social", "generated-read"), "u1", "", AT),
            tuple(sources), "signal", "room-1")
        memory = WakeMemory(graph=FakeMemoryGraph(), records=records,
                            metrics=None, content_checks=False)
        state = SimpleNamespace(unit="u1", session_id=1, historical=True,
            now_ms=int(AT.timestamp() * 1000), memory_items={},
            graph_fetched=GraphFetched(), shown_keys=set(),
            items_by_index={0: SimpleNamespace(stimulus_ids=(1,),
                                               room=Room("signal", "room-1"))})
        await memory.prepare(state, {1: _s(1, state.now_ms, kind="social", item="post",
                                           ref="social:generated-read")},
                             SimpleNamespace(records={}))
        return state.memory_items[0]

    first, reordered = await prepare(), await prepare(reverse=True)
    assert first.sources != reordered.sources
    assert first.work.input_hash == reordered.work.input_hash
    assert first.snapshot["stimulus_inputs"] == reordered.snapshot["stimulus_inputs"]
    for changed in (await prepare(text="Changed evidence"), await prepare(persons=("p1",))):
        assert first.work.input_hash != changed.work.input_hash
        assert first.snapshot["stimulus_inputs"] != changed.snapshot["stimulus_inputs"]
