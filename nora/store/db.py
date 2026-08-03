"""SQLite audit store (spec C.4, design/01). Stateless: every call opens and closes.

Role: audit record + the tagging path's context source. No semantics live here.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from ..models import NormalizedMessage

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
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
);
CREATE INDEX IF NOT EXISTS idx_messages_conv_ts ON messages(conversation_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_is_nora ON messages(is_nora);
"""

_VALID_STATUS = ("pending", "sent", "failed")


async def _connect(path: Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init(path: Path) -> None:
    """Create the schema if absent, then run the v1→v2 migration (Task 9)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = await _connect(path)
    try:
        from .migrate import migrate  # local import keeps the migration self-contained

        await migrate(conn)
        await conn.executescript(SCHEMA)
        await conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        await conn.commit()
    finally:
        await conn.close()


async def append(
    path: Path,
    msg: NormalizedMessage,
    *,
    is_nora: bool = False,
    delivery_status: str = "pending",
    max_per_conversation: int = 500,
) -> int:
    if delivery_status not in _VALID_STATUS:
        raise ValueError(f"delivery_status must be one of {_VALID_STATUS}, got {delivery_status!r}")
    conn = await _connect(path)
    try:
        cur = await conn.execute(
            "INSERT INTO messages (platform, conversation_id, conversation_type, sender_id,"
            " sender_name, ts, body, is_nora, delivery_status, raw_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                msg.platform, msg.conversation_id, msg.conversation_type, msg.sender_id,
                msg.sender_name, msg.timestamp, msg.body, int(is_nora), delivery_status,
                json.dumps(msg.raw),
            ),
        )
        row_id = cur.lastrowid
        # Prune in the same transaction: the window is an invariant, not a chore.
        await conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id NOT IN ("
            "  SELECT id FROM messages WHERE conversation_id = ?"
            "  ORDER BY ts DESC, id DESC LIMIT ?)",
            (msg.conversation_id, msg.conversation_id, max_per_conversation),
        )
        await conn.commit()
        return int(row_id)
    finally:
        await conn.close()


async def recent(path: Path, conversation_id: str, limit: int) -> list[dict]:
    """Newest `limit` rows for one conversation, returned oldest-first."""
    conn = await _connect(path)
    try:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT id, sender_id, sender_name, ts, body, is_nora, delivery_status, raw_json"
            " FROM messages WHERE conversation_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()
    return list(reversed(rows))


async def set_delivery_status(path: Path, row_id: int, status: str) -> None:
    if status not in _VALID_STATUS:
        raise ValueError(f"delivery_status must be one of {_VALID_STATUS}, got {status!r}")
    conn = await _connect(path)
    try:
        await conn.execute("UPDATE messages SET delivery_status = ? WHERE id = ?", (status, row_id))
        await conn.commit()
    finally:
        await conn.close()


async def prune(path: Path, conversation_id: str, keep: int) -> int:
    conn = await _connect(path)
    try:
        cur = await conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id NOT IN ("
            "  SELECT id FROM messages WHERE conversation_id = ?"
            "  ORDER BY ts DESC, id DESC LIMIT ?)",
            (conversation_id, conversation_id, keep),
        )
        await conn.commit()
        return cur.rowcount or 0
    finally:
        await conn.close()


async def stats(path: Path) -> dict:
    conn = await _connect(path)
    try:
        cur = await conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT conversation_id), COALESCE(SUM(is_nora),0),"
            " COALESCE(MAX(ts),0) FROM messages"
        )
        total, conversations, nora, latest = await cur.fetchone()
    finally:
        await conn.close()
    return {
        "messages": total,
        "conversations": conversations,
        "nora_messages": nora,
        "latest_ts": latest,
        "schema_version": SCHEMA_VERSION,
    }
