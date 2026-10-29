"""One entry contract for SQL and memory, with generated data only."""

import asyncio
from dataclasses import replace

import pytest

from nora.domain.entries import EntryContent, EntryStore
from nora.testing.entries import FakeEntryStore

CONTENT = EntryContent(
    (("topic", "generated-racing"),),
    "A recurring generated subject.",
    (("message", "generated-1"),),
    "medium",
    "active",
    "cognition",
    "generated-session",
    "generated-model",
    "a" * 64,
)


@pytest.fixture(scope="module")
def _entry_db(pg_admin):
    from uuid import uuid4

    import psycopg

    from nora.infra.sql.migrations_runner import run_migrations

    dbname = "t_entry_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeEntryStore()
        return
    import sqlalchemy as sa

    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.entries import SqlEntryStore

    engine = create_sql_engine(request.getfixturevalue("_entry_db"))
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
        await conn.execute(sa.text("TRUNCATE notebook_entry_revision, notebook_entry CASCADE"))
    try:
        yield SqlEntryStore(engine)
    finally:
        await engine.dispose()


async def test_topic_lifecycle_transitions(store: EntryStore) -> None:
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    for state in ("dormant", "active", "retired"):
        head = await store.append(
            unit="u",
            entry_id=entry.id,
            base_revision_id=entry.head.id,
            content=replace(CONTENT, state=state),
        )
        entry = await store.get(unit="u", entry_id=entry.id)
        assert entry and entry.head == head
    with pytest.raises(ValueError, match="lifecycle"):
        await store.append(unit="u", entry_id=entry.id, base_revision_id=head.id, content=CONTENT)
    assert [r.content.state for r in await store.revisions(unit="u", entry_id=entry.id)] == [
        "retired",
        "active",
        "dormant",
        "active",
    ]
    assert entry.audience == "internal"


@pytest.mark.parametrize("about", [(), (("person", "p"),), (("topic", "a"), ("topic", "b"))])
async def test_topic_about_exactly_one_topic(store: EntryStore, about) -> None:
    with pytest.raises(ValueError, match="topic_about"):
        await store.create(kind="topic", unit="u", content=replace(CONTENT, about=about))


async def test_topic_has_no_links(store: EntryStore) -> None:
    with pytest.raises(ValueError, match="topic_links"):
        await store.create(
            kind="topic", unit="u", content=replace(CONTENT, links=(("lesson", "l"),))
        )


@pytest.mark.parametrize("kind", ["topic", "profile_line", "lesson", "unknown", "person"])
async def test_topic_citations_record_rollup_only(store: EntryStore, kind: str) -> None:
    with pytest.raises(ValueError, match="citation_layer"):
        await store.create(
            kind="topic", unit="u", content=replace(CONTENT, citations=((kind, "x"),))
        )
    entry = await store.create(
        kind="topic", unit="u", content=replace(CONTENT, citations=(("episode", "generated"),))
    )
    assert entry.head.content.citations == (("episode", "generated"),)


async def test_entry_stale_base(store: EntryStore) -> None:
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    first = await store.append(
        unit="u",
        entry_id=entry.id,
        base_revision_id=entry.head.id,
        content=replace(CONTENT, body="First revision."),
    )
    with pytest.raises(ValueError, match="stale_base"):
        await store.append(
            unit="u",
            entry_id=entry.id,
            base_revision_id=entry.head.id,
            content=replace(CONTENT, body="Stale revision."),
        )
    held = await store.get(unit="u", entry_id=entry.id)
    assert held and held.head == first
    assert first.base_revision_id == entry.head.id
    assert len(await store.revisions(unit="u", entry_id=entry.id)) == 2


async def test_entry_concurrent_append_and_unit_boundary(store: EntryStore) -> None:
    entry = await store.create(kind="topic", unit="u", content=CONTENT)
    assert await store.get(unit="other", entry_id=entry.id) is None
    assert await store.revisions(unit="other", entry_id=entry.id) == ()
    results = await asyncio.gather(
        *(
            store.append(
                unit="u",
                entry_id=entry.id,
                base_revision_id=entry.head.id,
                content=replace(CONTENT, body=f"Generated {i}"),
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ValueError) and str(r) == "stale_base" for r in results) == 1
    assert len(await store.revisions(unit="u", entry_id=entry.id)) == 2
