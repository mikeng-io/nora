"""Postgres storage for per-unit Cognitive Graph stimuli."""

from dataclasses import asdict
from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus, StimulusKind
from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS


class SqlGraphStimuli:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, stimulus: GraphStimulus) -> None:
        async with self._engine.begin() as conn:
            await self.append_in(conn, stimulus)

    @staticmethod
    async def append_in(conn: AsyncConnection, stimulus: GraphStimulus) -> None:
        await conn.execute(
            insert(GRAPH_STIMULUS).values(**asdict(stimulus))
            .on_conflict_do_nothing(constraint="pk_graph_stimulus"),
        )

    async def read(self, unit: str) -> tuple[GraphStimulus, ...]:
        table = GRAPH_STIMULUS
        stmt = sa.select(table).where(table.c.unit == unit).order_by(
            table.c.eligible_at, table.c.kind, table.c.graph_id, table.c.occurrence,
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return tuple(
            GraphStimulus(
                row["unit"], cast(StimulusKind, row["kind"]), row["graph_id"],
                row["eligible_at"], row["source"], row["room"], row["person"],
                tuple(row["cues"]), row["revision_id"], tuple(row["dependents"]), row["occurrence"],
            )
            for row in rows
        )
