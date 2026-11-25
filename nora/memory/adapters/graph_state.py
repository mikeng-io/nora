"""Whole-unit Cognitive Graph state reads for evaluation snapshots.

The subject-facing retrieval surface is deliberately scoped and bounded. The
cognition-loop evaluator needs the other cut: one unit's complete typed edge
set and its admitted entity anchors, read from the graph's own tables so a
before/after diff can name what changed. These reads expose no broad subject
tool; callers hold the unit boundary explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY


async def unit_edges(engine: AsyncEngine, *, unit: str) -> list[Mapping[str, Any]]:
    """Every typed edge row the unit holds, oldest first."""
    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(EDGE).where(EDGE.c.unit == unit)
            .order_by(EDGE.c.asserted_at, EDGE.c.id)
        )).mappings().all()
    return [dict(row) for row in rows]


async def entity_anchor_aliases(engine: AsyncEngine, *, unit: str) -> Mapping[str, str]:
    """Casefolded names and aliases of admitted entities, to their graph id.

    `design/cognition/graph.md` makes an entity the neutral anchor; canonical
    identity, room, unit and Nora rows are pointer entities rather than a
    separate anchor store. The graph's answer to the legacy alias question is
    therefore the admitted entity row's `name` plus `aliases`, resolving to
    that entity's graph id.
    """
    row = ENTITY.c
    async with engine.connect() as conn:
        rows = await conn.execute(
            sa.select(row.id, row.name, row.aliases).where(
                row.unit == unit, row.state == "admitted",
            ).order_by(row.id)
        )
    aliases: dict[str, str] = {}
    for entity_id, name, extra in rows:
        for label in (name, *(extra or ())):
            if label:
                aliases[str(label).casefold()] = str(entity_id)
    return aliases
