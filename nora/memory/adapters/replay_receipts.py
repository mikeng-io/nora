"""One work list for replay and its coverage, scoped like graph retrieval."""

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import FromClause

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.domain.lineage import room_key
from nora.engagement.slot_keys import resolve_slot_room
from nora.infra.sql.cognitive_graph_schema import GRAPH_REPLAY_RECEIPT
from nora.memory.adapters.retrieval_rows import graph_rows
from nora.memory.adapters.retrieval_scope import RetrievalAccess, scoped_rows
from nora.memory.domain.registration import ITEM_SOURCES
from nora.memory.domain.retrieval import ReadingRoom, RetrievalUnavailableError
from nora.memory.domain.wake import NotObservable, ReplayCoverage, ReplayItem
from nora.policy import Policy


class SqlReplayReceipts:
    def __init__(
        self, engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy, policy: Policy,
    ) -> None:
        self._engine = engine
        self._authority = RetrievalAccess(access, lineage)
        self._policy = policy

    @asynccontextmanager
    async def unit_lock(self, unit: str) -> AsyncIterator[None]:
        async with self._engine.begin() as conn:
            await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtextextended(f"graph-replay:{unit}", 0),
            )))
            yield

    def _room(self, unit: str, origin: str | None) -> tuple[ReadingRoom, str] | None:
        room = (
            self._policy.home_rooms().get(unit) if origin is None else
            resolve_slot_room(self._policy.rooms(), origin, unit=unit)
        )
        if room is None or not room.absorbed:
            return None
        return ReadingRoom(room.platform, room.primary_id), room_key(room.platform, room.primary_id)

    async def coverage(self, unit: str, until: datetime) -> ReplayCoverage:
        items = graph_rows(unit, None, sources=tuple(ITEM_SOURCES))
        receipt = GRAPH_REPLAY_RECEIPT.c
        receipted = sa.select(
            sa.func.jsonb_object_keys(receipt.inputs).label("graph_id"),
        ).where(receipt.unit == unit).cte("receipted")
        row = items.c
        statement = sa.select(
            row.graph_id, row.source, row.event_time, row.origin_room, row.exposure,
        ).where(
            row.event_time <= until,
            ~sa.exists(sa.select(1).where(receipted.c.graph_id == row.graph_id)),
        ).order_by(row.event_time, row.graph_id)

        async with self._engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="REPEATABLE READ")
            pending: dict[str, tuple[ReplayItem, str]] = {}
            not_observable: dict[str, NotObservable] = {}
            by_room: dict[ReadingRoom, set[str]] = defaultdict(set)
            for id_, source, clock, origin, exposure in await conn.execute(statement):
                if exposure == "operator":
                    not_observable[id_] = "operator_exposure"
                    continue
                room = self._room(unit, origin)
                if room is None:
                    not_observable[id_] = (
                        "no_home_room" if origin is None else "outside_reading_scope"
                    )
                    continue
                reading, key = room
                by_room[reading].add(id_)
                pending[id_] = (ReplayItem(id_, source, clock, origin), key)
            for reading, ids in by_room.items():
                target, scope, dm_rooms = await self._authority.authority(
                    reading, frozenset(), None,
                )
                if scope.unit != unit:
                    raise RetrievalUnavailableError("reading_room_unavailable")
                social_off = await self._authority.social_opt_outs(conn, scope)
                allowed: FromClause = await scoped_rows(
                    conn, replace(scope, social_off=social_off), target, dm_rooms,
                    ceiling=until, rows=items,
                )
                visible = set(await conn.scalars(sa.select(allowed.c.graph_id).where(
                    allowed.c.graph_id.in_(ids),
                )))
                for id_ in ids - visible:
                    not_observable[id_] = "outside_reading_scope"
                    del pending[id_]
        return ReplayCoverage(tuple(pending.values()), not_observable)

    async def record(
        self, unit: str, pass_id: str, clock: datetime, inputs: dict[str, str],
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(GRAPH_REPLAY_RECEIPT).values(
                unit=unit, pass_id=pass_id, clock=clock, inputs=inputs,
            ))
