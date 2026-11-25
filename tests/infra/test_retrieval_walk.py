"""Exercise exact and ranked seeds, bounded walks, and lexical degradation."""

from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH
from nora.memory.domain.retrieval import EvidenceHandle, RetrievalAnchors
from nora.testing.memory_index import bag_of_words
from tests.infra.test_retrieval_scope import (
    AT,
    BOUNDS,
    PERSON,
    READ,
    references,
    write_edge,
)
from tests.infra.test_retrieval_scope import scenario as scope_scenario

scenario = scope_scenario


async def test_exact_ref_and_handle_win_over_ranked_hits(scenario) -> None:
    await scenario.item("exact", "Generated alternative")
    await scenario.item("lexical", "Lantern celebrations")
    vector_query = "Quasars"
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.update(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.graph_id == "lexical",
        ).values(embedding=bag_of_words(vector_query), embedding_model="fixture-model"))
    service = scenario.service(model="fixture-model")
    result = await service.retrieve(
        READ, RetrievalAnchors(
            handles=(EvidenceHandle("exact").handle,), query="Lantern celebrations",
        ),
        replace(BOUNDS, rows=1),
    )
    assert [row.graph_id for row in result.rows] == ["exact"]
    assert result.status == "partial" and result.limits == ("rows",)
    vector_result = await service.retrieve(READ, RetrievalAnchors(query=vector_query), BOUNDS)
    assert vector_result.status == "ok"
    assert [row.graph_id for row in vector_result.rows] == ["lexical"]


@pytest.mark.parametrize(
    ("limit", "changes", "visible"),
    [
        ("rows", {"rows": 1}, {"root"}),
        ("depth", {"depth": 0}, {"root"}),
        ("fan_out", {"fan_out": 1}, {"root", "one"}),
        ("time_window", {"until": AT}, {"root", "two"}),
    ],
)
async def test_each_walk_bound_reports_partial(scenario, limit, changes, visible) -> None:
    await scenario.entity("root")
    await scenario.item("one", at=AT + timedelta(hours=1))
    await scenario.item("two")
    await write_edge(scenario, "one", "root", "about")
    await write_edge(scenario, "two", "root", "about")
    service = scenario.service()
    complete = await service.retrieve(READ, references("root"), BOUNDS)
    assert complete.status == "ok"
    assert {row.graph_id for row in complete.rows} == {"root", "one", "two"}
    result = await service.retrieve(READ, references("root"), replace(BOUNDS, **changes))
    assert result.status == "partial"
    assert limit in result.limits
    assert {row.graph_id for row in result.rows} == visible


async def test_person_anchor_walks_incoming_claims_and_never_item_edges(scenario) -> None:
    await scenario.entity("person-anchor", pointer_kind="identity", pointer_ref=PERSON)
    await scenario.claim("someone-claim", identities=[PERSON])
    await scenario.item("earlier-message", at=AT - timedelta(hours=1))
    await write_edge(scenario, "someone-claim", "person-anchor", "about")
    await write_edge(scenario, "earlier-message", "person-anchor", "about")
    result = await scenario.service().retrieve(READ, references("person-anchor"), BOUNDS)
    assert [(row.graph_id, row.distance) for row in result.rows] == [
        ("person-anchor", 0), ("someone-claim", 1),
    ]


async def test_citation_brings_its_item(scenario) -> None:
    await scenario.claim("speaking-claim")
    await scenario.item("cited-message")
    await write_edge(scenario, "speaking-claim", "cited-message", "cites")
    result = await scenario.service().retrieve(READ, references("speaking-claim"), BOUNDS)
    assert [(row.graph_id, row.distance) for row in result.rows] == [
        ("speaking-claim", 0), ("cited-message", 1),
    ]


async def test_claim_brings_later_topic_items_in_newest_order(scenario) -> None:
    await scenario.entity("topic")
    await scenario.claim("topic-claim")
    await scenario.item("before", at=AT - timedelta(hours=1))
    await scenario.item("after", at=AT + timedelta(hours=1))
    await scenario.item("newest", at=AT + timedelta(hours=2))
    await write_edge(scenario, "topic-claim", "topic", "about")
    for graph_id in ("before", "after", "newest"):
        await write_edge(scenario, graph_id, "topic", "about")
    result = await scenario.service().retrieve(
        READ, references("topic-claim"), replace(BOUNDS, depth=1),
    )
    assert [row.graph_id for row in result.rows] == ["topic-claim", "topic", "newest", "after"]


async def test_embedder_outage_preserves_only_available_lexical_hits(scenario) -> None:
    await scenario.item("lexical-hit", "Lantern celebrations")
    service = scenario.service(fail=TimeoutError("fixture embedder unavailable"))
    partial = await service.retrieve(READ, RetrievalAnchors(query="Lantern celebrations"), BOUNDS)
    assert partial.status == "partial"
    assert partial.cause == "query_embedding_failed"
    assert [row.graph_id for row in partial.rows] == ["lexical-hit"]
    unavailable = await service.retrieve(READ, RetrievalAnchors(query="Quasars"), BOUNDS)
    assert (unavailable.status, unavailable.cause) == ("unavailable", "query_embedding_failed")
    assert unavailable.rows == ()
