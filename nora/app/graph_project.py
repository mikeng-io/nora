"""Explicit, rate-limited code projection backfill. Never consumes model work."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql import memory_index
from nora.infra.sql.graph_projection import CODE_RELATIONS, ProjectionContext, project_rollups
from nora.infra.sql.schema import (
    LEDGER_COMMITMENT,
    LEDGER_DECISION,
    LEDGER_THREAD,
    MEMORY_EDGE,
    MEMORY_PROJECTION_QUEUE,
    RELATION,
    REMINDERS,
)


async def graph_project(
    engine: AsyncEngine,
    *,
    scope: ProjectionContext,
    apply: bool = False,
    rows_per_second: float = 25,
) -> dict[str, Any]:
    if rows_per_second <= 0:
        raise ValueError("positive rate required")
    engine = engine.execution_options(memory_graph=replace(scope, produce=False))
    counts: dict[str, int] = {}
    sources = [
        *memory_index._sources(),
        ("reminder", REMINDERS.c.id, []),
        ("decision", LEDGER_DECISION.c.id, []),
        ("commitment", LEDGER_COMMITMENT.c.id, []),
        ("thread", LEDGER_THREAD.c.id, []),
    ]
    for kind, key, where in sources:
        last: Any = None
        counts[kind] = 0
        while True:
            async with engine.begin() as conn:
                ids = list(
                    (
                        await conn.execute(
                            sa.select(key)
                            .where(*where, *([] if last is None else [key > last]))
                            .order_by(key)
                            .limit(100)
                        )
                    ).scalars()
                )
            if not ids:
                break
            counts[kind] += len(ids)
            if apply:
                for sid in ids:
                    async with engine.begin() as conn:
                        await memory_index.project(conn, kind, sid)
                    await asyncio.sleep(1 / rows_per_second)
            last = ids[-1]
    async with engine.begin() as conn:
        if apply:
            await memory_index.reproject_episodes(conn)
            await project_rollups(conn)
        unprojected = (
            await conn.execute(sa.select(sa.func.count()).select_from(MEMORY_PROJECTION_QUEUE))
        ).scalar_one()
        graph = {
            r[0]: r[1]
            for r in (
                (
                    await conn.execute(
                        sa.select(RELATION.c.rel, sa.func.count())
                        .where(RELATION.c.rel.in_(CODE_RELATIONS), RELATION.c.status == "current")
                        .group_by(RELATION.c.rel)
                    )
                ).all()
            )
        }
        index = {
            r[0]: r[1]
            for r in (
                (
                    await conn.execute(
                        sa.select(MEMORY_EDGE.c.rel, sa.func.count())
                        .where(MEMORY_EDGE.c.rel.in_(CODE_RELATIONS))
                        .group_by(MEMORY_EDGE.c.rel)
                    )
                ).all()
            )
        }
    return {
        "applied": apply,
        "sources": counts,
        "unprojected": unprojected,
        "relations": {
            r: {
                "graph": graph.get(r, 0),
                "memory_edge": index.get(r, 0),
                "difference": graph.get(r, 0) - index.get(r, 0),
            }
            for r in CODE_RELATIONS
        },
    }
