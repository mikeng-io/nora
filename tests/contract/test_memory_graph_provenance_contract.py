"""Source-provenance contract for `stage_and_admit`: punctuation spans and
attribute spans keep store provenance on both implementations."""

from dataclasses import replace

import pytest

from nora.domain.memory_graph import Evidence, Refusal, Walk
from tests.contract.test_memory_graph_contract import (
    EV,
    EVENT,
    PERSON,
    REL,
    SOURCE,
)
from tests.contract.test_memory_graph_contract import _graph_db as _graph_db
from tests.contract.test_memory_graph_contract import (
    graph as graph,
)


@pytest.mark.parametrize("span,accepted", [
    ("the event, by train", True),
    ("the event, by plane", False),
])
async def test_api_punctuation_span_keeps_store_provenance(graph, span, accepted):
    source = replace(SOURCE, text="I attended the event — by train.")
    evidence = replace(EV, span=span)
    result = await graph.stage_and_admit(
        entities=(PERSON, EVENT), relations=(REL,), evidence=(evidence,),
        sources={source.key: source})
    assert bool(result.relations) is accepted
    if accepted:
        assert result.evidence == (evidence,)
        readback = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
        assert readback.status == "ok" and readback.relations[0].evidence_count == 1
    else:
        assert Refusal("relation", REL.id, "span_not_in_source") in result.refused
    assert source.text == "I attended the event — by train."


@pytest.mark.parametrize("invented_tail", [False, True])
async def test_api_punctuation_attribute_keeps_store_provenance(graph, invented_tail):
    source = replace(SOURCE, persons=(), text="I attended the event — by train.")
    event = replace(EVENT, attributes={"travel": "train"})
    attribute = Evidence(source.key, entity_id=event.id, attribute="travel",
                         span="event, by train" + (" invented" if invented_tail else ""))
    result = await graph.stage_and_admit(
        entities=(PERSON, event), relations=(REL,), evidence=(EV, attribute),
        sources={source.key: source})
    assert result.relations == (REL,)
    stored = next(e for e in result.entities if e.id == event.id)
    assert dict(stored.attributes) == ({} if invented_tail else {"travel": "train"})
    assert (attribute in result.evidence) is not invented_tail
    if invented_tail:
        assert Refusal("evidence", event.id, "span_not_in_source",
                       attribute="travel") in result.refused
    readback = await graph.walk((PERSON.key,), Walk("u1", frozenset({"p1"})))
    persisted = next(n for n in readback.nodes if n.key == event.key)
    assert persisted.entity is not None
    assert dict(persisted.entity.attributes) == dict(stored.attributes)
    assert source.text == "I attended the event — by train."

