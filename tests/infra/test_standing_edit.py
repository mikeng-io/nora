"""`scripts/standing_edit.py` — the operator's one other writer of `standing`
(design/dreamer-agent-loop/26-standing.md, dated section 2026-09-25).

Against real Postgres, not a fake: the property under test IS the
compare-and-set the database enforces (`SqlStandingStore.write_if_unchanged`),
so a fake standing in for it would test this module's plumbing and not the
guarantee it exists for — the same argument `tests/infra/test_standing_
revision.py`'s module docstring makes for `read_as_of`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.standing import STANDING_BODY_CEILING_CHARS
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.standing import SqlStandingStore
from scripts.standing_edit import EditRefusedError, edit_standing

PLATFORM = "signal"
CONV = "g1"
T0 = 1_758_000_000_000


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_standing_edit_" + uuid4().hex
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
async def store(_db: str) -> AsyncIterator[SqlStandingStore]:
    engine = create_sql_engine(_db)
    try:
        yield SqlStandingStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("truncate standing, standing_revision cascade"))
        await engine.dispose()


async def test_the_edit_lands_as_operator_and_keeps_fold_count_and_watermark(
    store: SqlStandingStore,
) -> None:
    from nora.domain.standing import Standing

    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="the fold's position",
        fold_count=3, updated_at=T0, last_folded_row_id=42,
    ))

    before, after = await edit_standing(
        platform=PLATFORM, conversation_id=CONV,
        new_body="the operator's correction", store=store, now_ms=T0 + 1000,
    )
    assert before.body == "the fold's position"
    assert after.body == "the operator's correction"
    assert after.fold_count == 3
    assert after.last_folded_row_id == 42

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "the operator's correction"
    assert head.fold_count == 3
    assert head.last_folded_row_id == 42


async def test_editing_a_room_with_no_standing_row_is_refused(
    store: SqlStandingStore,
) -> None:
    with pytest.raises(EditRefusedError, match="nothing to edit"):
        await edit_standing(
            platform=PLATFORM, conversation_id="no-such-room",
            new_body="anything", store=store, now_ms=T0,
        )


async def test_a_fold_landing_between_the_read_and_the_write_refuses_the_edit(
    store: SqlStandingStore,
) -> None:
    """The race `write_if_unchanged` exists to close. `edit_standing` cannot
    be handed a stale read directly (it does its own read), so the fold is
    made to land INSIDE the function's own window by monkeypatching `read` to
    write behind its own back before returning — the only way to exercise
    this from the public entry point rather than the store method alone,
    which `tests/infra/test_standing_revision.py` already covers directly."""
    from nora.domain.standing import Standing

    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="position one",
        fold_count=1, updated_at=T0,
    ))

    real_read = store.read

    async def _read_then_a_fold_lands(*, platform: str, conversation_id: str):
        row = await real_read(platform=platform, conversation_id=conversation_id)
        await store.write(standing=Standing(
            platform=PLATFORM, conversation_id=CONV,
            body="position two, folded mid-edit", fold_count=2,
            updated_at=T0 + 1000,
        ))
        return row

    store.read = _read_then_a_fold_lands  # type: ignore[method-assign]
    try:
        with pytest.raises(EditRefusedError, match="changed since it was read"):
            await edit_standing(
                platform=PLATFORM, conversation_id=CONV,
                new_body="the operator's edit", store=store, now_ms=T0 + 2000,
            )
    finally:
        store.read = real_read  # type: ignore[method-assign]

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "position two, folded mid-edit"


async def test_an_over_ceiling_body_is_refused_and_writes_nothing(
    store: SqlStandingStore,
) -> None:
    from nora.domain.standing import Standing

    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="a fine position",
        fold_count=1, updated_at=T0,
    ))
    with pytest.raises(EditRefusedError):
        await edit_standing(
            platform=PLATFORM, conversation_id=CONV,
            new_body="x" * (STANDING_BODY_CEILING_CHARS + 1),
            store=store, now_ms=T0 + 1000,
        )
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None and head.body == "a fine position"
