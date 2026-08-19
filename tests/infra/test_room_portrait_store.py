"""`SqlRoomPortraitStore` — Dreamer Plan 3's durability layer. Much simpler
than `tests/infra/test_language_store.py`/`tests/infra/test_exchange_store.py`:
one UPSERT table, one row per scope, no evidence layer and no watermark of
its own (see `nora.infra.sql.room_portrait`'s module docstring).
"""

from __future__ import annotations

import pytest

from nora.domain.ports import RoomPortraitReader
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.room_portrait import SqlRoomPortraitStore


@pytest.fixture
async def store(pg_url) -> SqlRoomPortraitStore:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    s = SqlRoomPortraitStore(engine)
    yield s
    await engine.dispose()


def test_the_store_satisfies_the_reader_port():
    assert isinstance(
        SqlRoomPortraitStore(create_sql_engine("postgresql+asyncpg://x/x")),
        RoomPortraitReader,
    )


async def test_an_unknown_scope_reads_empty(store):
    portrait = await store.room_portrait(scope_id="never-seen")
    assert portrait.narrative == ""
    assert not portrait


async def test_upsert_then_read_round_trips(store):
    await store.upsert_portrait(
        scope_id="g1", narrative="This room has its own vocabulary.",
        lexeme_basis=("big walk",), exchange_basis=("banter",),
    )
    portrait = await store.room_portrait(scope_id="g1")
    assert portrait.narrative == "This room has its own vocabulary."
    assert portrait


async def test_a_second_upsert_replaces_rather_than_appends(store):
    """UPSERT, not append — a portrait has no evidence layer to accumulate
    (see module docstring); a second synthesis for the same scope must
    REPLACE the row, never leave two."""
    await store.upsert_portrait(
        scope_id="g1", narrative="first narrative",
        lexeme_basis=("word1",), exchange_basis=(),
    )
    await store.upsert_portrait(
        scope_id="g1", narrative="second narrative",
        lexeme_basis=("word2",), exchange_basis=("banter",),
    )
    portrait = await store.room_portrait(scope_id="g1")
    assert portrait.narrative == "second narrative"


async def test_an_upsert_that_clears_the_narrative_is_read_back_empty(store):
    """A scope that used to have material and no longer does must clear,
    not keep serving a stale portrait — `PortraitSynthesizer.synthesize`
    always UPSERTs, even an empty result, for exactly this reason."""
    await store.upsert_portrait(
        scope_id="g1", narrative="had material once",
        lexeme_basis=("word1",), exchange_basis=(),
    )
    await store.upsert_portrait(
        scope_id="g1", narrative="", lexeme_basis=(), exchange_basis=(),
    )
    portrait = await store.room_portrait(scope_id="g1")
    assert portrait.narrative == ""


async def test_scopes_are_isolated(store):
    await store.upsert_portrait(
        scope_id="g1", narrative="room one", lexeme_basis=(), exchange_basis=(),
    )
    await store.upsert_portrait(
        scope_id="g2", narrative="room two", lexeme_basis=(), exchange_basis=(),
    )
    assert (await store.room_portrait(scope_id="g1")).narrative == "room one"
    assert (await store.room_portrait(scope_id="g2")).narrative == "room two"
