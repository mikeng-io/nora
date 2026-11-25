"""The Cognition loop eval's state diff sees the topics Nora keeps."""

from dataclasses import replace

import sqlalchemy as sa

from nora.domain.entries import EntryContent
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.entries import SqlEntryStore
from nora.infra.sql.schema import MEMORY_NODE
from scripts.cognition_loop.state import diff, snapshot

CONTENT = EntryContent(
    (("topic", "generated-racing"),),
    "A recurring generated subject.",
    (("media", "generated-1"),),
    "medium",
    "active",
    "cognition",
    "generated-session",
    "generated-model",
    "a" * 64,
)


async def test_topic_entries_are_created_revised_and_retired_in_the_diff(pg_module_url):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(MEMORY_NODE).values(
            kind="media", source_id="generated-1", unit="u",
            origin_room="signal:room-a"))
    store = SqlEntryStore(engine)

    async def state():
        return await snapshot(engine, unit="u", rooms=(), profile_keys=(), now_ms=1)

    empty = await state()
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    created = await state()
    key = f"entry:{entry.id}"
    assert [e.entry_id for e in diff(empty, created).kinds["topic"].created] == [key]

    second = await store.append(
        unit="u", entry_id=entry.id, base_revision_id=entry.head.id,
        content=replace(CONTENT, body="It came back, changed."))
    revised = await state()
    assert [e.entry_id for e in diff(created, revised).kinds["topic"].revised] == [key]

    await store.append(
        unit="u", entry_id=entry.id, base_revision_id=second.id,
        content=replace(CONTENT, state="retired"))
    gone = await state()
    assert [e.entry_id for e in diff(revised, gone).kinds["topic"].retired] == [key]
    await engine.dispose()


def _entity(row_id: str, name: str, aliases: list[str]) -> dict:
    return {
        "id": row_id, "unit": "u", "kind": "entity", "basis": "inferred",
        "reasoning": "deduced", "confidence": "high", "state": "admitted",
        "exposure": "internal", "writer": "generated", "name": name,
        "aliases": aliases,
    }


async def test_cognitive_graph_edges_and_entity_anchors_are_in_the_diff(pg_module_url):
    """The graph half of the state snapshot reads `edge` rows and admitted
    entity names/aliases — the Cognitive Graph's answers to the legacy
    relation and alias questions."""
    engine = create_sql_engine(pg_module_url)
    writer = SqlCognitiveGraph(engine)

    async def state():
        return await snapshot(engine, unit="u", rooms=(), profile_keys=(), now_ms=1)

    empty = await state()
    for row_id, name, aliases in (
        ("entity-a", "Generated Alpha", ["Alpha Generated"]),
        ("entity-b", "Generated Topic", []),
    ):
        await writer.create(ENTITY, _entity(row_id, name, aliases))
    with_anchor = await state()
    assert {e.entry_id for e in diff(empty, with_anchor).kinds["topic"].created} == {
        "anchor:generated alpha", "anchor:alpha generated", "anchor:generated topic",
    }
    assert with_anchor.entries("topic")["anchor:alpha generated"] == {
        "kind": "entity", "id": "entity-a",
    }

    await writer.create(EDGE, {
        "id": "edge-about", "unit": "u", "kind": "edge", "basis": "inferred",
        "reasoning": "deduced", "confidence": "medium", "state": "live",
        "exposure": "internal", "writer": "generated", "type": "about",
        "src_id": "entity-a", "dst_id": "entity-b",
    })
    foreign = _entity("entity-foreign", "Generated Foreign", ["Generated Alpha"])
    foreign["unit"] = "foreign-unit"
    await writer.create(ENTITY, foreign)
    with_edge = await state()
    created = diff(with_anchor, with_edge).kinds["graph"].created
    assert [e.entry_id for e in created] == ["about:edge-about"]
    assert created[0].after["src_id"] == "entity-a"
    assert created[0].after["type"] == "about"
    assert with_edge.entries("topic")["anchor:generated alpha"]["id"] == "entity-a"
    assert all(edge["unit"] == "u" for edge in with_edge.entries("graph").values())
    await engine.dispose()
