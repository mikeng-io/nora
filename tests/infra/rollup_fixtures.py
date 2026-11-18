"""Registered aggregate rows for tests of existing Rollup consumers."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.cognitive_graph_schema import REGISTRY


async def insert_rollup_row(conn: AsyncConnection, table: sa.Table, **row) -> None:
    graph_id = f"generated:{table.name}:{uuid4()}"
    await conn.execute(sa.insert(REGISTRY).values(id=graph_id, kind="aggregate", unit=row["unit"]))
    await conn.execute(sa.insert(table).values(graph_id=graph_id, **row))
