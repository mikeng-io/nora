"""A forget request or dispute changes scoped graph visibility when committed."""

# ruff: noqa: F811 - pytest resolves the imported engine fixture.

from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.domain.fetched_page import FetchedPage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, FORGET_REQUEST, NORA_ACT, REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph, visible_to_reads
from nora.infra.sql.fetched_pages import SqlFetchedPageStore
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.schema import BRIDGE_ACTION, FETCHED_PAGES, MESSAGE_EDIT, MESSAGES
from nora.memory.wire import memory_intake
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_item_registration import (
    _AT,
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    _Writer,
    engine,  # noqa: F401
)

_OTHER = "generated-other"


async def _claim(graph, claim_id, *, unit=_UNIT, identities=(_PERSON, _OTHER), **revision):
    row = {
        "id": claim_id, "kind": "claim", "unit": unit, "basis": "inferred",
        "reasoning": "induced", "confidence": "low", "part": "fact", "content": {"text": "fixture"},
        "state": "live", "writer": "generated", "exposure": "internal",
        "identities": list(identities),
        **revision,
    }
    if revision:
        await graph.revise(CLAIM, row)
    else:
        await graph.create(CLAIM, row)


async def _visible(engine, table):
    identity = table.c.graph_id if "graph_id" in table.c else table.c.id
    async with engine.connect() as conn:
        return set(await conn.scalars(sa.select(identity).where(visible_to_reads(table))))


async def _messages(engine):
    registration = _registration()
    writer = _Writer(engine, registration)
    store = SqlRecordStore(engine, registration=registration)
    message = writer._message()
    for offset in range(2):
        await store.append(
            message=replace(message, timestamp=message.timestamp + offset), person_id=_PERSON,
        )
    return list((await _rows(engine, MESSAGES)).values())


async def test_any_subjects_dispute_hides_the_claim_and_its_revisions(engine):
    graph = SqlCognitiveGraph(engine)
    await _claim(graph, "generated-private")
    await _claim(graph, "generated-readable")
    intake = memory_intake(engine, _registration())
    assert await _visible(engine, CLAIM) == {"generated-private", "generated-readable"}
    dispute = await intake.dispute(unit=_UNIT, author=_OTHER, claim_id="generated-private", at=_AT)
    assert (await _rows(engine, CLAIM))["generated-private"]["state"] == "hidden"
    assert await _visible(engine, CLAIM) == {"generated-readable"}
    assert "generated-private" not in await _visible(engine, REGISTRY)
    assert any(s.graph_id == dispute and s.kind == "new_item"
               for s in await SqlGraphStimuli(engine).read(_UNIT))
    await _claim(graph, "generated-revision", replaces="generated-private")
    assert await _visible(engine, CLAIM) == {"generated-readable"}


async def test_another_persons_dispute_leaves_the_claim_readable_for_weighing(engine):
    await _claim(SqlCognitiveGraph(engine), "generated-claim", identities=(_PERSON,))
    intake = memory_intake(engine, _registration())
    dispute = await intake.dispute(unit=_UNIT, author=_OTHER, claim_id="generated-claim", at=_AT)
    assert (await _rows(engine, CLAIM))["generated-claim"]["state"] == "awaiting_weighing"
    assert await _visible(engine, CLAIM) == {"generated-claim"}
    stimuli = await SqlGraphStimuli(engine).read(_UNIT)
    assert [(s.kind, s.graph_id) for s in stimuli] == [("new_item", dispute)]


async def test_dispute_reports_all_live_reverse_claim_dependencies(engine):
    graph = SqlCognitiveGraph(engine)
    for row_id in ("generated-base", "generated-draws", "generated-rests", "generated-unrelated"):
        await _claim(graph, row_id)
    for kind, source, destination in (
        ("draws_on", "generated-draws", "generated-base"),
        ("rests_on", "generated-rests", "generated-base"),
        ("draws_on", "generated-unrelated", "generated-rests"),
    ):
        await graph.create(EDGE, {
            "id": f"{source}-edge", "unit": _UNIT, "kind": "edge", "basis": "computed",
            "state": "live", "writer": "generated", "exposure": "internal", "type": kind,
            "src_id": source, "dst_id": destination,
        })
    dispute = await memory_intake(engine, _registration()).dispute(
        unit=_UNIT, author=_PERSON, claim_id="generated-base", at=_AT,
    )
    stimuli = await SqlGraphStimuli(engine).read(_UNIT)
    (dependency,) = [s for s in stimuli if s.kind == "dependency"]
    assert dependency.graph_id == "generated-base"
    assert dependency.revision_id == dispute
    assert dependency.dependents == ("generated-draws", "generated-rests")


async def test_forget_request_hides_every_named_row_without_deleting(engine):
    rows = await _messages(engine)
    item_ids = {row["graph_id"] for row in rows}
    await _claim(SqlCognitiveGraph(engine), "generated-claim")
    result = await memory_intake(engine, _registration()).forget(
        unit=_UNIT, author=_PERSON, targets=(*item_ids, "generated-claim"), at=_AT,
    )
    assert not result.tombstoned and not result.blocked
    assert len(await _rows(engine, MESSAGES)) == 2
    assert (await _rows(engine, CLAIM))["generated-claim"]["state"] == "hidden"
    assert not await _visible(engine, MESSAGES)
    assert not await _visible(engine, CLAIM)
    assert result.graph_id in await _visible(engine, FORGET_REQUEST)


async def test_delete_now_tombstones_a_page_and_refetch_remains_hidden(engine):
    pages = SqlFetchedPageStore(engine, registration=_registration())
    initial = FetchedPage(_ROOM, "https://example.invalid/path", "generated-key",
                          _AT, _AT + timedelta(days=1), {})
    await pages.put(page=initial)
    (graph_id,) = await _visible(engine, FETCHED_PAGES)
    result = await memory_intake(engine, _registration()).forget(
        unit=_UNIT, author=_PERSON, targets=[graph_id], at=_AT, delete_now=True,
    )
    assert result.tombstoned == (graph_id,)
    assert not result.blocked
    assert await pages.get(conversation_id=_ROOM, url=initial.url, now=_AT) is None
    assert graph_id not in await _visible(engine, REGISTRY)
    await pages.put(page=initial)
    assert await pages.get(conversation_id=_ROOM, url=initial.url, now=_AT)
    assert not await _visible(engine, FETCHED_PAGES)


async def test_delete_now_keeps_blocked_targets_hidden_and_tombstones_the_others(engine):
    blocked, removable = await _messages(engine)
    registration = _registration()
    writer = _Writer(engine, registration)
    await SqlRecordStore(engine, registration=registration).append_edit(
        message_id=blocked["id"], message=writer._message(),
    )
    (edit,) = (await _rows(engine, MESSAGE_EDIT)).values()
    async with engine.begin() as conn:
        await conn.execute(sa.insert(BRIDGE_ACTION).values(
            action_id="generated-blocker", platform="signal",
            account_key="generated-account", owner_kind="live_invocation", owner_id="fixture",
            op="send", request={}, request_hash="fixture", message_id=blocked["id"],
        ))
    result = await memory_intake(engine, _registration()).forget(
        unit=_UNIT, author=_PERSON, targets=[blocked["graph_id"], removable["graph_id"]],
        at=_AT, delete_now=True,
    )
    assert result.tombstoned == (removable["graph_id"],)
    assert [(entry.graph_id, entry.table) for entry in result.blocked] == [
        (blocked["graph_id"], "bridge_action"),
    ]
    assert set(await _rows(engine, MESSAGES)) == {blocked["id"]}
    registry = await _rows(engine, REGISTRY)
    assert registry[blocked["graph_id"]]["state"] == "live"
    assert registry[edit["graph_id"]]["state"] == "live"
    assert (await _rows(engine, MESSAGE_EDIT))[edit["id"]]["message_id"] == blocked["id"]
    assert registry[removable["graph_id"]]["state"] == "tombstoned"
    assert not await _visible(engine, MESSAGES)
    (request,) = (await _rows(engine, FORGET_REQUEST)).values()
    assert request["blocked"] == [
        {"graph_id": blocked["graph_id"], "table": "bridge_action", "reason": "foreign_key"},
    ]



async def test_delete_now_hides_protected_act_and_records_the_source_as_blocked(engine):
    registration = _registration()
    await _Writer(engine, registration).write("nora_act")
    (act,) = (await _rows(engine, NORA_ACT)).values()
    graph_id = act["graph_id"]
    assert await _visible(engine, NORA_ACT) == {graph_id}

    result = await memory_intake(engine, registration).forget(
        unit=_UNIT, author=_PERSON, targets=[graph_id], at=_AT, delete_now=True,
    )
    assert not result.tombstoned
    (blocked,) = result.blocked
    assert (blocked.graph_id, blocked.table, blocked.reason) == (
        graph_id, "nora_act", "protected_source",
    )
    assert (await _rows(engine, NORA_ACT))[act["id"]] == act
    assert (await _rows(engine, REGISTRY))[graph_id]["state"] == "live"
    assert not await _visible(engine, NORA_ACT)
    (request,) = (await _rows(engine, FORGET_REQUEST)).values()
    assert request["blocked"] == [
        {"graph_id": graph_id, "table": "nora_act", "reason": "protected_source"},
    ]


async def test_tombstone_writer_refuses_to_attempt_a_protected_source(engine):
    await _Writer(engine, _registration()).write("nora_act")
    async with engine.begin() as conn:
        with pytest.raises(ValueError, match="protected item source"):
            await tombstone_items(conn, NORA_ACT)
        assert await conn.scalar(sa.select(sa.func.count()).select_from(NORA_ACT)) == 1
