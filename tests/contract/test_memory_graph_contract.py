"""Shared contract. Phase 1 must replace the explicit SQL skip with its loader."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.memory_graph import (
    ENTITY_KINDS,
    GRAPH_RELATIONS,
    RELATION_RULES,
    Entity,
    Evidence,
    MemoryGraph,
    Relation,
    Source,
    Walk,
    WorkItem,
)
from nora.testing.memory_graph import FakeMemoryGraph

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PERSON = Entity("p1", "person", "u1", "Person One")
EVENT = Entity("e1", "event", "u1", "Generated Event")
SOURCE = Source(("message", "m1"), "record", "u1", ("p1",), "I attended the event.")
REL = Relation("r1", PERSON.key, "attended", EVENT.key, "notebook", "u1", ("p1",), asserted_at=NOW)
EV = Evidence(SOURCE.key, relation_id="r1", span="attended")


@pytest.fixture(
    params=[
        "fake",
        pytest.param(
            "sql",
            marks=pytest.mark.skip(reason="Phase 1: Postgres memory graph implementation pending"),
        ),
    ]
)
def graph(request: pytest.FixtureRequest) -> MemoryGraph:
    assert request.param == "fake"
    return FakeMemoryGraph()


async def load(graph: MemoryGraph, relation: Relation = REL) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(relation,),
        evidence=(replace(EV, relation_id=relation.id),),
        sources={SOURCE.key: SOURCE},
    )
    assert result.relations == (relation,)
    assert not result.refused


def test_vocabulary_is_complete() -> None:
    assert set(GRAPH_RELATIONS) == set(RELATION_RULES)
    assert len(ENTITY_KINDS) == 8


@pytest.mark.parametrize(
    ("change", "source", "evidence", "reason"),
    [
        ({"rel": "invented"}, SOURCE, EV, "vocabulary"),
        ({"dst": ("work", "e1")}, SOURCE, EV, "wrong_ends"),
        ({"layer": "record"}, SOURCE, EV, "wrong_layer"),
        ({}, replace(SOURCE, layer="notebook"), EV, "notebook_evidence"),
        ({}, SOURCE, replace(EV, source=("message", "unseen")), "unseen_source"),
        ({}, SOURCE, replace(EV, span="not present"), "span_not_in_source"),
        ({"unit": "u2"}, SOURCE, EV, "scope_wider_than_evidence"),
        ({"persons": ()}, SOURCE, EV, "scope_wider_than_evidence"),
    ],
)
async def test_refusals(
    graph: MemoryGraph, change: dict, source: Source, evidence: Evidence, reason: str
) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(replace(REL, **change),),
        evidence=(evidence,),
        sources={source.key: source},
    )
    assert not result.relations
    assert reason in [r.reason for r in result.refused]
    assert not (await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))).nodes


async def test_partial_commit(graph: MemoryGraph) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT),
        relations=(REL, replace(REL, id="bad", rel="invented")),
        evidence=(EV,),
        sources={SOURCE.key: SOURCE},
    )
    assert result.relations == (REL,)
    assert result.evidence == (EV,)
    assert result.refused[0].reason == "vocabulary"
    preview = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
    assert preview.status == "ok"
    assert len(preview.nodes) == 2
    assert preview.relations[0].evidence_count == 1
    assert preview.relations[0].relation.layer == "notebook"


async def test_missing_evidence_and_orphans(graph: MemoryGraph) -> None:
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT), relations=(REL,), evidence=(), sources={}
    )
    assert {r.reason for r in result.refused} == {"missing_evidence", "orphan_entity"}


@pytest.mark.parametrize("caps", [{"nodes": 1}, {"depth": 0}, {"fan_out": 1}])
async def test_caps_are_partial(graph: MemoryGraph, caps: dict) -> None:
    await load(graph)
    other = replace(EVENT, id="e2", name="Another Generated Event")
    r2 = replace(REL, id="r2", dst=other.key)
    result = await graph.stage_and_admit(
        entities=(other,),
        relations=(r2,),
        evidence=(replace(EV, relation_id="r2"),),
        sources={SOURCE.key: SOURCE},
    )
    assert result.relations
    result_walk = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"}), **caps))
    assert result_walk.status == "partial"
    assert len(result_walk.nodes) <= caps.get("nodes", 100)


async def test_hub_rule_and_scope_and_filters(graph: MemoryGraph) -> None:
    await load(graph)
    pointer = Relation(
        "pointer", SOURCE.key, "authored_by", PERSON.key, "record", "u1", ("p1",), asserted_at=NOW
    )
    assert (
        await graph.stage_and_admit(entities=(), relations=(pointer,), evidence=(), sources={})
    ).relations
    walk = await graph.walk(
        (PERSON.key,), Walk("u1", frozenset({"p1"}), layers=("record", "notebook"))
    )
    assert {n.key for n in walk.nodes} == {PERSON.key, EVENT.key}
    assert [e.relation.id for e in walk.relations] == ["r1"]
    for query in (
        Walk("u2", frozenset({"p1"})),
        Walk("u1", frozenset()),
        Walk("u1", frozenset({"p1"}), layers=("rollup",)),
    ):
        assert not (await graph.walk((PERSON.key,), query)).nodes


async def test_validity(graph: MemoryGraph) -> None:
    await load(graph, replace(REL, valid_from=NOW, valid_to=NOW + timedelta(days=1)))
    for at, count in ((NOW - timedelta(seconds=1), 0), (NOW, 1), (NOW + timedelta(days=1), 0)):
        result = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"}), valid_at=at))
        assert len(result.relations) == count


async def test_work_lifecycle(graph: MemoryGraph) -> None:
    item = WorkItem("w1", SOURCE.key, "u1", "generated-hash", NOW)
    assert await graph.enqueue(item) == await graph.enqueue(replace(item, id="duplicate"))
    assert await graph.claim(historical=True, now=NOW, lease_seconds=10) is None
    first = await graph.claim(historical=False, now=NOW, lease_seconds=10)
    assert first and first.attempts == 1
    assert await graph.claim(historical=False, now=NOW, lease_seconds=10) is None
    second = await graph.claim(historical=False, now=NOW + timedelta(seconds=11), lease_seconds=10)
    assert second and second.attempts == 2
    assert not await graph.settle(first, now=NOW)
    assert not await graph.fail(first, error="stale", now=NOW)
    assert await graph.settle(second, now=NOW)
    assert (
        await graph.claim(historical=False, now=NOW + timedelta(days=1), lease_seconds=10) is None
    )
    changed = await graph.enqueue(replace(item, id="w2", input_hash="changed-hash"))
    assert changed.state == "pending"
    for attempt in range(1, 6):
        now = NOW + timedelta(days=attempt)
        claimed = await graph.claim(historical=False, now=now, lease_seconds=10)
        assert claimed and claimed.attempts == attempt
        assert await graph.fail(claimed, error="generated failure", now=now)
    parked = await graph.enqueue(replace(changed, id="duplicate-failed"))
    assert parked.state == "failed" and parked.attempts == 5
    assert parked.last_error == "generated failure"
    assert (
        await graph.claim(historical=False, now=NOW + timedelta(days=10), lease_seconds=10) is None
    )


async def test_attribute_evidence_is_admitted_independently(graph: MemoryGraph) -> None:
    event = replace(EVENT, attributes={"category": "generated", "venue": "unsupported"})
    public = replace(SOURCE, persons=())
    attribute = Evidence(SOURCE.key, entity_id=EVENT.id, attribute="category", span="event")
    result = await graph.stage_and_admit(
        entities=(PERSON, event),
        relations=(REL,),
        evidence=(EV, attribute),
        sources={public.key: public},
    )
    assert result.relations == (REL,)
    assert result.entities[1].attributes == {"category": "generated"}
    assert attribute in result.evidence
    assert any(r.reason == "missing_evidence" for r in result.refused)


async def test_expired_attempts_park_without_deleting(graph: MemoryGraph) -> None:
    item = WorkItem("expired", SOURCE.key, "u1", "expired-hash", NOW, historical=True)
    await graph.enqueue(item)
    for attempt in range(1, 6):
        claimed = await graph.claim(
            historical=True, now=NOW + timedelta(days=attempt), lease_seconds=10
        )
        assert claimed and claimed.attempts == attempt
    assert await graph.claim(historical=True, now=NOW + timedelta(days=6), lease_seconds=10) is None
    assert (await graph.enqueue(item)).state == "failed"


async def test_unavailable_is_not_empty_success() -> None:
    graph = FakeMemoryGraph()
    await load(graph)
    assert (await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))).nodes
    graph.available = False
    result = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
    assert result.status == "unavailable"
    assert not result.nodes and not result.relations


async def test_attributes_accumulate_across_batches(graph: MemoryGraph) -> None:
    public = replace(SOURCE, persons=())
    first = replace(EVENT, attributes={"category": "generated"})
    first_ev = Evidence(SOURCE.key, entity_id=EVENT.id, attribute="category", span="event")
    await graph.stage_and_admit(
        entities=(PERSON, first),
        relations=(REL,),
        evidence=(EV, first_ev),
        sources={public.key: public},
    )
    second = replace(EVENT, attributes={"venue": "generated venue"})
    second_ev = Evidence(SOURCE.key, entity_id=EVENT.id, attribute="venue", span="event")
    r2 = replace(REL, id="r2")
    result = await graph.stage_and_admit(
        entities=(PERSON, second),
        relations=(r2,),
        evidence=(replace(EV, relation_id="r2"), second_ev),
        sources={public.key: public},
    )
    event = next(e for e in result.entities if e.id == EVENT.id)
    assert event.attributes == {"category": "generated", "venue": "generated venue"}


def test_a_relation_without_asserted_at_is_refused() -> None:
    with pytest.raises(ValueError, match="asserted_at"):
        replace(REL, asserted_at=None)
