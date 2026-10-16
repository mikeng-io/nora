"""Shared contract for the memo store (DEC-306). Every implementation runs every test.

Two implementations: `nora.infra.sql.memo.SqlMemoStore` over real Postgres in a
testcontainer, and `nora.testing.memo_store.InMemoryMemoStore`. The property it
is about is the chain: a memo is never overwritten, a revision lands only on the
head it was judged against, a withdrawal is one more revision, and a read never
crosses a unit. The database is built ONCE for this module and truncated between
tests (`test_standing_store_contract.py`'s house rule).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.memo import MemoStore, MemoWithdrawnError, StaleMemoError
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import MEMO_BODY_CEILING_CHARS, MEMO_TITLE_CEILING_CHARS
from nora.testing import memo_store as fake

NOW = 1_790_000_000_000
BODY = "## Kept\n- the wifi is on the fridge [101]"


@pytest.fixture(scope="module")
def _memo_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memo_" + uuid4().hex
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
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[MemoStore]:
    if request.param == "memory":
        yield fake.InMemoryMemoStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_memo_db"))
    try:
        yield SqlMemoStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate memo, memo_revision cascade"))
        await engine.dispose()


async def _create(store: MemoStore, *, unit: str = "u", title: str = "wifi") -> Any:
    return await store.create(
        unit=unit, platform="signal", conversation_id="room-a", title=title, body=BODY,
        citations=("101",), mode="told", established_by="cognition:u", model="m",
        now_ms=NOW,
    )


def test_the_fake_and_the_schema_hold_the_same_ceilings() -> None:
    assert fake.MEMO_TITLE_CEILING_CHARS == MEMO_TITLE_CEILING_CHARS
    assert fake.MEMO_BODY_CEILING_CHARS == MEMO_BODY_CEILING_CHARS


async def test_a_created_memo_is_kept_with_its_first_revision(store):
    memo = await _create(store)
    assert memo.is_kept and memo.head.seq == 1 and memo.title == "wifi"
    assert memo.head.citations == ("101",) and memo.head.mode == "told"
    assert (memo.platform, memo.conversation_id) == ("signal", "room-a")
    assert [m.id for m in await store.kept(unit="u")] == [memo.id]
    assert await store.get(unit="u", memo_id=memo.id) == memo


async def test_a_revision_lands_on_its_head_and_a_stale_base_is_refused(store):
    memo = await _create(store)
    second = await store.append(
        unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="wifi",
        body=BODY + "\n## Conditions\n- weekdays [101]", citations=("101",),
        mode="told", state="kept", established_by="cognition:u", model=None, now_ms=NOW + 1,
    )
    assert second.seq == 2
    with pytest.raises(StaleMemoError):
        await store.append(
            unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="wifi",
            body=BODY, citations=("101",), mode="told", state="kept",
            established_by="cognition:u", model=None, now_ms=NOW + 2,
        )
    assert [r.seq for r in await store.revisions(unit="u", memo_id=memo.id)] == [2, 1]
    assert (await store.get(unit="u", memo_id=memo.id)).head == second


async def test_a_withdrawn_memo_leaves_the_kept_list_and_keeps_its_chain(store):
    memo = await _create(store)
    await store.append(
        unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="wifi",
        body=BODY, citations=("102",), mode="told", state="withdrawn",
        established_by="cognition:u", model=None, now_ms=NOW + 1,
    )
    assert await store.kept(unit="u") == ()
    got = await store.get(unit="u", memo_id=memo.id)
    assert got is not None and not got.is_kept
    assert len(await store.revisions(unit="u", memo_id=memo.id)) == 2


async def test_another_units_memo_is_never_found(store):
    memo = await _create(store, unit="other")
    assert await store.kept(unit="u") == ()
    assert await store.get(unit="u", memo_id=memo.id) is None
    assert await store.revisions(unit="u", memo_id=memo.id) == ()
    with pytest.raises(LookupError):
        await store.append(
            unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="x",
            body=BODY, citations=("1",), mode="told", state="kept",
            established_by="cognition:u", model=None, now_ms=NOW,
        )


async def test_an_id_that_is_not_a_memo_is_not_found(store):
    assert await store.get(unit="u", memo_id="not-a-uuid") is None
    assert await store.get(unit="u", memo_id=str(uuid4())) is None


async def test_a_revision_citing_nothing_is_refused(store):
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError or ValueError
        await store.create(
            unit="u", platform="signal", conversation_id="room-a", title="t", body=BODY,
            citations=(), mode="told", established_by="cognition:u", model=None, now_ms=NOW,
        )


async def test_a_withdrawn_head_is_final_and_nothing_appends_onto_it(store):
    """Withdrawn is final, so neither a revision nor a second
    withdrawal lands on it, even against the current head."""
    memo = await _create(store)
    gone = await store.append(
        unit="u", memo_id=memo.id, base_revision_id=memo.head.id, title="wifi", body=BODY,
        citations=("102",), mode="told", state="withdrawn", established_by="cognition:u",
        model=None, now_ms=NOW + 1,
    )
    for state in ("kept", "withdrawn"):
        with pytest.raises(MemoWithdrawnError):
            await store.append(
                unit="u", memo_id=memo.id, base_revision_id=gone.id, title="wifi",
                body=BODY, citations=("103",), mode="told", state=state,
                established_by="cognition:u", model=None, now_ms=NOW + 2,
            )
    assert len(await store.revisions(unit="u", memo_id=memo.id)) == 2
