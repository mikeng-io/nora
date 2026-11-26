"""Historical `WakeMemory` boundaries retained after the backfill driver left."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory, historical_graph
from nora.domain.cognition_session import Room
from nora.domain.memory_graph import (
    CATCH_UP_PREFIX,
    Admission,
    Entity,
    GraphEdge,
    GraphNode,
    Relation,
    Subgraph,
)
from nora.domain.stimuli import Stimulus

pytestmark = pytest.mark.asyncio

AT = datetime(2026, 1, 1, tzinfo=UTC)


def _s(sid: int, at: int, *, kind: str = "message", item: str = "message",
       ref: str | None = None, unit: str = "u1", platform: str = "signal",
       cid: str = "room-1") -> Stimulus:
    return Stimulus(
        id=sid, unit=unit, kind=kind, item=item,
        ref=ref or f"messages:{sid}", at_ms=at, recorded_at_ms=at,
        platform=platform, conversation_id=cid, person_id="p1",
        cues=(), waits=False, due_ms=None,
    )


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


async def test_prepare_processes_a_catch_up_stimulus_and_selects_its_graph_anchors():
    selected = {"unit": "u1", "as_of_ms": 100,
                "relations": [{"id": "plan", "src": ["person", "p1"],
                               "dst": ["event", "e1"], "persons": ["p1"]}],
                "reminds": ["allowed-subject"], "events": [], "commitments": []}
    item = CATCH_UP_PREFIX + json.dumps(selected, sort_keys=True)
    pending = _s(9, 100, kind="time", item=item, ref="time:catch-up")
    room = Room("signal", "room-1")
    walked = []

    class Graph:
        async def aliases(self, **kwargs):
            return {}

        async def walk(self, seeds, query):
            walked.append((seeds, query))
            return Subgraph("ok")

    memory = WakeMemory(graph=Graph(), records=None, metrics=None, content_checks=False)
    state = SimpleNamespace(unit="u1", now_ms=100, historical=False,
        items_by_index={0: SimpleNamespace(stimulus_ids=(9,), room=room)},
        memory_items={}, graph_fetched=GraphFetched(), shown_keys=set())
    await memory.prepare(state, {9: pending}, SimpleNamespace(records={}, rollups={}))
    assert ("event", "e1") in walked[0][0]
    assert walked[0][1].identities == frozenset({"p1"})
    assert state.memory_items[0].sources[0].text == item

    foreign = dict(selected["relations"][0], persons=["p2"])
    foreign_selected = {**selected, "unit": "other", "relations": [foreign]}
    foreign_pending = _s(10, 100, kind="time", ref="time:catch-up",
        item=CATCH_UP_PREFIX + json.dumps(foreign_selected, sort_keys=True))
    state.memory_items.clear()
    state.items_by_index[0].stimulus_ids = (10,)
    with pytest.raises(ValueError, match="outside the unit"):
        await memory.prepare(state, {10: foreign_pending}, SimpleNamespace(records={}, rollups={}))


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
        async def _admit(self, *args, confirmed=()):
            return (Admission(relations=(relation,)), [],
                    {"report": {0: {"status": "saved", "kind": "relation"}},
                     "claims": {}})

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
    result = await memory.propose(state, statements=statements)
    assert result["status"] == "ok"
    assert "future-private-name" not in str(result)
    assert result["connected"]["nodes"][0]["key"] == ["event", "e1"]


async def test_prepared_input_hash_ignores_source_and_person_iteration_order():
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph

    async def prepare(reverse=False, text="Generated first source", persons=("p1", "p2")):
        sources = [Source(("social", "first"), "record", "u1", persons, text),
                   Source(("social", "second"), "record", "u1", persons, "Generated second")]
        if reverse:
            sources = [replace(source, identities=tuple(reversed(source.identities)))
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
                             SimpleNamespace(records={}, rollups={}))
        return state.memory_items[0]

    first, reordered = await prepare(), await prepare(reverse=True)
    assert first.sources != reordered.sources
    assert first.work.input_hash == reordered.work.input_hash
    assert first.snapshot["stimulus_inputs"] == reordered.snapshot["stimulus_inputs"]
    for changed in (await prepare(text="Changed evidence"), await prepare(persons=("p1",))):
        assert first.work.input_hash != changed.work.input_hash
        assert first.snapshot["stimulus_inputs"] != changed.snapshot["stimulus_inputs"]
