"""Migration `0071_rollup_builds` on a database that already holds presence
Rollup rows.

The rows the wall-clock rollup wrote become ONE build, `legacy`, current —
so every reader reads what it read before the migration until the analytics
process rebuilds and switches — each carries its lineage and system time,
and each is in `row_history`, so the Rollup as of any later T is restorable.
A database with none gets no build at all (a snapshot's target must be
empty)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import command

from nora.infra.sql import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config, run_migrations


async def _one(engine: Any, sql: str) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text(sql))).all()


async def test_existing_rows_become_the_current_legacy_build_with_history(pg_url: str) -> None:
    engine = create_sql_engine(pg_url)
    try:
        assert await _one(engine, "SELECT id FROM rollup_build") == []  # fresh: no build
        command.downgrade(alembic_config(pg_url), "0070_stimulus_rename")
        async with engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO presence_stays (subject_id, person_id, circle_id, started_ts, "
                "ended_ts, lat, lng, radius_m, fix_count) "
                "VALUES ('a', 'a', 'c1', 1000, 700000, 22.4, 114.1, 50, 5)"
            ))
            await conn.execute(sa.text(
                "INSERT INTO presence_places (circle_id, place_id, name, lat, lng, updated_ms) "
                "VALUES ('c1', 'p1', 'Home', 22.4, 114.1, 1790000000000)"
            ))
        run_migrations(pg_url)
        [(build_id, version, status)] = await _one(
            engine, "SELECT id, code_version, status FROM rollup_build",
        )
        assert (version, status) == ("legacy", "current")
        [(stay_build, lineage)] = await _one(engine, "SELECT build_id, lineage FROM presence_stays")
        assert stay_build == build_id and lineage == [["presence", "a", None]]
        tables = {r[0] for r in await _one(
            engine, "SELECT table_name FROM row_history WHERE op = 'backfill'",
        )}
        assert {"presence_stays", "presence_places", "rollup_build"} <= tables
        [(held,)] = await _one(
            engine, "SELECT recorded_at FROM row_history WHERE table_name = 'presence_places'",
        )
        assert int(held.timestamp() * 1000) == 1790000000000  # dated at its own updated_ms
    finally:
        await engine.dispose()
