"""Alembic's baseline IS the schema.

The single Postgres-native baseline builds the schema on a fresh, empty
database; there is no data migration.
"""

from __future__ import annotations

import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import (
    current_revision,
    head_revision,
    run_migrations,
    sync_url_for,
)
from nora.infra.sql.schema import METADATA


async def test_upgrade_head_reaches_the_head_revision(pg_url):
    run_migrations(pg_url)
    assert current_revision(pg_url) == head_revision()


async def test_startup_is_idempotent_across_restarts(pg_url):
    for _ in range(3):
        run_migrations(pg_url)
    assert current_revision(pg_url) == head_revision()


async def test_a_migrated_database_accepts_writes(pg_url):
    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url))
    try:
        await store.append(message=NormalizedMessage(
            "signal", "g", "group", "+1", "A", 1, "x", {}))
        assert (await store.stats())["messages"] == 1
    finally:
        await store.aclose()


async def test_a_signal_magnitude_timestamp_round_trips_through_the_store(pg_url):
    """`ts` is BigInteger, and a Signal millisecond timestamp (~1.78e12)
    overflows INT4. This writes one through the real `MESSAGES.ts` column and
    reads it back unchanged. Were `ts` an `sa.Integer`, the baseline would
    build an int4 column and this append would raise NumericValueOutOfRange."""
    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url))
    magnitude = 1785950108283
    try:
        await store.append(message=NormalizedMessage(
            "signal", "g", "group", "+1", "A", magnitude, "x", {}))
        row = (await store.recent(conversation_id="g", limit=1))[0]
        assert row.ts == magnitude
    finally:
        await store.aclose()


def test_the_only_tables_are_the_declared_ones_and_alembic_version(pg_url):
    """Rollback depends on this: the migrated database holds exactly the tables
    the metadata declares plus Alembic's own `alembic_version`. The expected set
    is derived from `schema.METADATA` rather than hard-coded — a new table lands
    here with no edit, a STRAY table (one not in the metadata) still fails."""
    run_migrations(pg_url)
    engine = sa.create_engine(sync_url_for(pg_url))
    try:
        with engine.connect() as conn:
            names = {
                r[0]
                for r in conn.execute(sa.text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ))
            }
    finally:
        engine.dispose()
    assert names == set(METADATA.tables) | {"alembic_version"}
