import aiosqlite
import pytest

from nora.models import NormalizedMessage
from nora.store import db

V1_SCHEMA = """
CREATE TABLE messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      TEXT NOT NULL,
    sender_id     TEXT NOT NULL,
    sender_name   TEXT,
    ts            INTEGER NOT NULL,
    body          TEXT,
    is_nora       INTEGER NOT NULL DEFAULT 0,
    raw_json      TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_group_ts ON messages(group_id, ts DESC);
CREATE INDEX idx_messages_is_nora ON messages(is_nora);
"""


@pytest.fixture
async def v1_db(tmp_path):
    path = tmp_path / "messages.db"
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(V1_SCHEMA)
        await conn.executemany(
            "INSERT INTO messages (group_id, sender_id, sender_name, ts, body, is_nora, raw_json)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                ("gA", "+852111", "Miles", 10, "hello", 0, '{"x":1}'),
                ("gA", "nora", "Nora", 11, "hi Miles", 1, None),
                ("gB", "+852222", "Ada", 12, "yo", 0, None),
            ],
        )
        await conn.commit()
    return path


async def _cols(path) -> set[str]:
    async with aiosqlite.connect(path) as conn:
        cur = await conn.execute("PRAGMA table_info(messages)")
        return {r[1] for r in await cur.fetchall()}


async def test_migration_produces_the_v2_columns(v1_db):
    await db.init(v1_db)
    cols = await _cols(v1_db)
    assert "conversation_id" in cols
    assert "platform" in cols
    assert "conversation_type" in cols
    assert "delivery_status" in cols
    assert "group_id" not in cols


async def test_migration_preserves_every_row(v1_db):
    await db.init(v1_db)
    assert (await db.stats(v1_db))["messages"] == 3


async def test_migration_backfills_platform_and_type(v1_db):
    await db.init(v1_db)
    async with aiosqlite.connect(v1_db) as conn:
        cur = await conn.execute("SELECT DISTINCT platform, conversation_type FROM messages")
        assert await cur.fetchall() == [("signal", "group")]


async def test_migration_maps_group_id_to_conversation_id(v1_db):
    await db.init(v1_db)
    rows = await db.recent(v1_db, "gA", 10)
    assert [r["body"] for r in rows] == ["hello", "hi Miles"]
    assert [r["body"] for r in await db.recent(v1_db, "gB", 10)] == ["yo"]


async def test_migration_marks_existing_nora_rows_as_sent(v1_db):
    await db.init(v1_db)
    rows = await db.recent(v1_db, "gA", 10)
    by_body = {r["body"]: r for r in rows}
    assert by_body["hi Miles"]["delivery_status"] == "sent"
    assert by_body["hello"]["delivery_status"] == "pending"


async def test_migration_preserves_raw_json(v1_db):
    await db.init(v1_db)
    rows = await db.recent(v1_db, "gA", 10)
    assert rows[0]["raw_json"] == '{"x":1}'


async def test_migration_is_idempotent(v1_db):
    await db.init(v1_db)
    await db.init(v1_db)
    await db.init(v1_db)
    assert (await db.stats(v1_db))["messages"] == 3
    assert "group_id" not in await _cols(v1_db)


async def test_migration_sets_user_version(v1_db):
    await db.init(v1_db)
    async with aiosqlite.connect(v1_db) as conn:
        cur = await conn.execute("PRAGMA user_version")
        assert (await cur.fetchone())[0] == db.SCHEMA_VERSION


async def test_a_fresh_db_needs_no_migration(tmp_path):
    path = tmp_path / "fresh.db"
    await db.init(path)
    await db.append(path, NormalizedMessage("signal", "g", "group", "+1", "A", 1, "x", {}))
    assert (await db.stats(path))["messages"] == 1


async def test_appends_work_after_migration(v1_db):
    await db.init(v1_db)
    await db.append(
        v1_db, NormalizedMessage("signal", "gA", "group", "+852111", "Miles", 20, "new", {})
    )
    assert [r["body"] for r in await db.recent(v1_db, "gA", 10)] == ["hello", "hi Miles", "new"]
