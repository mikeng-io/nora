"""The Cognition loop eval's state diff sees the topics Nora keeps."""

from dataclasses import replace

import sqlalchemy as sa

from nora.domain.entries import EntryContent
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
