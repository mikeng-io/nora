"""The PRAGMAs are per-connection, not per-database. `db.py` set them in its
open-and-close-per-call helper; a pooled engine must set them on every new
connection or WAL and busy_timeout silently stop applying."""

from __future__ import annotations

import sqlalchemy as sa

from nora.infra.sql.engine import async_url_for, create_sql_engine


def test_async_url_uses_the_aiosqlite_driver(tmp_path):
    assert async_url_for(tmp_path / "m.db").startswith("sqlite+aiosqlite:///")


async def test_wal_is_enabled(tmp_path):
    """A state assertion, not a per-connection one: WAL is recorded in the
    database file header and survives reconnection, so this proves the database
    ends up in WAL mode, not that the hook runs on every connection. The
    per-connection property is covered by
    `test_pragmas_apply_to_every_pooled_connection`."""
    engine = create_sql_engine(tmp_path / "m.db")
    try:
        async with engine.connect() as conn:
            mode = await conn.scalar(sa.text("PRAGMA journal_mode"))
        assert mode == "wal"
    finally:
        await engine.dispose()


async def test_busy_timeout_is_set_to_the_configured_value(tmp_path):
    """Deliberately NOT 5000. Python's `sqlite3.connect()` defaults to
    `timeout=5.0`, i.e. `PRAGMA busy_timeout=5000`, so asserting the default
    value would pass even if the connect hook never ran — verified 2026-08-05.
    A non-default value is what makes this test capable of failing."""
    engine = create_sql_engine(tmp_path / "m.db", busy_timeout_ms=1234)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(sa.text("PRAGMA busy_timeout")) == 1234
    finally:
        await engine.dispose()


async def test_foreign_keys_are_on(tmp_path):
    engine = create_sql_engine(tmp_path / "m.db")
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(sa.text("PRAGMA foreign_keys")) == 1
    finally:
        await engine.dispose()


async def test_pragmas_apply_to_every_pooled_connection(tmp_path):
    """The regression this guards: setting PRAGMAs once at construction rather
    than on the `connect` event. Asserts only the two that are genuinely
    per-connection state — `foreign_keys` (defaults to 0 on every new
    connection) and a non-default `busy_timeout`. `journal_mode` is
    deliberately NOT asserted here: WAL persists in the database file header,
    so a second connection reports `wal` whether or not the hook ran, and it
    would make this test pass against the very implementation it exists to
    reject."""
    engine = create_sql_engine(tmp_path / "m.db", busy_timeout_ms=1234)
    try:
        for _ in range(3):
            async with engine.connect() as conn:
                assert await conn.scalar(sa.text("PRAGMA foreign_keys")) == 1
                assert await conn.scalar(sa.text("PRAGMA busy_timeout")) == 1234
    finally:
        await engine.dispose()


async def test_the_parent_directory_is_created(tmp_path):
    engine = create_sql_engine(tmp_path / "nested" / "deeper" / "m.db")
    try:
        async with engine.connect() as conn:
            await conn.scalar(sa.text("SELECT 1"))
        assert (tmp_path / "nested" / "deeper").is_dir()
    finally:
        await engine.dispose()
