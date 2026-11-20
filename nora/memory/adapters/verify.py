"""Read due intention heads and persist clock endings in Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.cognitive_graph_schema import INTENTION
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph
from nora.memory.adapters.condition_sources import ConditionSources, Source
from nora.memory.adapters.intention_lineage import intention_lineages
from nora.memory.adapters.intention_verdicts import record_intention_verdicts
from nora.memory.app.verify import ClockEnding, DueIntention
from nora.memory.domain.conditions import decode_condition
from nora.memory.domain.time_trigger import aware_instant


class SqlIntentionVerification:
    def __init__(self, engine: AsyncEngine, graph: SqlCognitiveGraph) -> None:
        self._engine = engine
        self._graph = graph

    @asynccontextmanager
    async def batch(self) -> AsyncIterator[_Batch]:
        async with self._engine.begin() as conn:
            yield _Batch(conn, self._graph)


class _Batch:
    def __init__(self, conn: AsyncConnection, graph: SqlCognitiveGraph) -> None:
        self._conn = conn
        self._graph = graph
        self._sources = ConditionSources(conn)
        self._intentions: dict[tuple[str, str], RowData] = {}

    async def due_intention_heads(self, now: datetime) -> list[DueIntention]:
        i = INTENTION.c
        stmt = sa.select(INTENTION).where(
            i.state == "open",
            sa.or_(i.deadline <= now, i.drop_when_form == "deadline"),
        ).order_by(i.unit, i.id)
        due = []
        for row in (await self._conn.execute(stmt)).mappings():
            deadline = row["deadline"] if row["form"] == "achieve" else None
            drop = None
            if row["drop_when_form"] == "deadline":
                value = row["drop_when"]
                drop = aware_instant(value.get("at") if isinstance(value, dict) else value)
            drops = drop is not None and (deadline is None or drop <= deadline)
            end = drop if drops else deadline
            if end is not None and end <= now:
                self._intentions[row["unit"], row["id"]] = dict(row)
                due.append(DueIntention(row["unit"], row["id"], end, drops, row["origin_room"]))
        return due

    async def window(self, due: DueIntention) -> tuple[datetime, set[Source]]:
        row = self._intentions[due.unit, due.id]
        i = INTENTION.c
        lineage = intention_lineages(due.unit, sa.select(
            i.id.label("head"), i.replaces, i.asserted_at,
        ).where(i.unit == due.unit, i.id == due.id))
        opened = (await self._conn.execute(sa.select(lineage.c.asserted_at).where(
            lineage.c.replaces.is_(None),
        ))).scalar_one()
        sources: set[Source] = set()
        for role in ("done_when", "drop_when"):
            form = row[f"{role}_form"]
            if form is None or form == "deadline":
                continue
            condition = decode_condition(form, row[role])
            if condition is None:
                return opened, set()
            resolved = await self._sources.resolve(row, condition)
            if not resolved:
                return opened, set()
            sources.update(resolved)
        return opened, sources

    async def end(
        self, due: DueIntention, ending: ClockEnding, *, room_busy: Callable[[], bool],
    ) -> None:
        await self._graph.end_intention_by_clock_in(
            self._conn, self._intentions[due.unit, due.id], ending, ended_at=due.deadline,
            room_busy=room_busy,
        )

    async def record_verdicts(self, clock: Callable[[], datetime]) -> None:
        await record_intention_verdicts(self._conn, self._graph, clock)
