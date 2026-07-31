"""SQLite ambient buffer: append, query recent, enforce rolling cap (DESIGN.md §4).

Stateless by design — every call opens a connection, does its work, and closes.
"""

from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
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
CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_messages_is_nora ON messages(is_nora);
"""


async def init(db_path: Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def append(
    db_path: Path,
    *,
    group_id: str,
    sender_id: str,
    sender_name: str,
    ts: int,
    body: str,
    is_nora: bool = False,
    raw_json: str | None = None,
    max_per_group: int = 500,
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO messages (group_id, sender_id, sender_name, ts, body, is_nora, raw_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, sender_id, sender_name, ts, body, int(is_nora), raw_json),
        )
        # rolling window: keep only the newest max_per_group rows for this group
        await db.execute(
            "DELETE FROM messages WHERE group_id = ? AND id NOT IN ("
            "  SELECT id FROM messages WHERE group_id = ? ORDER BY ts DESC, id DESC LIMIT ?)",
            (group_id, group_id, max_per_group),
        )
        await db.commit()


async def recent(db_path: Path, group_id: str, limit: int) -> list[dict]:
    """Newest `limit` messages for a group, returned oldest-first."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT sender_name, body, is_nora FROM messages WHERE group_id = ?"
            " ORDER BY ts DESC, id DESC LIMIT ?",
            (group_id, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return list(reversed(rows))
