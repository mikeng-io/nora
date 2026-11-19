"""Read current and historical graph facts with their evidence and outcome."""

from datetime import timedelta

import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import CLAIM, GRAPH_SEARCH, INTENTION
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.schema import MESSAGES
from nora.memory.domain.retrieval import (
    EvidenceHandle,
    RetrievalAnchors,
    RetrievedEdge,
    RetrievedPerson,
)
from tests.infra.test_retrieval_scope import (
    AT,
    BOUNDS,
    PERSON,
    READ,
    ROOM,
    UNIT,
    claim_values,
    references,
    write_edge,
)
from tests.infra.test_retrieval_scope import scenario as scope_scenario

scenario = scope_scenario


async def test_superseded_seed_loads_its_successor(scenario) -> None:
    await scenario.claim("initial-claim")
    await scenario.graph.revise(CLAIM, claim_values(
        "current-claim", replaces="initial-claim", content={"text": "Current content"},
    ))
    result = await scenario.service().retrieve(READ, references("initial-claim"), BOUNDS)
    assert result.status == "ok"
    (current,) = result.rows
    assert (current.graph_id, current.content, current.distance) == (
        "current-claim", {"text": "Current content"}, 0,
    )


async def test_stale_index_text_never_revives_a_retired_claim(scenario) -> None:
    await scenario.claim("retired-claim", content={"text": "Archive evidence"}, state="retired")
    async with scenario.engine.connect() as conn:
        assert await conn.scalar(sa.select(GRAPH_SEARCH.c.text).where(
            GRAPH_SEARCH.c.graph_id == "retired-claim",
        )) == "Archive evidence"
    service = scenario.service()
    search = await service.retrieve(READ, RetrievalAnchors(query="Archive evidence"), BOUNDS)
    assert search.status == "nothing_found"
    exact = await service.retrieve(READ, references("retired-claim"), BOUNDS)
    assert exact.status == "ok"
    assert [row.state for row in exact.rows] == ["retired"]


async def test_rows_explain_their_identity_scope_time_people_evidence_and_endings(scenario) -> None:
    later = AT + timedelta(hours=1)
    await scenario.item("cited-item")
    await scenario.item("deleted-item", at=AT - timedelta(hours=1))
    await scenario.claim("recorded-claim", persons=[PERSON], valid_from=AT)
    await scenario.graph.create(INTENTION, {
        "id": "held-intention", "unit": UNIT, "kind": "intention", "basis": "committed",
        "state": "open", "writer": "fixture", "exposure": "internal", "origin_room": ROOM,
        "asserted_at": AT, "owner": "nora", "owed_to": PERSON, "persons": [PERSON],
        "form": "achieve", "done_when_form": "act_event", "done_when": {"act": "fixture"},
        "drop_when_form": "deadline", "drop_when": {"at": later.isoformat()},
        "deadline": later, "review_time": later, "delivery_mode": "when_it_comes_up",
    })
    await write_edge(scenario, "recorded-claim", "cited-item", "cites")
    await write_edge(scenario, "recorded-claim", "deleted-item", "cites")
    await write_edge(scenario, "held-intention", "cited-item", "cites")
    async with scenario.engine.begin() as conn:
        await tombstone_items(conn, MESSAGES, MESSAGES.c.graph_id == "deleted-item")
    result = await scenario.service().retrieve(
        READ, references("recorded-claim", "held-intention"), BOUNDS,
    )
    assert result.status == "ok"
    claim, intention, item = result.rows
    assert (item.graph_id, item.kind, item.source, item.state, item.basis) == (
        "cited-item", "item", "messages", "live", "observed",
    )
    assert (item.occurred_at, item.asserted_at, item.valid_from, item.valid_to) == (
        AT, AT, AT, None,
    )
    assert (item.origin_room, item.exposure, item.distance, item.already_shown) == (
        ROOM, "external", 1, False,
    )
    assert item.persons == (RetrievedPerson(PERSON, "author"),)
    assert item.content == {"body": "Generated observation"}
    assert (claim.kind, claim.basis, claim.reasoning, claim.confidence) == (
        "claim", "inferred", "deduced", "high",
    )
    assert claim.persons == (RetrievedPerson(PERSON, "subject"),)
    assert claim.close_when == {"valid_to": None, "window_end": None}
    assert claim.edges == (RetrievedEdge("cites", "recorded-claim", "cited-item"),)
    assert claim.evidence == (EvidenceHandle("cited-item"), EvidenceHandle("deleted-item", True))
    assert (intention.kind, intention.basis, intention.state) == ("intention", "committed", "open")
    assert intention.persons == (
        RetrievedPerson("nora", "owner"), RetrievedPerson(PERSON, "owed_to"),
    )
    assert intention.close_when == {
        "done_when": {"act": "fixture"}, "drop_when": {"at": later.isoformat()},
        "deadline": later.isoformat(), "review_time": later.isoformat(),
    }
    assert intention.evidence == (EvidenceHandle("cited-item"),)


async def test_repeat_flag_does_not_change_status_and_new_count_excludes_it(scenario) -> None:
    await scenario.item("was-shown")
    await scenario.item("newly-found")
    result = await scenario.service().retrieve(
        READ, references("was-shown", "newly-found"), BOUNDS,
        already_shown=frozenset({"was-shown"}),
    )
    assert result.status == "ok"
    assert [(row.graph_id, row.already_shown) for row in result.rows] == [
        ("was-shown", True), ("newly-found", False),
    ]
    assert result.new_count == 1


async def test_as_of_hides_later_registration_and_reads_revision_then_in_force(scenario) -> None:
    await scenario.claim("then-claim")
    at = await scenario.now()
    await scenario.graph.revise(CLAIM, claim_values("now-claim", replaces="then-claim"))
    await scenario.claim("later-claim")
    service = scenario.service()
    current = await service.retrieve(READ, references("then-claim", "later-claim"), BOUNDS)
    assert [row.graph_id for row in current.rows] == ["now-claim", "later-claim"]
    old = await service.retrieve(READ, references("then-claim", "later-claim"), BOUNDS, as_of=at)
    assert old.status == "ok"
    assert [(row.graph_id, row.state, row.content) for row in old.rows] == [
        ("then-claim", "live", {"text": "then-claim"}),
    ]


async def test_as_of_marks_tombstoned_content_unavailable_without_returning_it(scenario) -> None:
    await scenario.item("pruned-item")
    at = await scenario.now()
    async with scenario.engine.begin() as conn:
        await tombstone_items(conn, MESSAGES, MESSAGES.c.graph_id == "pruned-item")
    service = scenario.service()
    live = await service.retrieve(READ, references("pruned-item"), BOUNDS)
    assert live.status == "nothing_found"
    old = await service.retrieve(READ, references("pruned-item"), BOUNDS, as_of=at)
    assert (old.status, old.cause) == ("unavailable", "tombstoned_since")
    assert [(row.graph_id, row.unavailable, row.content) for row in old.rows] == [
        ("pruned-item", "tombstoned_since", None),
    ]


async def test_statement_error_is_unavailable(scenario) -> None:
    await scenario.item("fixture-item")
    service = scenario.service()
    good = await service.retrieve(READ, references("fixture-item"), BOUNDS)
    assert good.status == "ok"

    def refuse_seed(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("WITH RECURSIVE"):
            raise sa.exc.OperationalError(statement, parameters, RuntimeError("fixture statement"))

    sa.event.listen(scenario.engine.sync_engine, "before_cursor_execute", refuse_seed)
    try:
        bad = await service.retrieve(READ, references("fixture-item"), BOUNDS)
    finally:
        sa.event.remove(scenario.engine.sync_engine, "before_cursor_execute", refuse_seed)
    assert (bad.status, bad.cause, bad.rows) == ("unavailable", "statement_failed", ())
