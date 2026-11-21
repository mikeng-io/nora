"""Fold and register the current head of a platform observation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.observations import (
    ObservationHead,
    SourceScope,
    StoredObservation,
    fold_observation,
)
from nora.infra.sql.aggregate_writes import content_hash
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph
from nora.infra.sql.item_registration import SqlItemRegistration, epoch_ms
from nora.infra.sql.schema import PLATFORM_OBJECT_STATE, PLATFORM_OBSERVATION

OBS = PLATFORM_OBSERVATION
ST = PLATFORM_OBJECT_STATE
PROJECTION_REV = 1


def scope_columns(scope: SourceScope) -> dict[str, Any]:
    return {
        "platform": scope.platform,
        "account_key": scope.account_key,
        "conversation_id": scope.conversation_id,
        "thread": scope.thread,
    }


def room_where(t: Any, scope: SourceScope) -> Any:
    """`platform` + `conversation_id` + `thread` — the room's boundary, no
    account. A read with an EMPTY `account_key` holds the room's boundary
    (the unit the grounder reads for): `platform` and `conversation_id`
    still name the room, so a scope cannot widen past it."""
    return sa.and_(
        t.c.platform == scope.platform,
        t.c.conversation_id == scope.conversation_id,
        t.c.thread == scope.thread,
    )


def head_scope_where(scope: SourceScope) -> Any:
    if not scope.account_key:
        return room_where(ST, scope)
    t = ST
    return sa.and_(room_where(t, scope), t.c.account_key == scope.account_key)


def _head_to_row(head: ObservationHead) -> dict[str, Any]:
    return {
        "source_state": head.state,
        "applied": head.applied,
        "head_observation_id": head.head_id or None,
        "source_position": head.position,
        "revision_scope": head.scope or None,
        "observed_ms": head.observed_ms,
        "ordering": head.ordering,
        "coverage": dict(head.coverage),
        "projection_rev": PROJECTION_REV,
    }


def row_to_head(row: sa.RowMapping) -> ObservationHead:
    return ObservationHead(
        kind=row["source_kind"],
        object_ref=row["object_ref"],
        state=dict(row["source_state"]),
        applied=int(row["applied"]),
        head_id=int(row["head_observation_id"] or 0),
        position=row["source_position"],
        scope=row["revision_scope"] or "",
        observed_ms=row["observed_ms"],
        ordering=row["ordering"],
        coverage=dict(row["coverage"]),
        account_key=row["account_key"] or "",
        thread=row["thread"] or "",
    )


class SqlObservationHeadWriter:
    """The projection receives the registry used in the observation transaction."""

    def __init__(
        self,
        graph: SqlCognitiveGraph,
        registration: SqlItemRegistration | None,
    ) -> None:
        self._registration = registration
        self._graph = graph

    async def begin_room_write(
        self, conn: AsyncConnection, scope: SourceScope, unit: str | None,
    ) -> None:
        room = f"platform-head:{scope.platform}|{scope.conversation_id}"
        await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
            sa.func.hashtextextended(room, 0),
        )))
        former = (await conn.execute(sa.select(ST).where(
            ST.c.platform == scope.platform,
            ST.c.conversation_id == scope.conversation_id,
            ST.c.unit.is_distinct_from(unit), ST.c.state == "live",
        ).with_for_update())).mappings().all()
        for row in former:
            await self._retire(conn, row)

    async def apply(
        self,
        conn: AsyncConnection,
        stored: StoredObservation,
        *,
        unit: str | None,
    ) -> None:
        obs = stored.observation
        sel = (
            sa.select(ST)
            .where(
                *(ST.c[name] == value for name, value in scope_columns(obs.scope).items()),
                ST.c.unit.is_not_distinct_from(unit),
                ST.c.source_kind == obs.object_kind,
                ST.c.object_ref == obs.object_ref,
            )
            .with_for_update()
        )
        row = (await conn.execute(sel)).mappings().one_or_none()
        if row is None:
            # The loser of a first insert race re-reads the winner under lock.
            first = fold_observation(None, stored)
            first_values = await self._head_values(conn, stored, first)
            ins = await conn.execute(
                pg_insert(ST)
                .values(
                    **scope_columns(obs.scope),
                    source_kind=obs.object_kind,
                    object_ref=obs.object_ref,
                    **first_values,
                )
                .on_conflict_do_nothing(constraint="uq_platform_object_state_object")
                .returning(ST.c.id)
            )
            if ins.scalar_one_or_none() is not None:
                return
            row = (await conn.execute(sel)).mappings().one()
        head = row_to_head(row) if row["state"] == "live" else None
        new_head = fold_observation(head, stored)
        values = await self._head_values(conn, stored, new_head, prior=row)
        await conn.execute(
            sa.update(PLATFORM_OBJECT_STATE).where(ST.c.id == row["id"]).values(**values)
        )

    async def _head_values(
        self,
        conn: AsyncConnection,
        stored: StoredObservation,
        head: ObservationHead,
        prior: sa.RowMapping | None = None,
        sources: Iterable[RowData] = (),
    ) -> dict[str, Any]:
        values = _head_to_row(head)
        if (reg := self._registration) is None:
            return {**values, "updated_at": sa.func.now()}
        obs = stored.observation
        scope = obs.scope
        evidence = (
            (await conn.execute(sa.select(OBS).where(OBS.c.id == stored.id))).mappings().one()
        )
        inputs: list[RowData] = [*sources, evidence]
        if prior is not None and prior["unit"] == evidence["unit"] and prior["state"] == "live":
            inputs.append(prior)
        asserted = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
        return {
            **values,
            "updated_at": asserted,
            **await self._graph.aggregate_planes(
                conn,
                ST,
                unit=evidence["unit"],
                key=(
                    scope.platform,
                    scope.account_key,
                    scope.conversation_id,
                    scope.thread,
                    obs.object_kind,
                    obs.object_ref,
                    evidence["unit"],
                ),
                occurred_at=epoch_ms(head.observed_ms),
                asserted_at=asserted,
                room=scope.conversation_id,
                inputs=inputs,
                persons={p for source in inputs for p in source["persons"]},
                revisions=reg.revisions(),
            ),
        }


    async def restore(
        self, conn: AsyncConnection, stored: StoredObservation,
        head: ObservationHead, inputs: list[RowData],
    ) -> None:
        obs = stored.observation
        values = await self._head_values(conn, stored, head, sources=inputs)
        stmt = pg_insert(ST).values(
            **scope_columns(obs.scope), source_kind=obs.object_kind,
            object_ref=obs.object_ref, **values,
        )
        await conn.execute(stmt.on_conflict_do_update(
            constraint="uq_platform_object_state_object", set_=values,
        ))

    async def retire_uncovered(
        self, conn: AsyncConnection, scope: SourceScope, unit: str | None,
        kept: set[tuple[str, str, str]],
    ) -> None:
        existing = (await conn.execute(sa.select(ST).where(
            head_scope_where(scope), ST.c.unit.is_not_distinct_from(unit), ST.c.state == "live",
        ).with_for_update())).mappings().all()
        for row in existing:
            key = (row["account_key"], row["source_kind"], row["object_ref"])
            if key in kept:
                continue
            await self._retire(conn, row)

    async def _retire(self, conn: AsyncConnection, row: sa.RowMapping) -> None:
        if row["graph_id"] is not None:
            await self._graph.tombstone_in(
                conn, ST, row_id=row["graph_id"], unit=row["unit"], content_hash=content_hash(row),
            )
        else:
            await conn.execute(sa.delete(ST).where(ST.c.id == row["id"]))
