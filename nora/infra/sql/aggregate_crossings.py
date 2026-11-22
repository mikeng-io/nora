"""Publish baseline crossings by their registered aggregate identity."""

from collections.abc import Iterable
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus
from nora.domain.mobility import Stay
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.schema import PRESENCE_STAYS


class SqlAggregateCrossings:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def publish_crossings(self, build: int, crossings: Iterable[Stay]) -> None:
        stays = tuple(crossings)
        if not stays:
            return
        table = PRESENCE_STAYS
        keys = {(s.subject_id, s.circle_id, s.started_ts, s.ended_ts) for s in stays}
        query = sa.select(
            table.c.graph_id, table.c.unit, table.c.person_id, table.c.ended_ts,
        ).where(
            table.c.build_id == build, table.c.state == "live", table.c.graph_id.is_not(None),
            sa.tuple_(table.c.subject_id, table.c.circle_id, table.c.started_ts,
                      table.c.ended_ts).in_(keys),
        )
        async with self._engine.begin() as conn:
            for row in (await conn.execute(query)):
                at = datetime.fromtimestamp(row.ended_ts / 1000, UTC)
                await SqlGraphStimuli.append_in(conn, GraphStimulus(
                    row.unit, "aggregate_crossing", row.graph_id, at, table.name,
                    person=row.person_id, cues=("baseline_crossed",),
                    occurrence=at.isoformat(),
                ))
