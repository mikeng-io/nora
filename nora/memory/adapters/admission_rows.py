"""Read admission facts through the unit's registry and the registered planes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import Lineage
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY, INTENTION, REGISTRY
from nora.infra.sql.schema import MESSAGES, METADATA
from nora.memory.app.admission_identity import AdmissionIdentity
from nora.memory.domain.admission_ports import AdmissionUnavailableError
from nora.memory.domain.admission_proposals import PersonAuthor, RowFacts, SourceReference
from nora.memory.domain.graph import Kind
from nora.memory.domain.registration import AGGREGATE_FAMILIES, ITEM_SOURCES

GRAPH_TABLES = {table.name: table for table in (CLAIM, EDGE, ENTITY, INTENTION)}
_PLANES = (
    "kind",
    "basis",
    "state",
    "unit",
    "origin_room",
    "persons",
    "excluded_persons",
    "exposure",
    "lineage",
    "asserted_at",
)
_SPEECH = {"messages", "message_edit", "message_reaction"}


class SqlAdmissionRows:
    def __init__(self, engine: AsyncEngine, identity: AdmissionIdentity) -> None:
        self._engine = engine
        self._identity = identity

    async def read(self, unit: str, ids: frozenset[str]) -> Mapping[str, RowFacts]:
        try:
            async with self._engine.connect() as raw:
                conn = await raw.execution_options(isolation_level="REPEATABLE READ")
                result: dict[str, RowFacts] = {}
                pending: set[str] = set(ids)
                visited: set[str] = set()
                while pending:
                    result.update(await self._batch(conn, unit, pending))
                    forward = await self._dependencies(conn, unit, pending)
                    for source, targets in forward.items():
                        if source in result:
                            result[source] = replace(result[source], dependencies=tuple(targets))
                    visited.update(pending)
                    pending = {target for targets in forward.values() for target in targets}
                    pending.difference_update(visited)
                return result
        except sa.exc.SQLAlchemyError as exc:
            raise AdmissionUnavailableError(commit_uncertain=False) from exc

    async def _batch(
        self,
        conn: AsyncConnection,
        unit: str,
        ids: set[str],
    ) -> dict[str, RowFacts]:
        registry = dict(
            (
                await conn.execute(
                    sa.select(REGISTRY.c.id, REGISTRY.c.kind).where(
                        REGISTRY.c.unit == unit, REGISTRY.c.id.in_(ids)
                    )
                )
            ).all()
        )
        found: dict[str, RowFacts] = {}
        for kind in set(registry.values()):
            keys = {key for key, value in registry.items() if value == kind}
            tables = (
                ITEM_SOURCES
                if kind == "item"
                else AGGREGATE_FAMILIES
                if kind == "aggregate"
                else {kind: GRAPH_TABLES[kind]}
            )
            graph_id = "graph_id" if kind in ("item", "aggregate") else "id"
            for name in tables:
                if not keys:
                    break
                table = METADATA.tables[name]
                values = await conn.execute(
                    _select(table, graph_id).where(
                        table.c[graph_id].in_(keys), table.c.unit == unit
                    )
                )
                for row in values.mappings():
                    found[row["id"]] = await self._facts(name, row)
                    keys.discard(row["id"])
            if keys:
                raise AdmissionUnavailableError(commit_uncertain=False)
        return found

    @staticmethod
    async def _dependencies(
        conn: AsyncConnection,
        unit: str,
        ids: set[str],
    ) -> dict[str, list[str]]:
        edges = await conn.execute(
            sa.select(EDGE.c.src_id, EDGE.c.dst_id).where(
                EDGE.c.unit == unit,
                EDGE.c.state == "live",
                EDGE.c.type.in_(("draws_on", "rests_on")),
                EDGE.c.src_id.in_(ids),
            )
        )
        result: dict[str, list[str]] = {}
        for source, target in edges:
            result.setdefault(source, []).append(target)
        return result

    async def _facts(self, table: str, row: sa.RowMapping) -> RowFacts:
        origin, unit = row["origin_room"], row["unit"]
        platform = row.get("source_platform")
        room = await self._identity.room(origin, unit, platform=platform) if origin else None
        author: PersonAuthor | Literal["nora"] | None = None
        if table in _SPEECH:
            sender = row["sender"]
            if row.get("source_is_nora") or self._identity.is_nora(platform, sender):
                author = "nora"
            elif sender and row["sender_person"]:
                person = await self._identity.person(row["sender_person"])
                if person is not None:
                    author = PersonAuthor(person)
        is_dm = row.get("source_room_type") == "dm" or bool(room and room.is_dm)
        origin_id = room.id if room and room.unit == unit else (None if is_dm else origin)
        return RowFacts(
            row["id"],
            cast(Kind, row["kind"]),
            row["basis"],
            row["state"],
            unit,
            origin_id,
            is_dm,
            tuple(row["persons"]),
            tuple(row["excluded_persons"]),
            row["exposure"],
            Lineage.from_json(row["lineage"], origin=origin),
            form=row.get("form"),
            trigger=row.get("trigger"),
            delivery_mode=row.get("delivery_mode"),
            pointer_kind=row.get("pointer_kind"),
            source=table if table in ITEM_SOURCES else AGGREGATE_FAMILIES.get(table),
            author=author,
            source_ref=SourceReference(row["source_ref_kind"], row["source_ref_value"])
            if row.get("source_ref_kind") is not None else None,
            fetched_url=row.get("fetched_url"),
            table=table, evidence_at=row["evidence_at"],
            aimed_at_nora=bool(row.get("aimed_at_nora")),
            pointer_ref=row.get("pointer_ref"), part=row.get("part"),
        )


def _select(table: sa.Table, id_column: str) -> sa.Select:
    needed = [table.c[id_column].label("id"), *(table.c[name] for name in _PLANES)]
    needed.append(sa.func.coalesce(table.c.occurred_at, table.c.asserted_at).label(
        "evidence_at",
    ))
    needed.extend(
        table.c[name]
        for name in ("form", "trigger", "delivery_mode", "pointer_kind", "pointer_ref", "part",
                     "source_ref_kind", "source_ref_value")
        if name in table.c
    )
    if table.name == "fetched_pages":
        needed.append(table.c.url.label("fetched_url"))
    if table.name not in _SPEECH:
        return sa.select(*needed)
    stimulus = METADATA.tables["graph_stimulus"].c
    source = table.outerjoin(stimulus.table, sa.and_(
        stimulus.unit == table.c.unit, stimulus.graph_id == table.c.graph_id,
        stimulus.kind == "strong_cue", stimulus.occurrence == "",
        stimulus.cues.contains(["addresses_nora"]),
    ))
    needed.append(stimulus.graph_id.is_not(None).label("aimed_at_nora"))
    parent = MESSAGES
    sender = parent if table.name == "message_edit" else table
    stmt = sa.select(
        *needed,
        sender.c.sender_id.label("sender"),
        sender.c.person_id.label("sender_person"),
        parent.c.platform.label("source_platform"),
        parent.c.conversation_type.label("source_room_type"),
        (parent.c.is_nora if sender is parent else sa.literal(0)).label("source_is_nora"),
    )
    if table is parent:
        return stmt.select_from(source)
    return stmt.select_from(
        source.outerjoin(
            parent,
            (parent.c.id == table.c.message_id) & (parent.c.unit == table.c.unit),
        )
    )
