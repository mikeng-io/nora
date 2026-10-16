"""The bake-off's trigram arm is the live seed over nodes the live projector
wrote; it writes only into a database that proves it is a throwaway, and a
load that did not land every node is a failure, never a smaller pool
(`nora.subject.lexical_seed`)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command

from evals.retrieval_bakeoff.run import Media
from nora.infra.sql import memory_index
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_index import _reading
from nora.infra.sql.migrations_runner import alembic_config, current_revision
from nora.infra.sql.schema import MEMORY_PROJECTION_QUEUE, MESSAGES
from nora.subject import lexical_seed

SHA = {n: f"{n:064x}" for n in range(1, 4)}
POOL = {
    "img-1": (SHA[1], "Egg tarts", "On a metal tray", ""),
    "img-2": (SHA[2], "Tram", "A tram on a wet street at night", ""),
    "img-3": (SHA[3], "點心", "蒸籠入面有四粒小籠包", ""),
}


def _url(pg_admin, db: str) -> str:
    return (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
            f"@{pg_admin.host}:{pg_admin.port}/{db}")


def _create(pg_admin, name: str, template: str | None = None) -> str:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as c:
        if template:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      "WHERE datname = %s AND pid <> pg_backend_pid()", (template,))
            c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
        else:
            c.execute(f'CREATE DATABASE "{name}"')
    return name


def _drop(pg_admin, name: str) -> None:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _tables(pg_admin, db: str) -> set[str]:
    with psycopg.connect(pg_admin.dsn(db)) as c:
        return {r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}


@pytest.fixture(scope="module")
def fresh(pg_admin) -> Iterator[str]:
    """An empty database named for the bake-off, loaded once from nothing."""
    name = _create(pg_admin, f"bakeoff_{uuid.uuid4().hex}")
    url = _url(pg_admin, name)
    lexical_seed.load(url, POOL)
    yield url
    _drop(pg_admin, name)


@pytest.fixture
def loaded(pg_admin, pg_template) -> Iterator[str]:
    """A bake-off database a previous load left behind: at head, marked."""
    name = _create(pg_admin, f"bakeoff_{uuid.uuid4().hex}", template=pg_template)
    with psycopg.connect(pg_admin.dsn(name), autocommit=True) as c:
        c.execute(f"CREATE TABLE {lexical_seed.MARKER} (created_at timestamptz DEFAULT now())")
    yield _url(pg_admin, name)
    _drop(pg_admin, name)


def test_a_fresh_load_marks_the_database_and_ranks_by_the_live_terms(fresh: str, pg_admin) -> None:
    assert lexical_seed.MARKER in _tables(pg_admin, fresh.rsplit("/", 1)[1])
    got = lexical_seed.seed(fresh, {"q-en": "the egg tarts", "q-yue": "小籠包", "q-none": "the"},
                            limit=10)
    assert got["q-en"][0] == "img-1"
    assert got["q-yue"] == ["img-3"]
    assert got["q-none"] == []  # no term survives terms_of: nothing, not everything


def test_load_replaces_the_bakeoff_room_and_nothing_else(loaded: str) -> None:
    lexical_seed.load(loaded, {"img-1": (SHA[1], "first", "", "")})
    lexical_seed.load(loaded, {"img-2": (SHA[2], "second", "", "")})
    assert lexical_seed.seed(loaded, {"q": "first"}, limit=10)["q"] == []
    assert lexical_seed.seed(loaded, {"q": "second"}, limit=10)["q"] == ["img-2"]


def test_messages_are_seeded_by_their_text_and_ties_go_to_the_newer(loaded: str) -> None:
    lexical_seed.load(loaded, {
        "msg-1": (1_000, "打風 聽日唔使返工"),
        "msg-2": (2_000, "打風 聽日唔使返工"),
        "msg-3": (3_000, "a tram on a wet street"),
    })
    got = lexical_seed.seed(loaded, {"q": "打風"}, limit=10)["q"]
    assert got == ["msg-2", "msg-1"]  # the live seed's tie-break: newest first


def test_a_database_not_named_for_the_bakeoff_is_refused_untouched(pg_admin) -> None:
    name = _create(pg_admin, f"t_{uuid.uuid4().hex}")  # empty: only the name is wrong
    try:
        with pytest.raises(lexical_seed.NotAThrowawayDatabaseError, match="bakeoff"):
            lexical_seed.load(_url(pg_admin, name), POOL)
        assert _tables(pg_admin, name) == set()
    finally:
        _drop(pg_admin, name)


def test_a_non_empty_unmarked_database_is_refused_untouched(pg_admin) -> None:
    name = _create(pg_admin, f"bakeoff_{uuid.uuid4().hex}")
    try:
        with psycopg.connect(pg_admin.dsn(name), autocommit=True) as c:
            c.execute("CREATE TABLE traces (id int PRIMARY KEY, body text)")
        with pytest.raises(lexical_seed.NotAThrowawayDatabaseError, match="marker"):
            lexical_seed.load(_url(pg_admin, name), POOL)
        assert _tables(pg_admin, name) == {"traces"}
    finally:
        _drop(pg_admin, name)


def test_a_live_database_behind_head_is_refused_before_any_migration(pg_admin) -> None:
    name = _create(pg_admin, f"bakeoff_{uuid.uuid4().hex}")
    url = _url(pg_admin, name)
    try:
        command.upgrade(alembic_config(url), "0104_external_files")
        before = current_revision(url)
        with pytest.raises(lexical_seed.NotAThrowawayDatabaseError):
            lexical_seed.load(url, POOL)
        assert current_revision(url) == before == "0104_external_files"
    finally:
        _drop(pg_admin, name)


def test_a_marked_database_holding_a_foreign_message_is_refused(loaded: str) -> None:
    async def foreign() -> None:
        engine = create_sql_engine(loaded)
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MESSAGES).values(
                platform="signal", conversation_id="a-real-room", conversation_type="group",
                sender_id="someone", ts=1, body="x"))
        await engine.dispose()

    asyncio.run(foreign())
    with pytest.raises(lexical_seed.NotAThrowawayDatabaseError, match="messages"):
        lexical_seed.load(loaded, POOL)


def test_a_projection_that_fails_fails_the_load(loaded: str, monkeypatch) -> None:
    """`_guarded` queues a failed projection and carries on; scored, the
    missing nodes would read as misses, with an OK verdict."""
    real = memory_index._PROJECTORS["message"]
    calls = {"n": 0}

    async def flaky(conn, sid):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise RuntimeError("projector down")
        return await real(conn, sid)

    monkeypatch.setitem(memory_index._PROJECTORS, "message", flaky)
    with pytest.raises(lexical_seed.IncompleteLoadError):
        lexical_seed.load(loaded, POOL)


def test_a_projection_left_queued_fails_the_load(loaded: str) -> None:
    """A queued projection means the index is not what was loaded, even when
    the node count happens to match."""
    async def queued() -> None:
        engine = create_sql_engine(loaded)
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MEMORY_PROJECTION_QUEUE).values(
                kind="message", source_id="999999", last_error="RuntimeError"))
        await engine.dispose()

    asyncio.run(queued())
    with pytest.raises(lexical_seed.IncompleteLoadError, match="1 projections queued"):
        lexical_seed.load(loaded, POOL)


@pytest.mark.parametrize("fields", [("T", "D", "C"), ("T", "", "C"), ("", "", "C"), ("", "", "")])
def test_the_text_arms_read_what_the_index_reads(fields: tuple[str, str, str]) -> None:
    """BM25 and the dense floor see the same text the trigram arm's nodes hold."""
    assert Media("0" * 64, *fields).reading == _reading(*fields)
