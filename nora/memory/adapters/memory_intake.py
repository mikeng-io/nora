"""Record a person's forget request or dispute in one scoped transaction."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import CLAIM, DISPUTE, FORGET_REQUEST, REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph, content_digest, dependent_claims
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import SqlItemRegistration
from nora.infra.sql.item_retention import tombstone_items
from nora.infra.sql.schema import METADATA
from nora.memory.adapters.admission_rows import GRAPH_TABLES
from nora.memory.domain.registration import (
    AGGREGATE_FAMILIES,
    ITEM_SOURCES,
    PROTECTED_ITEM_SOURCES,
)


@dataclass(frozen=True, slots=True)
class BlockedTarget:
    graph_id: str
    table: str
    reason: Literal["protected_source", "foreign_key", "missing_source"]


@dataclass(frozen=True, slots=True)
class ForgetResult:
    graph_id: str
    tombstoned: tuple[str, ...]
    blocked: tuple[BlockedTarget, ...]


class SqlMemoryIntake:
    def __init__(
        self, engine: AsyncEngine, registration: SqlItemRegistration, graph: SqlCognitiveGraph,
    ) -> None:
        self._engine = engine
        self._registration = registration
        self._graph = graph

    async def claim_persons(self, *, unit: str, claim_id: str) -> tuple[str, ...] | None:
        """Who a claim of this unit is about, or `None` when the unit holds no such claim."""
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(CLAIM.c.persons).where(
                CLAIM.c.unit == unit, CLAIM.c.id == claim_id,
            ))).first()
        return None if row is None else tuple(row.persons or ())

    async def forget(
        self, *, unit: str, author: str, targets: Sequence[str], at: datetime,
        delete_now: bool = False,
    ) -> ForgetResult:
        targets = tuple(sorted(set(targets)))
        tombstoned: list[str] = []
        blocked: list[BlockedTarget] = []
        async with self._engine.begin() as conn:
            planes = await self._registration.planes(
                conn, FORGET_REQUEST, unit=unit, occurred_at=at, persons=(author,),
            )
            await conn.execute(sa.insert(FORGET_REQUEST).values(
                **planes, author=author, targets=list(targets), delete_now=delete_now,
            ))
            graph_id: str = planes["graph_id"]
            await conn.execute(sa.update(CLAIM).where(
                CLAIM.c.unit == unit, CLAIM.c.id.in_(targets), sa.any_(CLAIM.c.persons) == author,
                CLAIM.c.state != "tombstoned",
            ).values(state="hidden"))
            if delete_now:
                # ponytail: a blocked target waits for its blocker to go.
                # The blocked list is durable. There is no retry sweep.
                for target in targets:
                    failure = await self._try_delete(conn, unit, target)
                    if failure is None:
                        tombstoned.append(target)
                    else:
                        blocked.append(failure)
                await conn.execute(sa.update(FORGET_REQUEST).where(
                    FORGET_REQUEST.c.graph_id == graph_id, FORGET_REQUEST.c.unit == unit,
                ).values(blocked=[asdict(target) for target in blocked]))
        return ForgetResult(graph_id, tuple(tombstoned), tuple(blocked))

    async def dispute(
        self, *, unit: str, author: str, claim_id: str, at: datetime,
    ) -> str:
        async with self._engine.begin() as conn:
            claim = (await conn.execute(sa.select(
                CLAIM.c.persons, CLAIM.c.state, CLAIM.c.origin_room,
            ).where(CLAIM.c.unit == unit, CLAIM.c.id == claim_id).with_for_update()
            )).mappings().one()
            planes = await self._registration.planes(
                conn, DISPUTE, unit=unit, occurred_at=at, persons=(author,),
                room=claim["origin_room"],
            )
            await conn.execute(sa.insert(DISPUTE).values(
                **planes, author=author, claim_id=claim_id,
            ))
            dispute_id: str = planes["graph_id"]
            if claim["state"] not in {"hidden", "tombstoned"}:
                await conn.execute(sa.update(CLAIM).where(
                    CLAIM.c.id == claim_id, CLAIM.c.unit == unit,
                ).values(state="hidden" if author in claim["persons"] else "awaiting_weighing"))
            if dependents := await dependent_claims(conn, unit, claim_id):
                await SqlGraphStimuli.append_in(conn, GraphStimulus(
                    unit, "dependency", claim_id, at, "dispute",
                    room=claim["origin_room"], revision_id=dispute_id,
                    dependents=dependents, occurrence=dispute_id,
                ))
        return dispute_id

    async def _try_delete(
        self, conn: AsyncConnection, unit: str, graph_id: str,
    ) -> BlockedTarget | None:
        try:
            async with conn.begin_nested():
                return await self._delete(conn, unit, graph_id)
        except sa.exc.IntegrityError as exc:
            driver = getattr(exc.orig, "__cause__", None) or exc.orig
            table = getattr(driver, "table_name", None) or getattr(
                getattr(driver, "diag", None), "table_name", None,
            )
            if getattr(exc.orig, "sqlstate", None) != "23503" or not table:
                raise
            return BlockedTarget(graph_id, table, "foreign_key")

    async def _delete(
        self, conn: AsyncConnection, unit: str, graph_id: str,
    ) -> BlockedTarget | None:
        registry = (await conn.execute(sa.select(REGISTRY.c.kind, REGISTRY.c.state).where(
            REGISTRY.c.id == graph_id, REGISTRY.c.unit == unit,
        ))).one_or_none()
        if registry is None:
            return BlockedTarget(graph_id, REGISTRY.name, "missing_source")
        if registry.state == "tombstoned":
            return None
        if registry.kind in {"item", "aggregate"}:
            sources = ITEM_SOURCES if registry.kind == "item" else AGGREGATE_FAMILIES
            for name in sources:
                table = METADATA.tables[name]
                where = table.c.unit == unit, table.c.graph_id == graph_id
                if name in PROTECTED_ITEM_SOURCES:
                    if await conn.scalar(sa.select(sa.exists().where(*where))):
                        return BlockedTarget(graph_id, name, "protected_source")
                    continue
                if await tombstone_items(conn, table, *where):
                    return None
            return BlockedTarget(graph_id, REGISTRY.name, "missing_source")
        table = GRAPH_TABLES[registry.kind]
        row = (await conn.execute(sa.select(table).where(
            table.c.id == graph_id, table.c.unit == unit,
        ).with_for_update())).mappings().one()
        await self._graph.tombstone_in(
            conn, table, row_id=graph_id, unit=unit, content_hash=content_digest(table.name, row),
        )
        return None
