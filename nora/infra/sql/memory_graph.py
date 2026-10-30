"""Postgres memory graph. Each public operation owns one transaction.

Ids are globally unique: generated entities use UUIDs; code projections use
UUID5 over unit/kind/source, and identity pointers use pointer_entity. An
attempt to reuse an id for a different kind or unit is rejected, never conflated.
"""

from __future__ import annotations

import json
import re
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
    PersonTopicActivity,
    Relation,
    Source,
    Subgraph,
    TopicActivity,
    Walk,
    WorkItem,
)
from nora.infra.sql.schema import (
    COMPREHEND_WEB_PAGE,
    ENTITY,
    MEMORY_GRAPH_REFUSAL,
    MEMORY_NODE,
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
     WHERE status='current'
       AND (layer!='record' OR (p.depth=0 AND src_kind=p.kind AND src_id=p.id))
       AND layer=ANY(CAST(:layers AS text[]))
       AND (cardinality(CAST(:rels AS text[]))=0 OR rel=ANY(CAST(:rels AS text[])))
       AND (CAST(:at AS timestamptz) IS NULL OR
         ((valid_from IS NULL OR valid_from<=:at) AND (valid_to IS NULL OR :at<valid_to)))
       AND (NOT EXISTS (SELECT 1 FROM entity e WHERE e.kind=other_kind AND e.id=other_id)
         OR EXISTS (SELECT 1 FROM entity e WHERE e.kind=other_kind AND e.id=other_id
             AND e.unit=:unit))
   ) ranked WHERE ordinal<=:fan
 ) r
 WHERE p.depth<=:depth
   AND (NOT :scoped OR p.depth=0 OR p.kind NOT IN ('person','room','unit','nora','place'))
   AND NOT (r.other_kind || ':' || r.other_id)=ANY(p.seen)
)
SELECT * FROM paths LIMIT :budget
"""


def walk_sql(query: Walk) -> tuple[str, dict[str, Any]]:
    """Scope every endpoint and evidence source before it can enter a path."""
    if query.scope is None:
        return WALK_SQL, {}
    from sqlalchemy.dialects import postgresql

    from nora.infra.sql.memory_read import _in_scope

    n = MEMORY_NODE.alias("scoped_node")
    scoped_select = sa.select(n.c.kind, n.c.source_id, n.c.origin_room).where(
        _in_scope(n, query.scope)
    )
    compiled = scoped_select.compile(
        dialect=postgresql.dialect(paramstyle="named"),  # type: ignore[no-untyped-call]
        compile_kwargs={"render_postcompile": True},
    )
    predicate = re.sub(r":(\w+)::([A-Z]+(?:\[\])?)", r"CAST(:\1 AS \2)", str(compiled))

    # Correlated endpoint probes use the memory_node key index. No whole-unit preload.
    def permitted(kind: str, ident: str) -> str:
        # Only code-owned column expressions and a compiled bound predicate enter SQL.
        return f"""(EXISTS (SELECT 1 FROM entity e WHERE e.kind={kind} AND e.id={ident}
          AND e.unit=:unit AND (e.kind!='person'
            OR coalesce(e.attributes->>'ref',e.id)=ANY(CAST(:persons AS text[]))))
          OR EXISTS (SELECT 1 FROM ({predicate}) scoped_node
          WHERE scoped_node.kind=CASE WHEN {kind}='event_entry' THEN 'event' ELSE {kind} END
          AND scoped_node.source_id={ident}
          AND (scoped_node.origin_room IS NULL OR NOT EXISTS (
            SELECT 1 FROM (
              SELECT unnest(r.persons) AS person UNION
              SELECT coalesce(subject.attributes->>'ref',subject.id) FROM entity subject
              WHERE subject.kind='person' AND subject.unit=:unit
                AND (subject.id=r.src_id OR subject.id=r.dst_id)
            ) subjects WHERE NOT EXISTS (
              SELECT 1 FROM jsonb_each(CAST(:room_persons AS jsonb)) room
              WHERE room.key=scoped_node.origin_room AND room.value ? subjects.person))))
          OR ({kind}='web_page' AND EXISTS (SELECT 1 FROM comprehend_web_page page
              WHERE page.id={ident} AND page.unit=:unit
                AND (CAST(:since_ms AS bigint) IS NULL
                  OR extract(epoch FROM page.fetched_at)*1000>=:since_ms)
                AND (CAST(:until_ms AS bigint) IS NULL
                  OR extract(epoch FROM page.fetched_at)*1000<=:until_ms)
                AND CAST(:only_person AS text) IS NULL)))"""  # noqa: S608

    conditions = [permitted("r.src_kind", "r.src_id"), permitted("r.dst_kind", "r.dst_id")]
    conditions.append("""(NOT (r.rel=ANY(CAST(:location_rels AS text[]))) OR (
        r.persons <@ CAST(:presence AS text[]) AND NOT EXISTS (
          SELECT 1 FROM entity subject WHERE subject.kind='person' AND subject.unit=:unit
            AND subject.id IN (r.src_id,r.dst_id)
            AND NOT coalesce(subject.attributes->>'ref',subject.id)=ANY(CAST(:presence AS text[]))
        )))""")
    conditions.append(
        "NOT EXISTS (SELECT 1 FROM relation_evidence ev WHERE ev.relation_id=r.id AND NOT "  # noqa: S608
        + permitted("ev.source_kind", "ev.source_id")
        + ")"
    )
    conditions.append(
        "NOT EXISTS (SELECT 1 FROM relation_evidence attribute_ev "  # noqa: S608
        "WHERE attribute_ev.entity_id IN (r.src_id,r.dst_id) AND NOT "
        + permitted("attribute_ev.source_kind", "attribute_ev.source_id")
        + ")"
    )
    conditions.append("""(
        (CAST(:since_ms AS bigint) IS NULL OR extract(epoch FROM r.asserted_at)*1000>=:since_ms)
        AND (CAST(:until_ms AS bigint) IS NULL OR extract(epoch FROM r.asserted_at)*1000<=:until_ms)
        AND (CAST(:only_person AS text) IS NULL OR :only_person=ANY(r.persons)
          OR EXISTS (SELECT 1 FROM entity subject WHERE subject.kind='person'
            AND subject.unit=:unit AND subject.id IN (r.src_id,r.dst_id)
            AND coalesce(subject.attributes->>'ref',subject.id)=:only_person)
          OR NOT EXISTS (SELECT 1 FROM entity e WHERE e.kind=r.src_kind AND e.id=r.src_id)
          OR NOT EXISTS (SELECT 1 FROM entity e WHERE e.kind=r.dst_kind AND e.id=r.dst_id)
          OR EXISTS (SELECT 1 FROM relation_evidence ev WHERE ev.relation_id=r.id
                      OR ev.entity_id IN (r.src_id,r.dst_id)))
    )""")
    cte = (
        "eligible AS NOT MATERIALIZED (SELECT r.* FROM relation r WHERE "  # noqa: S608
        + " AND ".join(conditions)
        + "), "
    )
    sql = WALK_SQL.replace("WITH RECURSIVE ", "WITH RECURSIVE " + cte).replace(
        "FROM relation r", "FROM eligible r"
    )
    # The replacement must leave the CTE's base table intact.
    sql = sql.replace("SELECT r.* FROM eligible r WHERE", "SELECT r.* FROM relation r WHERE", 1)
    params = dict(compiled.params)
    only = query.scope.only
    params.update(
        since_ms=only.since_ms if only else None,
        until_ms=only.until_ms if only else None,
        only_person=only.person if only else None,
    )
    params.update(
        room_persons=json.dumps({k: sorted(v) for k, v in query.scope.room_persons.items()}),
        presence=sorted(query.scope.presence_persons),
        location_rels=[
            "works_at",
            "studies_at",
            "lives_in",
            "member_of",
            "stayed_at",
            "co_located_with",
            "during",
        ],
    )
    return sql, params


def walk_parameters(seeds: tuple[Key, ...], query: Walk) -> dict[str, Any]:
    return {
        "kinds": [k for k, _ in seeds],
        "ids": [i for _, i in seeds],
        "unit": query.unit,
        "scoped": query.scope is not None,
        "persons": sorted(query.persons),
        "layers": list(query.layers),
        "rels": list(query.relations),
        "at": query.valid_at,
        "fan": query.fan_out + 1,
        "depth": query.depth,
        "budget": max(1, query.nodes * (query.fan_out + 1) + len(seeds)),
    }


async def node_fields(conn: AsyncConnection, keys: set[Key]) -> dict[Key, Mapping[str, object]]:
    """Canonical source fields, grouped into indexed reads by kind."""
    from uuid import UUID

    from nora.infra.sql import schema

    tables = {
        "message": schema.MESSAGES,
        "note": schema.NOTE,
        "event_entry": schema.EVENTS,
        "memo": schema.MEMO,
        "lesson": schema.LESSON,
        "profile_line": schema.PROFILE_LINE,
        "web_page": schema.COMPREHEND_WEB_PAGE,
    }
    revisions = {
        "note": (schema.NOTE_REVISION, "note_id"),
        "memo": (schema.MEMO_REVISION, "memo_id"),
        "lesson": (schema.LESSON_REVISION, "lesson_id"),
    }
    result: dict[Key, Mapping[str, object]] = {}
    for kind, table in tables.items():
        ids: list[Any] = []
        for k, ident in keys:
            if k != kind:
                continue
            try:
                if kind == "message":
                    ids.append(int(ident))
                elif kind == "web_page":
                    ids.append(ident)
                else:
                    value = UUID(ident)
                    ids.append(value if getattr(table.c.id.type, "as_uuid", False) else str(value))
            except ValueError:
                continue
        if not ids:
            continue
        for row in (await conn.execute(sa.select(table).where(table.c.id.in_(ids)))).mappings():
            result[(kind, str(row["id"]))] = dict(row)
        if kind in revisions:
            revision, owner = revisions[kind]
            if kind == "note":
                heads = [
                    fields["head_revision_id"] for (k, _), fields in result.items() if k == kind
                ]
                stmt = sa.select(revision).where(revision.c.id.in_(heads))
            else:
                stmt = (
                    sa.select(revision)
                    .where(revision.c[owner].in_(ids))
                    .distinct(revision.c[owner])
                    .order_by(revision.c[owner], revision.c.seq.desc())
                )
            rows = (await conn.execute(stmt)).mappings()
            for row in rows:
                key = (kind, str(row[owner]))
                if key in result:
                    result[key] = {**result[key], "revision": dict(row)}
    return result


def entity_row(row: Mapping[Any, Any]) -> Entity:
    values = dict(row)
    values["aliases"] = tuple(values["aliases"])
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
    # ponytail: live walks; precomputed neighbourhoods if measured p95 exceeds 200 ms.
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

    async def topic_activity(self, *, unit: str, topic: Key, at: datetime) -> TopicActivity:
        if topic[0] != "topic":
            raise ValueError("topic key required")
        r, e = RELATION.c, RELATION_EVIDENCE.c
        stmt = (
            sa.select(r.src_kind, r.src_id, r.asserted_at, e.source_kind, e.source_id)
            .select_from(RELATION.join(RELATION_EVIDENCE, e.relation_id == r.id))
            .where(
                r.unit == unit,
                r.dst_kind == "topic",
                r.dst_id == topic[1],
                r.src_kind == "person",
                r.rel == "interested_in",
                r.status == "current",
                sa.or_(r.valid_from.is_(None), r.valid_from <= at),
                sa.or_(r.valid_to.is_(None), at < r.valid_to),
            )
        )
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    sa.text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}")
                )
                rows = (await conn.execute(stmt)).all()
            grouped: dict[Key, list[Any]] = {}
            for row in rows:
                grouped.setdefault((row.src_kind, row.src_id), []).append(row)
            persons = tuple(
                PersonTopicActivity(
                    person,
                    len(items),
                    min(r.asserted_at for r in items),
                    max(r.asserted_at for r in items),
                    tuple(sorted({(r.source_kind, r.source_id) for r in items})),
                )
                for person, items in sorted(grouped.items())
            )
            return TopicActivity("ok" if persons else "nothing_found", persons)
        except (SQLAlchemyError, OSError, TimeoutError):
            return TopicActivity("unavailable")

    async def walk(self, seeds: tuple[Key, ...], query: Walk) -> Subgraph:
        started = monotonic()
        result = Subgraph("unavailable")
        depth = 0
        try:
            async with self.engine.begin() as conn:
                # Scope, fields and evidence must see the same source revision.
                await conn.execute(
                    sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                await conn.execute(
                    sa.text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}"),
                )
                params = walk_parameters(seeds, query)
                sql, scope_params = walk_sql(query)
                params.update(scope_params)
                paths = (await conn.execute(sa.text(sql), params)).mappings().all()
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
                    if seeds and query.scope is None
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
                                RELATION_EVIDENCE.c.relation_id.in_(state.relations),
                                RELATION_EVIDENCE.c.entity_id.in_([i for _, i in keys]),
                            )
                        )
                    )
                ).mappings():
                    values = dict(row)
                    values.pop("id")
                    values["source"] = (values.pop("source_kind"), values.pop("source_id"))
                    state.evidence.append(Evidence(**values))
                source_keys = keys | {e.source for e in state.evidence}
                index_keys = {
                    ("event", i) if k == "event_entry" else (k, i) for k, i in source_keys
                }
                if source_keys:
                    from nora.infra.sql.memory_read import _columns, _node

                    n = MEMORY_NODE.alias("n")
                    stmt = sa.select(*_columns(n)).where(
                        sa.tuple_(n.c.kind, n.c.source_id).in_(index_keys)
                    )
                    for indexed_row in (await conn.execute(stmt)).all():
                        node = _node(indexed_row, query.scope)
                        state.indexed[node.key] = node
                        if node.kind == "event":
                            from dataclasses import replace

                            state.indexed[("event_entry", node.source_id)] = replace(
                                node, kind="event_entry"
                            )
                    page_ids = [i for k, i in source_keys if k == "web_page"]
                    if page_ids:
                        from nora.domain.memory_index import IndexedNode

                        for page in (
                            await conn.execute(
                                sa.select(COMPREHEND_WEB_PAGE).where(
                                    COMPREHEND_WEB_PAGE.c.id.in_(page_ids),
                                    COMPREHEND_WEB_PAGE.c.unit == query.unit,
                                )
                            )
                        ).mappings():
                            node = IndexedNode(
                                "web_page",
                                page["id"],
                                page["text"],
                                int(page["fetched_at"].timestamp() * 1000),
                                unit=page["unit"],
                            )
                            state.indexed[node.key] = node
                state.fields = await node_fields(conn, source_keys)
                from dataclasses import replace

                for key, fields in state.fields.items():
                    if key in state.indexed:
                        revision = fields.get("revision")
                        body = (
                            revision.get("body")
                            if isinstance(revision, dict)
                            else fields.get("body") or fields.get("text")
                        )
                        if isinstance(body, str):
                            state.indexed[key] = replace(state.indexed[key], text=body)
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
