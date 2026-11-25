"""Read current watches and write their outcomes in the same transaction."""

from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION, WATCH_RESULT
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import SqlItemRegistration
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.memory.app.watches import WatchAttempt, WatchKey
from nora.memory.domain.admission_proposals import SourceReference


class SqlWatchResults:
    def __init__(
        self,
        engine: AsyncEngine,
        registration: SqlItemRegistration,
        kinds: Collection[str],
    ) -> None:
        self._engine = engine
        self._registration = registration
        self._health = SqlSourceHealthLog(engine, registration=registration)
        self._kinds = tuple(kinds)

    @asynccontextmanager
    async def next(self, after: WatchKey | None) -> AsyncIterator[WatchAttempt | None]:
        e, i, target = EDGE.c, INTENTION.c, ENTITY.c
        order = (e.unit, e.id)
        stmt = (
            sa.select(EDGE, target.source_ref_kind, target.source_ref_value)
            .select_from(
                INTENTION.join(EDGE, (e.src_id == i.id) & (e.unit == i.unit)).join(
                    ENTITY, (target.id == e.dst_id) & (target.unit == e.unit)
                )
            )
            .where(
                e.type == "watches",
                e.state == "live",
                i.state == "open",
                i.form == "maintain",
                target.state == "admitted",
                target.source_ref_kind.in_(self._kinds),
            )
            .order_by(*order)
            .limit(1)
            .with_for_update(of=(INTENTION, EDGE), skip_locked=True)
        )
        async with self._engine.begin() as conn:
            row = None
            if after is not None:
                row = (
                    (
                        await conn.execute(
                            stmt.where(sa.tuple_(*order) > sa.tuple_(*after)),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                row = (await conn.execute(stmt)).mappings().one_or_none()
            yield _Attempt(conn, self._registration, self._health, row) if row is not None else None


class _Attempt:
    def __init__(
        self,
        conn: AsyncConnection,
        registration: SqlItemRegistration,
        health: SqlSourceHealthLog,
        row: sa.RowMapping,
    ) -> None:
        self._conn = conn
        self._registration = registration
        self._health = health
        self._row = row

    @property
    def key(self) -> WatchKey:
        return self._row["unit"], self._row["id"]

    @property
    def reference(self) -> SourceReference:
        return SourceReference(self._row["source_ref_kind"], self._row["source_ref_value"])

    async def record(self, digest: str | None, at: datetime) -> None:
        unit, edge = self.key
        row = self._row
        scope = {
            name: row[name]
            for name in ("origin_room", "identities", "excluded_identities", "lineage")
        }
        scope["exposure"] = "operator" if row["exposure"] == "operator" else "internal"
        if digest is None:
            await self._health.record_in(
                self._conn,
                source=f"watch.{self.reference.kind}",
                scope=row["dst_id"],
                event="poll",
                outcome="error",
                at_ms=int(at.timestamp() * 1000),
                unit=unit,
                silent_scope=scope,
            )
            return
        result = WATCH_RESULT.c
        last = await self._conn.scalar(
            sa.select(result.content_hash)
            .where(
                result.unit == unit,
                result.watch_edge == edge,
            )
            .order_by(result.id.desc())
            .limit(1)
        )
        if last == digest:
            return
        planes = await self._registration.planes(
            self._conn,
            WATCH_RESULT,
            unit=unit,
            occurred_at=at,
            own=True,
        )
        planes.update(scope)
        await self._conn.execute(
            sa.insert(WATCH_RESULT).values(
                **planes,
                watch_edge=edge,
                content_hash=digest,
            )
        )
        await SqlGraphStimuli.append_in(
            self._conn,
            GraphStimulus(
                unit,
                "watched_source_changed",
                planes["graph_id"],
                at,
                WATCH_RESULT.name,
                room=row["origin_room"],
            ),
        )
