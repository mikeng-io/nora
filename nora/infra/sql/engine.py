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


def readonly_async_url_for(db_path: Path | str) -> str:
    """SQLite's `mode=ro` URI form, which the DRIVER enforces — not a promise
    the caller keeps by only writing SELECTs."""
    return f"sqlite+aiosqlite:///file:{Path(db_path)}?mode=ro&uri=true"


def create_readonly_sql_engine(
    db_path: Path | str, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> AsyncEngine:
    """An engine that CANNOT write, for the operator control plane (D6).

    `nora groups` and `nora persons pending` are strictly read-only, and the
    honest way to say so is to make a write impossible rather than to promise
    one is never issued. Two independent mechanisms, because each covers a hole
    the other leaves:

      * `mode=ro` in the URI — SQLite opens the file read-only, so a write
        fails with "attempt to write a readonly database" at the driver.
      * `PRAGMA query_only=ON` — refuses the statement before it reaches the
        file, which also covers a temp/attached database `mode=ro` would not.

    It deliberately does NOT set `journal_mode=WAL` the way `create_sql_engine`
    does. That PRAGMA is itself a write to the database header, so a control-plane
    command that ran it would mutate the very file it claims only to read; on a
    `mode=ro` connection it is a silent no-op anyway. It also does not create
    the parent directory: a missing database means Nora has not run here, which
    is something to report, not to paper over by creating a directory.
    """
    engine = create_async_engine(readonly_async_url_for(db_path))

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            cursor.execute("PRAGMA query_only=ON")
        finally:
            cursor.close()

    return engine


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
