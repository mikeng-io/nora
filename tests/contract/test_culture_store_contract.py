"""Shared append contract for the SQL and in-memory Culture stores.

Each key's new revision names its previous head as its parent. The database
is built once for the module and truncated between cases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import CultureRevision
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import CULTURE, CULTURE_BODY_CEILING_CHARS, CULTURE_REVISION
from nora.testing import InMemoryCultureStore
from nora.testing.culture_store import (
    CULTURE_BODY_CEILING_CHARS as FAKE_CEILING,
)

NOW = 1_790_000_000_000
KEY = "room-a"
OTHER = "room-b"


class _CultureStore(Protocol):
    async def append(
        self, *, key: str, body: str, written_at: int, established_by: str,
        instruction_sha256: str | None = None, model: str | None = None,
        citations: tuple[str, ...] = (),
    ) -> CultureRevision: ...


@pytest.fixture(scope="module")
def _culture_db(pg_module_url: str) -> str:
    return pg_module_url


@pytest.fixture(params=["memory", "sql"])
async def engine(request: Any) -> AsyncIterator[AsyncEngine | None]:
    if request.param == "memory":
        yield None
        return
    engine = create_sql_engine(request.getfixturevalue("_culture_db"))
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
def store(engine: AsyncEngine | None) -> _CultureStore:
    return SqlCultureStore(engine) if engine is not None else InMemoryCultureStore()


async def _observed_revision(
    engine: AsyncEngine | None, written: CultureRevision,
) -> sa.Row[Any] | CultureRevision:
    if engine is None:
        return written
    async with engine.connect() as conn:
        row = (await conn.execute(
            sa.select(CULTURE_REVISION.c.parent_revision_id, CULTURE_REVISION.c.citations)
            .where(CULTURE_REVISION.c.id == written.id)
        )).one()
    assert row.parent_revision_id == written.parent_revision_id
    assert tuple(row.citations) == written.citations
    return row


async def _append(store: _CultureStore, body: str, *, key: str = KEY,
                  at: int = NOW) -> CultureRevision:
    return await store.append(
        key=key, body=body, written_at=at, established_by="dream:culture:test")


def test_the_fake_and_the_schema_hold_the_same_ceiling() -> None:
    assert FAKE_CEILING == CULTURE_BODY_CEILING_CHARS


async def test_the_first_account_has_no_parent(store, engine):
    written = await _append(store, "they decide by attrition")
    assert (await _observed_revision(engine, written)).parent_revision_id is None


async def test_the_citations_a_revision_rests_on_are_kept(store, engine):
    cited = await store.append(
        key=KEY, body="they decide by attrition", written_at=NOW,
        established_by="dream:culture", citations=("4812", "response:4790"),
    )
    assert tuple((await _observed_revision(engine, cited)).citations) == ("4812", "response:4790")
    uncited = await _append(store, "uncited", at=NOW + 1)
    assert tuple((await _observed_revision(engine, uncited)).citations) == ()


async def test_a_second_account_names_the_first_as_its_parent(store, engine):
    """DEC-183: the old account becomes history, it is not overwritten."""
    first = await _append(store, "first account")
    second = await _append(store, "second account", at=NOW + 1)
    assert (await _observed_revision(engine, second)).parent_revision_id == first.id


async def test_keys_do_not_share_a_chain(store, engine):
    """Interleaved appends to two rooms: a store ordering by insertion rather
    than by parent link would splice one room's history into the other's."""
    first = await _append(store, "a1")
    other = await _append(store, "b1", key=OTHER, at=NOW + 1)
    second = await _append(store, "a2", at=NOW + 2)
    assert (await _observed_revision(engine, other)).parent_revision_id is None
    assert (await _observed_revision(engine, second)).parent_revision_id == first.id


async def test_a_blank_body_is_refused(store):
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs ValueError
        await _append(store, "   ")


async def test_a_body_over_the_ceiling_is_refused(store):
    with pytest.raises(Exception):  # noqa: B017 — IntegrityError vs ValueError
        await _append(store, "x" * (CULTURE_BODY_CEILING_CHARS + 1))


@pytest.mark.parametrize("attempt", [0, 1])
async def test_reused_store_starts_empty_after_independent_commit(store, attempt, request):
    """Each invocation writes the same key; the next must see the reset."""
    key = "generated-reset-probe"
    written = await _append(store, f"generated reset {attempt}", key=key)
    assert written.parent_revision_id is None
    if isinstance(store, SqlCultureStore):
        # Reopen through an independent engine, as restart callers do.
        engine = create_sql_engine(request.getfixturevalue("_culture_db"))
        try:
            async with engine.connect() as conn:
                head_id = (await conn.execute(
                    sa.select(CULTURE.c.head_revision_id).where(CULTURE.c.key == key)
                )).scalar_one()
            assert head_id == written.id
        finally:
            await engine.dispose()
