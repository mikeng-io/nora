"""Postgres memory graph. Each public operation owns one transaction.

Ids are globally unique: generated entities use UUIDs; code projections use
UUID5 over unit/kind/source, and identity pointers use pointer_entity. An
attempt to reuse an id for a different kind or unit is rejected, never conflated.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.memory_admission import AdmissionState
from nora.domain.memory_graph import (
    ENTITY_KINDS,
    Admission,
    Correction,
    Entity,
    Evidence,
    Key,
    Relation,
    Source,
    Subgraph,
    Walk,
    WorkItem,
)
from nora.infra.sql.schema import (
    ENTITY,
    MEMORY_GRAPH_REFUSAL,
    MEMORY_WORK_ITEM,
    RELATION,
    RELATION_EVIDENCE,
)

# Each branch probes an endpoint index. Scope is applied inside each probe,
# including the seed probe. Record relations establish reachability only.
WALK_SQL = """
WITH RECURSIVE paths(kind, id, depth, seen, edge) AS (
 SELECT s.kind, s.id, 0, ARRAY[s.kind || ':' || s.id], NULL::text
 FROM unnest(CAST(:kinds AS text[]), CAST(:ids AS text[])) AS s(kind,id)
 WHERE (NOT EXISTS (SELECT 1 FROM entity e WHERE e.kind=s.kind AND e.id=s.id)
        OR EXISTS (SELECT 1 FROM entity e WHERE e.kind=s.kind AND e.id=s.id AND e.unit=:unit))
 AND EXISTS (
   SELECT 1 FROM relation r WHERE
     ((r.src_kind=s.kind AND r.src_id=s.id) OR (r.dst_kind=s.kind AND r.dst_id=s.id))
     AND r.unit=:unit AND r.persons <@ CAST(:persons AS text[])
     AND r.status='current' AND r.layer=ANY(CAST(:layers AS text[]))
     AND (cardinality(CAST(:rels AS text[]))=0 OR r.rel=ANY(CAST(:rels AS text[])))
     AND (CAST(:at AS timestamptz) IS NULL OR
       ((r.valid_from IS NULL OR r.valid_from<=:at) AND (r.valid_to IS NULL OR :at<r.valid_to)))
 )
 UNION ALL
 SELECT r.other_kind, r.other_id, p.depth+1,
        p.seen || (r.other_kind || ':' || r.other_id), r.id
 FROM paths p CROSS JOIN LATERAL (
   SELECT ranked.* FROM (
     SELECT adjacent.*, row_number() OVER (PARTITION BY rel ORDER BY id) AS ordinal
     FROM (
       SELECT r.*, r.dst_kind AS other_kind, r.dst_id AS other_id FROM relation r
       WHERE r.src_kind=p.kind AND r.src_id=p.id
         AND r.unit=:unit AND r.persons <@ CAST(:persons AS text[])
       UNION ALL
       SELECT r.*, r.src_kind AS other_kind, r.src_id AS other_id FROM relation r
       WHERE r.dst_kind=p.kind AND r.dst_id=p.id
         AND r.unit=:unit AND r.persons <@ CAST(:persons AS text[])
     ) adjacent
     WHERE status='current' AND layer!='record' AND layer=ANY(CAST(:layers AS text[]))
       AND (cardinality(CAST(:rels AS text[]))=0 OR rel=ANY(CAST(:rels AS text[])))
       AND (CAST(:at AS timestamptz) IS NULL OR
         ((valid_from IS NULL OR valid_from<=:at) AND (valid_to IS NULL OR :at<valid_to)))
       AND (NOT EXISTS (SELECT 1 FROM entity e WHERE e.kind=other_kind AND e.id=other_id)
         OR EXISTS (SELECT 1 FROM entity e WHERE e.kind=other_kind AND e.id=other_id
             AND e.unit=:unit))
   ) ranked WHERE ordinal<=:fan
 ) r
 WHERE p.depth<=:depth
   AND NOT (r.other_kind || ':' || r.other_id)=ANY(p.seen)
)
SELECT * FROM paths LIMIT :budget
"""


def walk_parameters(seeds: tuple[Key, ...], query: Walk) -> dict[str, Any]:
    return {
        "kinds": [k for k, _ in seeds],
        "ids": [i for _, i in seeds],
        "unit": query.unit,
        "persons": sorted(query.persons),
        "layers": list(query.layers),
        "rels": list(query.relations),
        "at": query.valid_at,
        "fan": query.fan_out + 1,
        "depth": query.depth,
        "budget": max(1, query.nodes * (query.fan_out + 1) + len(seeds)),
    }


def entity_row(row: Mapping[Any, Any]) -> Entity:
    values = dict(row)
    values["aliases"] = tuple(values["aliases"])
    values["created_at"] = None
    attrs = dict(values["attributes"])
    if "location" in attrs:
        attrs["location"] = tuple(attrs["location"])
    for key in ("start", "end"):
        if isinstance(attrs.get(key), str):
            attrs[key] = datetime.fromisoformat(attrs[key])
    values["attributes"] = attrs
    return Entity(**values)


def relation_row(row: Mapping[Any, Any]) -> Relation:
    values = dict(row)
    values["src"] = (values.pop("src_kind"), values.pop("src_id"))
    values["dst"] = (values.pop("dst_kind"), values.pop("dst_id"))
    values["persons"] = tuple(values["persons"])
    values["created_at"] = None
    return Relation(**values)


def relation_values(relation: Relation) -> dict[str, Any]:
    values = asdict(relation)
    values.pop("src")
    values.pop("dst")
    values.update(
        src_kind=relation.src[0],
        src_id=relation.src[1],
        dst_kind=relation.dst[0],
        dst_id=relation.dst[1],
    )
    if values["created_at"] is None:
        values.pop("created_at")
    return values


def work_row(row: Mapping[Any, Any]) -> WorkItem:
    values = dict(row)
    values["source"] = (values.pop("source_kind"), values.pop("source_id"))
    return WorkItem(**values)


async def upsert_entity(conn: AsyncConnection, entity: Entity) -> Entity:
    if entity.kind not in ENTITY_KINDS:
        raise ValueError("unknown entity kind")
    values = asdict(entity)
    values["attributes"] = json.loads(
        json.dumps(values["attributes"], default=lambda v: v.isoformat())
    )
    if values["created_at"] is None:
        values.pop("created_at")
    stmt = insert(ENTITY).values(**values)
    result = (
        await conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[ENTITY.c.id],
                set_={
                    k: getattr(stmt.excluded, k) for k in values if k not in ("id", "created_at")
                },
                where=sa.and_(ENTITY.c.unit == entity.unit, ENTITY.c.kind == entity.kind),
            ).returning(ENTITY.c.id)
        )
    ).scalar_one_or_none()
    if result is None:
        raise ValueError("an entity's unit and kind are fixed; use a globally unique id")
    return entity


async def enqueue(conn: AsyncConnection, item: WorkItem) -> WorkItem:
    values = asdict(item)
    values.pop("source")
    values.update(
        source_kind=item.source[0],
        source_id=item.source[1],
        state="pending",
        attempts=0,
        last_error=None,
    )
    if values["due_at"] is None:
        values["due_at"] = item.as_of
    for key in ("created_at", "updated_at", "due_at"):
        if values[key] is None:
            values.pop(key)
    stmt = insert(MEMORY_WORK_ITEM).values(**values)
    # Returning the existing row, without resetting a running/settled attempt.
    returning = stmt.on_conflict_do_update(
        constraint="memory_work_item_input_key",
        set_={"id": MEMORY_WORK_ITEM.c.id},
    ).returning(MEMORY_WORK_ITEM)
    return work_row((await conn.execute(returning)).mappings().one())


class SqlMemoryGraph:
    def __init__(
        self, engine: AsyncEngine, *, metrics: Any = None, statement_timeout_ms: int = 1000
    ) -> None:
        if statement_timeout_ms <= 0:
            raise ValueError("positive statement timeout required")
        self.engine = engine
        self.metrics = metrics
        self.statement_timeout_ms = statement_timeout_ms

    async def upsert_entity(self, entity: Entity) -> Entity:
        async with self.engine.begin() as conn:
            return await upsert_entity(conn, entity)

    async def stage_and_admit(
        self,
        *,
        entities: tuple[Entity, ...],
        relations: tuple[Relation, ...],
        evidence: tuple[Evidence, ...],
        sources: Mapping[Key, Source],
        corrections: tuple[Correction, ...] = (),
    ) -> Admission:
        async with self.engine.begin() as conn:
            # ponytail: serialize admission globally; use per-unit advisory locks
            # if concurrent sessions contend. Prevent duplicate-name/endpoint races.
            await conn.execute(sa.text("LOCK TABLE entity, relation IN SHARE ROW EXCLUSIVE MODE"))
            state = AdmissionState()
            units = {e.unit for e in entities} | {r.unit for r in relations}
            old = list(
                (
                    await conn.execute(
                        sa.select(RELATION).where(
                            sa.or_(
                                RELATION.c.id.in_([c.relation_id for c in corrections]),
                                RELATION.c.id.in_([r.id for r in relations]),
                            )
                        )
                    )
                ).mappings()
            )
            units.update(r["unit"] for r in old)
            for row in (
                await conn.execute(
                    sa.select(ENTITY).where(
                        sa.or_(ENTITY.c.unit.in_(units), ENTITY.c.id.in_([e.id for e in entities]))
                    )
                )
            ).mappings():
                e = entity_row(row)
                state.entities[e.id] = e
            state.relations = {r["id"]: relation_row(r) for r in old}
            admitted = await state._admit(
                entities=entities,
                relations=relations,
                evidence=evidence,
                sources=sources,
                corrections=corrections,
            )
            for e in admitted.entities:
                await upsert_entity(conn, e)
            for r in admitted.relations:
                await conn.execute(insert(RELATION).values(**relation_values(r)))
            for c in admitted.corrections:
                r = state.relations[c.relation_id]
                values = relation_values(r)
                values.pop("id")
                await conn.execute(
                    sa.update(RELATION).where(RELATION.c.id == r.id).values(**values)
                )
            for ev in admitted.evidence:
                values = asdict(ev)
                values.pop("source")
                values.update(source_kind=ev.source[0], source_id=ev.source[1])
                exists = sa.exists().where(
                    *(
                        getattr(RELATION_EVIDENCE.c, k).is_not_distinct_from(v)
                        for k, v in values.items()
                    )
                )
                await conn.execute(
                    insert(RELATION_EVIDENCE).from_select(
                        list(values),
                        sa.select(
                            *(
                                sa.literal(v, type_=getattr(RELATION_EVIDENCE.c, k).type)
                                for k, v in values.items()
                            )
                        ).where(~exists),
                    )
                )
            for refusal in admitted.refused:
                await conn.execute(
                    insert(MEMORY_GRAPH_REFUSAL).values(
                        kind=refusal.kind, part_id=refusal.id, reason=refusal.reason
                    )
                )
            return admitted

    async def aliases(self, *, unit: str) -> Mapping[str, Key]:
        async with self.engine.begin() as conn:
            rows = (
                await conn.execute(
                    sa.select(ENTITY)
                    .where(
                        ENTITY.c.unit == unit,
                        ENTITY.c.state == "live",
                        ENTITY.c.merged_into.is_(None),
                    )
                    .order_by(ENTITY.c.id)
                )
            ).mappings()
            return {
                name.casefold(): (r["kind"], r["id"])
                for r in rows
                for name in (r["name"], *r["aliases"])
            }

    async def candidates(
        self,
        *,
        unit: str,
        kind: str,
        name: str,
        near: Key | None = None,
        window: tuple[datetime, datetime] | None = None,
        limit: int = 5,
    ) -> tuple[Entity, ...]:
        async with self.engine.begin() as conn:
            anchor = (
                (
                    await conn.execute(
                        sa.select(ENTITY).where(
                            ENTITY.c.unit == unit, ENTITY.c.kind == near[0], ENTITY.c.id == near[1]
                        )
                    )
                )
                .mappings()
                .first()
                if near
                else None
            )
            location = anchor["attributes"].get("location") if anchor else None
            exact = sa.or_(
                sa.func.lower(ENTITY.c.name) == name.casefold(),
                sa.text("EXISTS (SELECT 1 FROM unnest(entity.aliases) a WHERE lower(a)=:alias)"),
            )
            lexical = sa.or_(ENTITY.c.name.op("%")(name), exact)
            rows = (
                (
                    await conn.execute(
                        sa.select(
                            ENTITY,
                            exact.label("exact"),
                            sa.func.similarity(ENTITY.c.name, name).label("score"),
                        ).where(
                            ENTITY.c.unit == unit,
                            ENTITY.c.kind == kind,
                            ENTITY.c.state == "live",
                            ENTITY.c.merged_into.is_(None),
                            sa.or_(
                                lexical,
                                sa.literal(kind == "place" and location is not None),
                                sa.literal(kind == "event" and window is not None),
                            ),
                        ),
                        {"alias": name.casefold()},
                    )
                )
                .mappings()
                .all()
            )

            def rank(r: Mapping[Any, Any]) -> tuple[Any, ...]:
                attrs = r["attributes"]
                point = attrs.get("location")
                distance = (
                    sum((float(a) - float(b)) ** 2 for a, b in zip(location, point, strict=True))
                    if location is not None and point is not None
                    else float("inf")
                )
                start, end = attrs.get("start"), attrs.get("end")
                overlap = bool(
                    window
                    and start
                    and end
                    and datetime.fromisoformat(start) <= window[1]
                    and datetime.fromisoformat(end) >= window[0]
                )
                return (not r["exact"], distance, not overlap, -r["score"], r["id"])

            return tuple(
                entity_row({column.name: r[column.name] for column in ENTITY.c})
                for r in sorted(rows, key=rank)[: max(0, min(limit, 5))]
            )

    async def walk(self, seeds: tuple[Key, ...], query: Walk) -> Subgraph:
        started = monotonic()
        result = Subgraph("unavailable")
        depth = 0
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    sa.text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}"),
                )
                params = walk_parameters(seeds, query)
                paths = (await conn.execute(sa.text(WALK_SQL), params)).mappings().all()
                state = AdmissionState()
                ids = {p["edge"] for p in paths if p["edge"] is not None}
                depth = min(query.depth, max((p["depth"] for p in paths), default=0))
                # Include record pointers only for seed reachability; they never expand.
                seed_edges = (
                    sa.and_(
                        RELATION.c.layer == "record",
                        sa.or_(
                            *(
                                sa.or_(
                                    sa.and_(RELATION.c.src_kind == k, RELATION.c.src_id == i),
                                    sa.and_(RELATION.c.dst_kind == k, RELATION.c.dst_id == i),
                                )
                                for k, i in seeds
                            )
                        ),
                    )
                    if seeds
                    else sa.false()
                )
                for row in (
                    await conn.execute(
                        sa.select(RELATION).where(
                            RELATION.c.unit == query.unit,
                            RELATION.c.persons.contained_by(sorted(query.persons)),
                            sa.or_(RELATION.c.id.in_(ids), seed_edges),
                        )
                    )
                ).mappings():
                    state.relations[row["id"]] = relation_row(row)
                keys = {(p["kind"], p["id"]) for p in paths}
                for row in (
                    await conn.execute(
                        sa.select(ENTITY).where(
                            ENTITY.c.unit == query.unit, ENTITY.c.id.in_([i for _, i in keys])
                        )
                    )
                ).mappings():
                    entity = entity_row(row)
                    state.entities[entity.id] = entity
                for row in (
                    await conn.execute(
                        sa.select(RELATION_EVIDENCE).where(
                            sa.or_(
                                RELATION_EVIDENCE.c.relation_id.in_(ids),
                                RELATION_EVIDENCE.c.entity_id.in_([i for _, i in keys]),
                            )
                        )
                    )
                ).mappings():
                    values = dict(row)
                    values.pop("id")
                    values["source"] = (values.pop("source_kind"), values.pop("source_id"))
                    state.evidence.append(Evidence(**values))
                result = await state._walk(seeds, query)
                if len(paths) >= params["budget"]:
                    result = Subgraph("partial", result.nodes, result.relations)
        except (SQLAlchemyError, OSError, TimeoutError):
            result = Subgraph("unavailable")
        finally:
            if self.metrics is not None:
                self.metrics.observe("nora_memory_graph_walk_seconds", monotonic() - started)
                self.metrics.observe("nora_memory_graph_walk_nodes", len(result.nodes))
                self.metrics.observe("nora_memory_graph_walk_depth", depth)
        return result

    async def enqueue(self, item: WorkItem) -> WorkItem:
        async with self.engine.begin() as conn:
            return await enqueue(conn, item)

    async def claim(
        self, *, historical: bool, now: datetime, lease_seconds: float
    ) -> WorkItem | None:
        if lease_seconds <= 0:
            raise ValueError("positive lease required")
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(MEMORY_WORK_ITEM)
                .where(
                    MEMORY_WORK_ITEM.c.historical == historical,
                    MEMORY_WORK_ITEM.c.state.in_(["pending", "running"]),
                    MEMORY_WORK_ITEM.c.due_at <= now,
                    MEMORY_WORK_ITEM.c.attempts >= 5,
                )
                .values(state="failed", updated_at=now)
            )
            row = (
                (
                    await conn.execute(
                        sa.select(MEMORY_WORK_ITEM)
                        .where(
                            MEMORY_WORK_ITEM.c.historical == historical,
                            MEMORY_WORK_ITEM.c.state.in_(["pending", "running"]),
                            MEMORY_WORK_ITEM.c.due_at <= now,
                            MEMORY_WORK_ITEM.c.attempts < 5,
                        )
                        .order_by(MEMORY_WORK_ITEM.c.as_of, MEMORY_WORK_ITEM.c.id)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            updated = (
                (
                    await conn.execute(
                        sa.update(MEMORY_WORK_ITEM)
                        .where(MEMORY_WORK_ITEM.c.id == row["id"])
                        .values(
                            state="running",
                            attempts=row["attempts"] + 1,
                            updated_at=now,
                            due_at=now + timedelta(seconds=lease_seconds),
                        )
                        .returning(MEMORY_WORK_ITEM)
                    )
                )
                .mappings()
                .one()
            )
            return work_row(updated)

    async def settle(self, item: WorkItem, *, now: datetime) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.update(MEMORY_WORK_ITEM)
                .where(*self._owns(item))
                .values(state="settled", settled_at=now, updated_at=now)
            )
            return result.rowcount == 1

    async def fail(self, item: WorkItem, *, error: str, now: datetime) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.update(MEMORY_WORK_ITEM)
                .where(*self._owns(item))
                .values(
                    state="failed" if item.attempts >= 5 else "pending",
                    last_error=error,
                    updated_at=now,
                    due_at=now + timedelta(seconds=min(3600, 2**item.attempts)),
                )
            )
            return result.rowcount == 1

    @staticmethod
    def _owns(item: WorkItem) -> tuple[Any, ...]:
        return (
            MEMORY_WORK_ITEM.c.id == item.id,
            MEMORY_WORK_ITEM.c.state == "running",
            MEMORY_WORK_ITEM.c.attempts == item.attempts,
            MEMORY_WORK_ITEM.c.input_hash == item.input_hash,
        )

    async def requeue_failed(self, *, unit: str | None = None, now: datetime) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.update(MEMORY_WORK_ITEM)
                .where(
                    MEMORY_WORK_ITEM.c.state == "failed",
                    MEMORY_WORK_ITEM.c.unit == unit if unit is not None else sa.true(),
                )
                .values(state="pending", attempts=0, last_error=None, due_at=now, updated_at=now)
            )
            return result.rowcount
