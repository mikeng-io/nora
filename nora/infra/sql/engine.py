"""One long-lived engine, replacing `db.py`'s open-and-close-per-call.

The PRAGMAs hang off the `connect` event rather than being executed once after
construction, because they are per-connection state and the pool opens new
connections lazily. Setting them once would leave every connection after the
first without WAL or a busy timeout — which is exactly the condition under
which an N-adapter burst produces SQLITE_BUSY and silent audit loss.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DEFAULT_BUSY_TIMEOUT_MS = 5000


def async_url_for(db_path: Path | str) -> str:
    return f"sqlite+aiosqlite:///{Path(db_path)}"


def create_sql_engine(
    db_path: Path | str, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> AsyncEngine:
    """Build the engine. Creates the database's parent directory if absent —
    `db.init` used to do this and nothing else would."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(async_url_for(db_path))

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine
