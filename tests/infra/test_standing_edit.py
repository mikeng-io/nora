"""`scripts/standing_edit.py` — the operator's one other writer of `standing`.

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

from nora.domain.standing import (
    DEFAULT_STANDING_MAX_CHARS,
    STANDING_BODY_CEILING_CHARS,
    Standing,
    WorkingSetItem,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.standing import SqlStandingStore
from scripts.standing_edit import (
    EditRefusedError,
    apply_operator_edit,
    preview_operator_edit,
    render_diff,
)

PLATFORM = "signal"
CONV = "g1"
T0 = 1_758_000_000_000
MAX_CHARS = DEFAULT_STANDING_MAX_CHARS


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
            # A reset, not a write: TRUNCATE is refused unless capture is off
            # (migration 0068, nora.infra.sql.history.CAPTURE_SETTING).
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("truncate standing, standing_revision cascade"))
        await engine.dispose()


# --- preview (the default, no-write path) -----------------------------------


async def test_preview_writes_nothing_and_returns_a_diff(store: SqlStandingStore) -> None:
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="the fold's position",
        fold_count=3, updated_at=T0, last_folded_row_id=42,
    ))
    before, diff = await preview_operator_edit(
        platform=PLATFORM, conversation_id=CONV,
        new_body="the operator's correction", store=store, max_chars=MAX_CHARS,
    )
    assert before.body == "the fold's position"
    assert "-the fold's position" in diff
    assert "+the operator's correction" in diff

    # Nothing was written — the head and its history are exactly as seeded.
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None and head.body == "the fold's position"


async def test_preview_of_a_room_with_no_standing_row_is_refused(
    store: SqlStandingStore,
) -> None:
    with pytest.raises(EditRefusedError, match="nothing to edit"):
        await preview_operator_edit(
            platform=PLATFORM, conversation_id="no-such-room",
            new_body="anything", store=store, max_chars=MAX_CHARS,
        )


def test_render_diff_says_so_when_there_is_no_change() -> None:
    assert render_diff(before_body="same", new_body="same") == "(no change)\n"


# --- apply --------------------------------------------------------------


async def test_apply_lands_as_operator_and_keeps_fold_count_and_watermark(
    store: SqlStandingStore,
) -> None:
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="the fold's position",
        fold_count=3, updated_at=T0, last_folded_row_id=42,
    ))

    outcome = await apply_operator_edit(
        platform=PLATFORM, conversation_id=CONV,
        new_body="the operator's correction", store=store, now_ms=T0 + 1000,
        max_chars=MAX_CHARS, recheck_delay_seconds=0,
    )
    assert outcome.before.body == "the fold's position"
    assert outcome.after.body == "the operator's correction"
    assert outcome.after.fold_count == 3
    assert outcome.after.last_folded_row_id == 42
    assert outcome.moved_again is False
    assert outcome.reread is not None and outcome.reread.body == "the operator's correction"

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "the operator's correction"
    assert head.fold_count == 3
    assert head.last_folded_row_id == 42


async def test_apply_on_a_room_holding_a_working_set_lands_and_keeps_it(
    store: SqlStandingStore,
) -> None:
    """The edit must carry `working_set` over from the held `Standing`:
    rebuilding it field by field without it would write an empty working set
    over the held one, and `write_if_unchanged` would refuse every edit of such
    a room."""
    held = (WorkingSetItem(what="two more options", source="web_search", found_at=T0),)
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="the fold's position",
        fold_count=3, updated_at=T0, last_folded_row_id=42, working_set=held,
    ))

    outcome = await apply_operator_edit(
        platform=PLATFORM, conversation_id=CONV,
        new_body="the operator's correction", store=store, now_ms=T0 + 1000,
        max_chars=MAX_CHARS, recheck_delay_seconds=0,
    )
    assert outcome.after.working_set == held

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "the operator's correction"
    assert head.working_set == held


async def test_apply_on_a_room_with_no_standing_row_is_refused(
    store: SqlStandingStore,
) -> None:
    with pytest.raises(EditRefusedError, match="nothing to edit"):
        await apply_operator_edit(
            platform=PLATFORM, conversation_id="no-such-room",
            new_body="anything", store=store, now_ms=T0,
            max_chars=MAX_CHARS, recheck_delay_seconds=0,
        )


async def test_a_fold_landing_between_the_read_and_the_write_refuses_the_edit(
    store: SqlStandingStore,
) -> None:
    """The race `write_if_unchanged` exists to close. `apply_operator_edit`
    cannot be handed a stale read directly (it does its own read), so the
    fold is made to land INSIDE the function's own window by monkeypatching
    `read` to write behind its own back before returning — the only way to
    exercise this from the public entry point rather than the store method
    alone, which `tests/infra/test_standing_revision.py` already covers
    directly."""
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
            await apply_operator_edit(
                platform=PLATFORM, conversation_id=CONV,
                new_body="the operator's edit", store=store, now_ms=T0 + 2000,
                max_chars=MAX_CHARS, recheck_delay_seconds=0,
            )
    finally:
        store.read = real_read  # type: ignore[method-assign]

    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None
    assert head.body == "position two, folded mid-edit"


async def test_a_body_over_the_operating_cap_is_refused_before_the_hard_ceiling(
    store: SqlStandingStore,
) -> None:
    """`[standing] max_chars` is the cap the FOLD is held to; an operator edit
    is not exempt from it just because the database's hard ceiling would still
    accept the body. A body between the two proves this check runs before
    Standing's own."""
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="a fine position",
        fold_count=1, updated_at=T0,
    ))
    over_cap_under_ceiling = "x" * (MAX_CHARS + 1)
    assert len(over_cap_under_ceiling) < STANDING_BODY_CEILING_CHARS
    with pytest.raises(EditRefusedError, match="max_chars"):
        await apply_operator_edit(
            platform=PLATFORM, conversation_id=CONV,
            new_body=over_cap_under_ceiling,
            store=store, now_ms=T0 + 1000, max_chars=MAX_CHARS,
            recheck_delay_seconds=0,
        )
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None and head.body == "a fine position"


async def test_an_over_ceiling_body_is_still_refused_and_writes_nothing(
    store: SqlStandingStore,
) -> None:
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="a fine position",
        fold_count=1, updated_at=T0,
    ))
    with pytest.raises(EditRefusedError):
        await apply_operator_edit(
            platform=PLATFORM, conversation_id=CONV,
            new_body="x" * (STANDING_BODY_CEILING_CHARS + 1),
            store=store, now_ms=T0 + 1000, max_chars=STANDING_BODY_CEILING_CHARS + 10,
            recheck_delay_seconds=0,
        )
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None and head.body == "a fine position"


# --- the recheck ("a fold can still overwrite an operator edit") ---------------------


async def test_the_recheck_reports_a_fold_that_lands_during_the_wait(
    store: SqlStandingStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race this script CANNOT close, only narrow. A fold's write lands
    during the recheck window; `apply_operator_edit`'s own write has already
    succeeded by then, so this is not a refusal — it is the outcome reporting
    that something moved again right after."""
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="position one",
        fold_count=1, updated_at=T0,
    ))

    # The fold is written the moment the operator's compare-and-set returns,
    # never on a wall clock. A fold started as a task before the edit would race
    # it: under a loaded parallel gate it could land BEFORE the operator's write,
    # which would then refuse as stale.
    operator_write = store.write_if_unchanged

    async def _operator_write_then_a_fold(**kwargs: Any) -> bool:
        applied = await operator_write(**kwargs)
        await store.write(standing=Standing(
            platform=PLATFORM, conversation_id=CONV,
            body="folded during the recheck window", fold_count=2,
            updated_at=T0 + 5000,
        ))
        return applied

    monkeypatch.setattr(store, "write_if_unchanged", _operator_write_then_a_fold)
    outcome = await apply_operator_edit(
        platform=PLATFORM, conversation_id=CONV,
        new_body="the operator's correction", store=store, now_ms=T0 + 1000,
        max_chars=MAX_CHARS, recheck_delay_seconds=0,
    )

    assert outcome.moved_again is True
    assert outcome.reread is not None
    assert outcome.reread.body == "folded during the recheck window"
    # And the fold's write is what actually stands — this script never
    # re-clobbers what it finds on the recheck.
    head = await store.read(platform=PLATFORM, conversation_id=CONV)
    assert head is not None and head.body == "folded during the recheck window"


async def test_the_recheck_finds_nothing_moved_when_nothing_did(
    store: SqlStandingStore,
) -> None:
    await store.write(standing=Standing(
        platform=PLATFORM, conversation_id=CONV, body="position one",
        fold_count=1, updated_at=T0,
    ))
    outcome = await apply_operator_edit(
        platform=PLATFORM, conversation_id=CONV,
        new_body="the operator's correction", store=store, now_ms=T0 + 1000,
        max_chars=MAX_CHARS, recheck_delay_seconds=0,
    )
    assert outcome.moved_again is False
