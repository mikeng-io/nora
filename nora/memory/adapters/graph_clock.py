"""Read due graph schedules and persist their occurrences in Postgres."""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus, StimulusKind
from nora.domain.ticks import due_tick_of_at, tick_start_ms
from nora.infra.sql.cognitive_graph_schema import CLAIM, GRAPH_STIMULUS, INTENTION
from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory.domain.time_trigger import aware_instant, decode_time_trigger

_I = INTENTION.c
_C = CLAIM.c
_S = GRAPH_STIMULUS.c


class SqlDueSchedules:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def publish_due(self, tick: int) -> None:
        boundary = datetime.fromtimestamp(tick_start_ms(tick) / 1000, UTC)
        async with self._engine.begin() as conn:
            await self._intentions(conn, tick, boundary)
            await self._expiries(conn, boundary)

    async def _intentions(self, conn: AsyncConnection, tick: int, boundary: datetime) -> None:
        stmt = sa.select(INTENTION).where(
            _I.state == "open",
            sa.or_(
                _I.review_time <= boundary, _I.deadline <= boundary,
                _I.drop_when_form == "deadline", _I.delivery_mode == "choose_moment",
            ),
        ).order_by(_I.unit, _I.id).with_for_update(read=True)
        for row in (await conn.execute(stmt)).mappings():
            if row["review_time"] is not None and row["review_time"] <= boundary:
                await _publish(conn, row, "review_time", row["review_time"])
            deadlines = {row["deadline"]}
            if row["drop_when_form"] == "deadline":
                value = row["drop_when"]
                deadlines.add(aware_instant(value.get("at") if isinstance(value, dict) else value))
            for at in sorted(at for at in deadlines if at is not None and at <= boundary):
                await _publish(conn, row, "deadline", at)
            if row["delivery_mode"] == "choose_moment":
                await self._delivery(conn, tick, row)

    async def _delivery(self, conn: AsyncConnection, tick: int, row: RowData) -> None:
        trigger = decode_time_trigger(row["trigger"])
        if trigger is None or due_tick_of_at(trigger.at) > tick:
            return
        # Only published occurrences advance the recurrence, so restart starts at the first gap.
        published = (await conn.execute(sa.select(sa.func.count()).where(
            _S.unit == row["unit"], _S.graph_id == row["id"], _S.kind == "intention_due",
        ))).scalar_one()
        for at in trigger.due_occurrences(tick, published):
            await _publish(conn, row, "intention_due", at)

    async def _expiries(self, conn: AsyncConnection, boundary: datetime) -> None:
        stmt = sa.select(CLAIM).where(
            _C.state == "live", _C.valid_to <= boundary,
            ~sa.exists().where(
                _S.unit == _C.unit, _S.graph_id == _C.id, _S.kind == "valid_to",
            ),
        ).order_by(_C.unit, _C.id).with_for_update(read=True)
        for row in (await conn.execute(stmt)).mappings():
            await _publish(conn, row, "valid_to", row["valid_to"])


async def _publish(
    conn: AsyncConnection, row: RowData, kind: StimulusKind, at: datetime,
) -> None:
    await SqlGraphStimuli.append_in(conn, GraphStimulus(
        row["unit"], kind, row["id"], at, "clock", row["origin_room"],
        row.get("owed_to") or next(iter(row["identities"]), None),
        occurrence=at.astimezone(UTC).isoformat(),
    ))
