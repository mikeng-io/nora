"""`SqlCultureStore` — the chain is the tier.

Single-store file, not a contract suite (there is one implementation). The
database is MODULE-scoped per `test_tag_trace_store.py`'s pattern: one
`CREATE DATABASE` plus one migration chain for the whole file, truncated between
tests, because rebuilding the world per test degrades the shared testcontainer
and the cost lands on an unrelated wall-clock test elsewhere.

Written around the two properties that make a chain a chain rather than a pile
of rows: the head and its revision move TOGETHER, and a revision's parent is
whatever the head was at the moment it was written.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations

T0 = 1_757_000_000_000
KEY = "discord/g1"


@pytest.fixture(scope="module")
def _culture_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_culture_" + uuid4().hex
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
        with psycopg.connect(
            pg_admin.dsn(pg_admin.maintenance_db), autocommit=True
        ) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def store(_culture_db: str) -> AsyncIterator[SqlCultureStore]:
    engine = create_sql_engine(_culture_db)
    try:
        yield SqlCultureStore(engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate culture, culture_revision cascade"))
        await engine.dispose()


async def _append(store: SqlCultureStore, body: str, at: int) -> Any:
    return await store.append(
        key=KEY, body=body, written_at=at, established_by="culture",
        instruction_sha256="a" * 64, model="flash",
    )


# --- the first account -----------------------------------------------------


async def test_a_room_with_no_fold_yet_has_no_head(store: SqlCultureStore) -> None:
    """`None` and not an empty revision: a room nobody has folded is a
    different state from a room whose account is empty, and only one of them
    is reachable."""
    assert await store.head(key=KEY) is None


async def test_the_first_revision_has_no_parent_and_becomes_the_head(
    store: SqlCultureStore,
) -> None:
    r = await _append(store, "they talk in shorthand", T0)
    assert r.parent_revision_id is None
    head = await store.head(key=KEY)
    assert head is not None and head.id == r.id and head.body == "they talk in shorthand"


async def test_the_runtimes_provenance_round_trips(store: SqlCultureStore) -> None:
    """Provenance is the runtime's and this store must not invent or drop it —
    a revision that cannot say which instruction and model produced it is not
    re-derivable, which is the property the whole chain rests on."""
    head = await _append(store, "an account", T0)
    assert head.instruction_sha256 == "a" * 64 and head.model == "flash"
    assert head.established_by == "culture"


# --- the chain -------------------------------------------------------------


async def test_the_second_revision_names_the_first_as_parent(
    store: SqlCultureStore,
) -> None:
    """«The same thing, earlier» — the caller never supplies this, because at
    the time it called it did not know what the head was."""
    first = await _append(store, "first", T0)
    second = await _append(store, "second", T0 + 86_400_000)
    assert second.parent_revision_id == first.id
    head = await store.head(key=KEY)
    assert head is not None and head.id == second.id


async def test_chain_walks_PARENT_LINKS_newest_first(store: SqlCultureStore) -> None:
    a = await _append(store, "a", T0)
    b = await _append(store, "b", T0 + 1)
    c = await _append(store, "c", T0 + 2)
    assert [r.id for r in await store.chain(key=KEY)] == [c.id, b.id, a.id]


async def test_chain_ignores_written_at_and_follows_the_links(
    store: SqlCultureStore,
) -> None:
    """Two revisions can share a millisecond, and a row can be written with an
    older clock. A timestamp sort would reorder those into a history that never
    happened; the parent links cannot."""
    a = await _append(store, "a", T0 + 500)
    b = await _append(store, "b", T0)  # EARLIER clock, later write
    assert [r.id for r in await store.chain(key=KEY)] == [b.id, a.id]


async def test_chain_respects_its_limit(store: SqlCultureStore) -> None:
    for i in range(5):
        await _append(store, f"r{i}", T0 + i)
    assert len(await store.chain(key=KEY, limit=2)) == 2


async def test_chain_of_a_room_with_nothing_is_empty(store: SqlCultureStore) -> None:
    assert await store.chain(key="discord/never") == ()


# --- nothing retires, and rooms do not leak --------------------------------


async def test_a_revision_never_disappears_when_the_head_moves(
    store: SqlCultureStore,
) -> None:
    """DEC-183. The old account does not become false, it becomes history —
    so an append is an APPEND, never an update in place."""
    first = await _append(store, "the old name", T0)
    await _append(store, "the new name", T0 + 1)
    chain = await store.chain(key=KEY)
    assert first.id in {r.id for r in chain}
    assert "the old name" in {r.body for r in chain}


async def test_two_rooms_do_not_share_a_chain(store: SqlCultureStore) -> None:
    await _append(store, "room one", T0)
    await store.append(
        key="signal/g9", body="room two", written_at=T0, established_by="culture",
    )
    assert [r.body for r in await store.chain(key=KEY)] == ["room one"]
    assert [r.body for r in await store.chain(key="signal/g9")] == ["room two"]


# --- concurrency: the property the transaction exists for ------------------


async def test_concurrent_appends_to_one_key_form_a_LINE_not_a_fork(
    store: SqlCultureStore,
) -> None:
    """`dream_loop` dispatches scopes as concurrent tasks. Without the
    `FOR UPDATE`, two folds of one key read the same parent and the chain
    FORKS — two revisions claiming one predecessor, one of them silently not in
    the history any more.

    The assertion is on the shape, not on which won: either order is correct,
    a fork is not.
    """
    await _append(store, "base", T0)
    await asyncio.gather(
        _append(store, "x", T0 + 1),
        _append(store, "y", T0 + 2),
    )
    chain = await store.chain(key=KEY)
    assert len(chain) == 3, "a fork drops a revision out of the walk"
    parents = [r.parent_revision_id for r in chain]
    assert len(set(parents)) == 3, f"two revisions share a parent: {parents}"


# --- the database refuses what the code must not write ---------------------


async def test_an_empty_body_is_refused_by_the_database(
    store: SqlCultureStore,
) -> None:
    """Not merely by a caller: a culture row claiming an account and holding
    nothing would render an empty block that says «this is how the room works»."""
    with pytest.raises(Exception, match="culture_revision_has_a_body_check"):
        await _append(store, "   ", T0)
