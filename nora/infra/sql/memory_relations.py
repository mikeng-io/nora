"""The unit's full graph relation set, read the production way.

`SqlMemoryGraph.walk` answers a scoped subgraph; nothing else returns
every live relation a unit holds. This is that read — the store's own
`relation` table mapped through `relation_row`, the same row mapping
the graph store uses — for readers that need the complete set (the
Cognition-loop evaluator's state snapshot).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memory_graph import Relation
from nora.infra.sql.memory_graph import relation_row
from nora.infra.sql.schema import RELATION


async def unit_relations(engine: AsyncEngine, *, unit: str) -> list[Relation]:
    """Every relation the unit's graph holds, oldest first."""
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(RELATION).where(RELATION.c.unit == unit)
            .order_by(RELATION.c.asserted_at, RELATION.c.id)
        )).mappings().all()
    return [relation_row(row) for row in rows]
