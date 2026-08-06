"""The engine over `postgresql+asyncpg://…`.

The SQLite era's PRAGMA/WAL/busy_timeout/parent-directory tests are gone with
the SQLite backend (design/20). What remains to prove is that the read/write
engine writes, and that the read-only control-plane engine is refused a write by
the SERVER — not by a caller keeping a promise.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from nora.infra.sql.engine import create_readonly_sql_engine, create_sql_engine


async def test_the_readwrite_engine_can_write(pg_url):
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE t (id bigint)"))
            await conn.execute(sa.text("INSERT INTO t VALUES (1)"))
        async with engine.connect() as conn:
            assert await conn.scalar(sa.text("SELECT id FROM t")) == 1
    finally:
        await engine.dispose()


async def test_a_signal_magnitude_timestamp_survives_a_bigint_column(pg_url):
    """The overflow that was invisible on SQLite (8-byte INTEGER) and is a crash
    on INT4. Reverting the column to `integer` here raises NumericValueOutOfRange
    instead — which is exactly the failure design/20 §2 fixes for `ts`."""
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE t (ts bigint)"))
            await conn.execute(sa.text("INSERT INTO t VALUES (1785950108283)"))
        async with engine.connect() as conn:
            assert await conn.scalar(sa.text("SELECT ts FROM t")) == 1785950108283
    finally:
        await engine.dispose()


async def test_the_readonly_engine_reads(pg_url):
    write = create_sql_engine(pg_url)
    try:
        async with write.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE t (id bigint)"))
            await conn.execute(sa.text("INSERT INTO t VALUES (7)"))
    finally:
        await write.dispose()

    ro = create_readonly_sql_engine(pg_url)
    try:
        async with ro.connect() as conn:
            assert await conn.scalar(sa.text("SELECT id FROM t")) == 7
    finally:
        await ro.dispose()


async def test_the_readonly_engine_refuses_a_write_at_the_server(pg_url):
    """`default_transaction_read_only=on` is enforced by Postgres, so an INSERT
    raises `ReadOnlySqlTransaction` ("cannot execute … in a read-only
    transaction") — the driver-enforced replacement for SQLite's `mode=ro`. A
    read-only guarantee the caller merely promised would not fail here."""
    write = create_sql_engine(pg_url)
    try:
        async with write.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE t (id bigint)"))
    finally:
        await write.dispose()

    ro = create_readonly_sql_engine(pg_url)
    try:
        with pytest.raises(DBAPIError, match=r"read-only transaction"):
            async with ro.begin() as conn:
                await conn.execute(sa.text("INSERT INTO t VALUES (1)"))
    finally:
        await ro.dispose()
