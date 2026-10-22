"""Sequence reset executes catalog identifiers safely against real PostgreSQL."""

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.snapshot_at import _reset_sequences


@pytest.mark.asyncio
@pytest.mark.parametrize(("table_name", "column_name", "rows"), [
    ("sequence_reset", "id", [41, 64]),
    ("sequence_reset", 'id") FROM "sequence_reset"; SELECT 1; --', [41, 64]),
    ('sequence"reset', "id", [41, 64]),
    ("sequence_reset", "id", []),
])
async def test_reset_sequences_quotes_catalog_identifiers(
    pg_url: str, table_name: str, column_name: str, rows: list[int],
) -> None:
    table = sa.Table(
        table_name, sa.MetaData(),
        sa.Column(column_name, sa.BigInteger, sa.Identity(), primary_key=True),
        schema="public",
    )
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(table.metadata.create_all)
            if rows:
                await conn.execute(sa.insert(table), [{column_name: value} for value in rows])
            await _reset_sequences(conn, table_name)
            generated = (await conn.execute(
                sa.insert(table).values({}).returning(table.c[column_name]),
            )).scalar_one()
            assert generated == (65 if rows else 1)
            assert (await conn.execute(
                sa.select(sa.func.count()).select_from(table),
            )).scalar_one() == len(rows) + 1
    finally:
        await engine.dispose()
