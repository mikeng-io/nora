"""The PRAGMAs are per-connection, not per-database. `db.py` set them in its
open-and-close-per-call helper; a pooled engine must set them on every new
connection or WAL and busy_timeout silently stop applying."""

from __future__ import annotations

import sqlalchemy as sa

from nora.infra.sql.engine import async_url_for, create_sql_engine


def test_async_url_uses_the_aiosqlite_driver(tmp_path):
    assert async_url_for(tmp_path / "m.db").startswith("sqlite+aiosqlite:///")


async def test_wal_is_enabled(tmp_path):
    engine = create_sql_engine(tmp_path / "m.db")
    try:
        async with engine.connect() as conn:
            mode = await conn.scalar(sa.text("PRAGMA journal_mode"))
        assert mode == "wal"
    finally:
        await engine.dispose()


async def test_busy_timeout_is_set(tmp_path):
    engine = create_sql_engine(tmp_path / "m.db", busy_timeout_ms=5000)
    try:
        async with engine.connect() as conn:
            assert await conn.scalar(sa.text("PRAGMA busy_timeout")) == 5000
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
    than on the connect event. Two sequential checkouts must both see them."""
    engine = create_sql_engine(tmp_path / "m.db")
    try:
        for _ in range(2):
            async with engine.connect() as conn:
                assert await conn.scalar(sa.text("PRAGMA foreign_keys")) == 1
                assert await conn.scalar(sa.text("PRAGMA busy_timeout")) == 5000
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
