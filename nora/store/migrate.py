"""v1 → v2 schema migration (spec C.4).

The v1 table is `messages(group_id, …)` with no platform, conversation_type or
delivery_status. Rebuild rather than ALTER: one transaction, version-independent,
and the CHECK constraint plus defaults apply to backfilled rows.

Idempotent, guarded by PRAGMA user_version (0 = v1 or fresh, 1 = v2).
"""

from __future__ import annotations

import logging

import aiosqlite

log = logging.getLogger("nora.store.migrate")

_V2_TABLE = """
CREATE TABLE messages_v2 (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT NOT NULL,
    conversation_id    TEXT NOT NULL,
    conversation_type  TEXT NOT NULL CHECK (conversation_type IN ('group','dm')),
    sender_id          TEXT NOT NULL,
    sender_name        TEXT,
    ts                 INTEGER NOT NULL,
    body               TEXT,
    is_nora            INTEGER NOT NULL DEFAULT 0,
    delivery_status    TEXT NOT NULL DEFAULT 'pending',
    raw_json           TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_COPY = """
INSERT INTO messages_v2
    (id, platform, conversation_id, conversation_type, sender_id, sender_name,
     ts, body, is_nora, delivery_status, raw_json, created_at)
SELECT id, 'signal', group_id, 'group', sender_id, sender_name,
       ts, body, is_nora,
       CASE WHEN is_nora = 1 THEN 'sent' ELSE 'pending' END,
       raw_json, created_at
FROM messages
"""


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}


async def migrate(conn: aiosqlite.Connection) -> bool:
    """Return True if a v1 table was rebuilt. Safe to call on every startup."""
    cur = await conn.execute("PRAGMA user_version")
    if (await cur.fetchone())[0] >= 1:
        return False

    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    if await cur.fetchone() is None:
        return False                       # fresh database — db.init creates v2 directly

    cols = await _table_columns(conn, "messages")
    if "group_id" not in cols:
        return False                       # already v2-shaped, just missing user_version

    log.info("migrating v1 messages table to the v2 schema")
    await conn.execute("DROP TABLE IF EXISTS messages_v2")
    await conn.execute(_V2_TABLE)
    await conn.execute(_COPY)
    await conn.execute("DROP INDEX IF EXISTS idx_messages_group_ts")
    await conn.execute("DROP INDEX IF EXISTS idx_messages_is_nora")
    await conn.execute("DROP TABLE messages")
    await conn.execute("ALTER TABLE messages_v2 RENAME TO messages")
    await conn.commit()
    log.info("v1 → v2 migration complete")
    return True
