"""Shared contract for the culture store. Every implementation runs every test.

Two implementations: `nora.infra.sql.culture.SqlCultureStore` over real
Postgres in a testcontainer, and `nora.testing.InMemoryCultureStore`. The
suite exists because `CultureReader` became a declared port (DEC-207) — a port
never gains a second implementation, including a test fake, without a suite
both pass.

**The property it is really about is the chain** (DEC-183): an account is never
overwritten, the new one names the old as its parent, and `chain` walks those
links. A fake that kept one row per key would pass every `head` test and lose
the history the tier exists to keep.

The database is built ONCE for this module and truncated between tests, per
`test_standing_store_contract.py`'s house rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Protocol
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.culture import CultureRevision
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import CULTURE_BODY_CEILING_CHARS
from nora.testing import InMemoryCultureStore
from nora.testing.culture_store import (
    CULTURE_BODY_CEILING_CHARS as FAKE_CEILING,
)

NOW = 1_790_000_000_000
KEY = "room-a"
OTHER = "room-b"


class _CultureStore(Protocol):
    async def head(self, *, key: str) -> CultureRevision | None: ...
    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = None, model: str | None = None,
    ) -> CultureRevision: ...
    async def chain(
        self, *, key: str, limit: int = 10,
    ) -> tuple[CultureRevision, ...]: ...


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


@pytest.fixture(params=["memory", "sql"])
async def store(request: Any) -> AsyncIterator[_CultureStore]:
    if request.param == "memory":
        yield InMemoryCultureStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_culture_db"))
    try:
        yield SqlCultureStore(engine)
    finally:
        async with engine.begin() as conn:
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate culture, culture_revision cascade"))
        await engine.dispose()


async def _append(store: _CultureStore, body: str, *, key: str = KEY,
                  at: int = NOW) -> CultureRevision:
    return await store.append(
        key=key, body=body, written_at=at, established_by="dream:culture:test")


def test_the_fake_and_the_schema_hold_the_same_ceiling() -> None:
    assert FAKE_CEILING == CULTURE_BODY_CEILING_CHARS


async def test_a_room_never_folded_has_no_account(store):
    """`None` is a first-class state — the ORDINARY one for a while after the
    tier is enabled — never an error."""
    assert await store.head(key=KEY) is None
    assert await store.chain(key=KEY) == ()


async def test_an_appended_account_is_the_head(store):
    written = await _append(store, "they decide by attrition")
    head = await store.head(key=KEY)
    assert head == written
    assert head.parent_revision_id is None


async def test_a_second_account_names_the_first_as_its_parent(store):
    """DEC-183: the old account becomes history, it is not overwritten."""
    first = await _append(store, "first account")
    second = await _append(store, "second account", at=NOW + 1)
    assert (await store.head(key=KEY)) == second
    assert second.parent_revision_id == first.id
    assert [r.body for r in await store.chain(key=KEY)] == [
        "second account", "first account"]


async def test_keys_do_not_share_a_chain(store):
    """Interleaved appends to two rooms: a store ordering by insertion rather
    than by parent link would splice one room's history into the other's."""
    await _append(store, "a1")
    await _append(store, "b1", key=OTHER, at=NOW + 1)
    await _append(store, "a2", at=NOW + 2)
    assert [r.body for r in await store.chain(key=KEY)] == ["a2", "a1"]
    assert [r.body for r in await store.chain(key=OTHER)] == ["b1"]


async def test_chain_is_bounded_by_limit(store):
    for i in range(4):
        await _append(store, f"account {i}", at=NOW + i)
    assert len(await store.chain(key=KEY, limit=2)) == 2


async def test_a_blank_body_is_refused(store):
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs ValueError
        await _append(store, "   ")


async def test_a_body_over_the_ceiling_is_refused(store):
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs ValueError
        await _append(store, "x" * (CULTURE_BODY_CEILING_CHARS + 1))
