"""Culture appends keep the head, parent links and revision rows consistent."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import CULTURE, CULTURE_REVISION

T0 = 1_757_000_000_000
KEY = "discord/g1"


@pytest.fixture(scope="module")
def _culture_db(pg_module_url: str) -> str:
    return pg_module_url


@pytest.fixture
async def engine(_culture_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_sql_engine(_culture_db)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate culture, culture_revision cascade"))
        await engine.dispose()


@pytest.fixture
def store(engine: AsyncEngine) -> SqlCultureStore:
    return SqlCultureStore(engine)


async def _head_id(engine: AsyncEngine) -> str:
    async with engine.connect() as conn:
        return (await conn.execute(
            sa.select(CULTURE.c.head_revision_id).where(CULTURE.c.key == KEY)
        )).scalar_one()


async def _parent_id(engine: AsyncEngine, revision_id: str) -> str | None:
    async with engine.connect() as conn:
        return (await conn.execute(
            sa.select(CULTURE_REVISION.c.parent_revision_id)
            .where(CULTURE_REVISION.c.id == revision_id)
        )).scalar_one()


async def _append(store: SqlCultureStore, body: str, at: int) -> Any:
    return await store.append(
        key=KEY, body=body, written_at=at, established_by="culture",
        instruction_sha256="a" * 64, model="flash",
    )


# --- the first account -----------------------------------------------------


async def test_the_first_revision_has_no_parent_and_becomes_the_head(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    r = await _append(store, "they talk in shorthand", T0)
    assert await _parent_id(engine, r.id) is None
    assert await _head_id(engine) == r.id


async def test_the_runtimes_provenance_round_trips(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    """Provenance is the runtime's and this store must not invent or drop it —
    a revision that cannot say which instruction and model produced it is not
    re-derivable, which is the property the whole chain rests on."""
    head = await _append(store, "an account", T0)
    async with engine.connect() as conn:
        revision = CULTURE_REVISION.c
        saved = (await conn.execute(
            sa.select(revision.instruction_sha256, revision.model, revision.established_by)
            .where(revision.id == head.id)
        )).one()
    assert tuple(saved) == ("a" * 64, "flash", "culture")


# --- the chain -------------------------------------------------------------


async def test_the_second_revision_names_the_first_as_parent(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    """«The same thing, earlier» — the caller never supplies this, because at
    the time it called it did not know what the head was."""
    first = await _append(store, "first", T0)
    second = await _append(store, "second", T0 + 86_400_000)
    assert await _parent_id(engine, second.id) == first.id
    assert await _head_id(engine) == second.id


async def test_append_uses_the_current_head_even_with_an_earlier_timestamp(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    """Two revisions can share a millisecond, and a row can be written with an
    older clock. A timestamp sort would reorder those into a history that never
    happened; the parent links cannot."""
    a = await _append(store, "a", T0 + 500)
    b = await _append(store, "b", T0)  # EARLIER clock, later write
    assert await _parent_id(engine, b.id) == a.id


# --- nothing retires, and rooms do not leak --------------------------------


async def test_a_revision_never_disappears_when_the_head_moves(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    """An append retains the previous account in the revision history."""
    first = await _append(store, "the old name", T0)
    await _append(store, "the new name", T0 + 1)
    async with engine.connect() as conn:
        body = (await conn.execute(
            sa.select(CULTURE_REVISION.c.body).where(CULTURE_REVISION.c.id == first.id)
        )).scalar_one()
    assert body == "the old name"


# --- concurrency: the property the transaction exists for ------------------


async def test_concurrent_appends_to_one_key_form_a_LINE_not_a_fork(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    base = await _append(store, "base", T0)
    results = await asyncio.gather(
        _append(store, "x", T0 + 1),
        _append(store, "y", T0 + 2),
    )
    head_id = await _head_id(engine)
    head = next(r for r in results if r.id == head_id)
    middle = next(r for r in results if r.id != head_id)
    assert await _parent_id(engine, head.id) == middle.id
    assert await _parent_id(engine, middle.id) == base.id


async def test_append_locks_the_existing_head_before_inserting_its_child(
    store: SqlCultureStore, engine: AsyncEngine,
) -> None:
    head = await _append(store, "base", T0)
    observed = []

    async def competing_head_is_lockable() -> bool:
        try:
            async with engine.begin() as conn:
                return (await conn.execute(
                    sa.select(CULTURE.c.key).where(CULTURE.c.key == KEY)
                    .with_for_update(nowait=True)
                )).scalar_one() == KEY
        except sa.exc.DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "55P03":
                raise
            return False

    def before_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO culture_revision "):
            observed.append(conn.connection.dbapi_connection.run_async(
                lambda _: competing_head_is_lockable(),
            ))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", before_insert)
    try:
        child = await _append(store, "child", T0 + 1)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", before_insert)

    assert observed == [False]
    assert await competing_head_is_lockable()
    assert await _parent_id(engine, child.id) == head.id


# --- the database refuses what the code must not write ---------------------


async def test_an_empty_body_is_refused_by_the_database(
    store: SqlCultureStore,
) -> None:
    """Not merely by a caller: a culture row claiming an account and holding
    nothing would render an empty block that says «this is how the room works»."""
    with pytest.raises(Exception, match="culture_revision_has_a_body_check"):
        await _append(store, "   ", T0)
