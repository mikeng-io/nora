"""Attach computed graph identity and source scope to Rollup row writes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.graph_registration import Exposure
from nora.domain.lineage import ROW, Lineage, split_room
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph
from nora.infra.sql.item_registration import SqlItemRegistration, epoch_ms, item_graph_id
from nora.infra.sql.schema import (
    MESSAGE_REACTION,
    MESSAGES,
    METADATA,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_STAYS,
    ROLLUP_BUILD,
    ROLLUP_RESPONSE,
    SOCIAL_ITEM,
)

_PRESENCE_INTERVAL = frozenset(
    {
        "presence_stays",
        "presence_trips",
        "presence_derived_places",
        "presence_corridors",
    }
)
_POSITIONS = {
    "presence_stays": ("subject_id", "circle_id"),
    "presence_trips": ("subject_id", "circle_id"),
    "presence_derived_places": ("subject_id", "circle_id", "first_seen_ts"),
    "presence_corridors": ("subject_id", "sequence"),
}


_ROLLUP_KEYS = {
    "rollup_colocation": ("persons",),
    "rollup_outing": ("persons",),
    "rollup_daybook": ("person_id", "local_date"),
    "rollup_tie": ("person_a", "person_b", "iso_week"),
}


def rollup_key(table: sa.Table, row: RowData) -> tuple[Any, ...]:
    if table is ROLLUP_RESPONSE:
        return (item_graph_id("messages", (row["message_id"],), row["unit"]),)
    return tuple(row[name] for name in (*_ROLLUP_KEYS[table.name], "valid_from_ms", "valid_to_ms"))


def content_hash(row: RowData) -> str:
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str).encode()).hexdigest()


def _interval(row: RowData, table: str) -> tuple[datetime, datetime] | None:
    if table.startswith("rollup_"):
        lo, hi = row["valid_from_ms"], row["valid_to_ms"]
    elif table in {"presence_stays", "presence_trips"}:
        lo, hi = row["started_ts"], row["ended_ts"]
    elif table in _PRESENCE_INTERVAL:
        lo, hi = row["first_seen_ts"], row["last_seen_ts"]
    else:
        return None
    start, end = epoch_ms(lo), epoch_ms(hi)
    if start is None or end is None:
        raise ValueError(f"missing aggregate interval for {table}")
    return start, end


def _participants(row: RowData) -> Iterable[str]:
    if persons := row.get("persons"):
        return tuple(persons)
    if "person_a" in row:
        return (row["person_a"], row["person_b"])
    if "responders" in row:
        return (*row["responders"], *row["named_persons"])
    if subject := row.get("subject_id", row.get("person_id")):
        return (subject,)
    return ()


class AggregateWrites:
    """Registration for the existing Postgres Rollup writers."""

    def __init__(self, graph: SqlCognitiveGraph, registration: SqlItemRegistration | None) -> None:
        self.graph = graph
        self.registration = registration

    async def annotate(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        row: RowData,
        *,
        key: tuple[Any, ...],
        unit: str | None,
        inputs: Iterable[RowData] = (),
        asserted_at: datetime | None = None,
        existing_id: str | None = None,
        live: bool = True,
    ) -> dict[str, Any]:
        interval = _interval(row, table.name)
        occurred_at = interval[0] if interval else epoch_ms(row["updated_ts"])
        recorded_at = asserted_at or row.get("recorded_at")
        if recorded_at is None:
            recorded_at = (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()
        build_lineage = Lineage.from_json(row.get("build_lineage"))
        minimum: Exposure = (
            "internal"
            if table.name.startswith("presence_")
            or any(source.kind == "presence" for source in build_lineage.section(ROW))
            else "external"
        )
        values = await self.graph.aggregate_planes(
            conn,
            table,
            unit=unit,
            key=key,
            interval=interval,
            occurred_at=occurred_at,
            asserted_at=recorded_at,
            room=row.get("conversation_id"),
            persons=_participants(row),
            minimum=minimum,
            build_lineage=row.get("build_lineage"),
            inputs=inputs,
            revisions=self.registration.revisions() if live and self.registration else None,
            existing_id=existing_id,
        )
        return {"recorded_at": recorded_at, **values} if "recorded_at" in table.c else values

    async def rollup_inputs(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        row: RowData,
        build: int,
    ) -> list[RowData]:
        unit = row["unit"]
        entries = Lineage.from_json(row["build_lineage"]).section(ROW)
        low, high = row["valid_from_ms"], row["valid_to_ms"]
        sources: list[Any] = []
        if subjects := {entry.subject for entry in entries if entry.kind == "presence"}:
            stays = PRESENCE_STAYS.c
            sources.append(
                sa.select(PRESENCE_STAYS).where(
                    stays.unit == unit,
                    stays.state == "live",
                    stays.build_id == build,
                    stays.subject_id.in_(subjects),
                    stays.started_ts <= high,
                    stays.ended_ts >= low,
                )
            )
            places = PRESENCE_DERIVED_PLACES.c
            sources.append(
                sa.select(PRESENCE_DERIVED_PLACES).where(
                    places.unit == unit,
                    places.state == "live",
                    places.build_id == build,
                    places.subject_id.in_(subjects),
                    places.source_kind == "home",
                )
            )
        if rooms := {split_room(e.origin) for e in entries if e.kind == "message" and e.origin}:
            room_filter = sa.or_(
                *(
                    sa.and_(MESSAGES.c.platform == platform, MESSAGES.c.conversation_id == room)
                    for platform, room in rooms
                )
            )
            sources.append(
                sa.select(MESSAGES).where(
                    MESSAGES.c.unit == unit,
                    MESSAGES.c.state == "live",
                    MESSAGES.c.ts.between(low, high),
                    room_filter,
                )
            )
        elif table is ROLLUP_RESPONSE:
            sources.append(sa.select(MESSAGES).where(MESSAGES.c.id == row["message_id"]))
        if table is ROLLUP_RESPONSE and row["reactions"]:
            reactions = MESSAGE_REACTION.c
            sources.append(sa.select(MESSAGE_REACTION).where(
                reactions.unit == unit, reactions.state == "live",
                reactions.removed == 0, reactions.message_id == row["message_id"],
                reactions.ts.between(low, high),
            ))
        if people := {e.subject for e in entries if e.kind == "social"}:
            s = SOCIAL_ITEM.c
            sources.append(
                sa.select(SOCIAL_ITEM).where(
                    s.unit == unit,
                    s.state == "live",
                    s.person.in_(people),
                    s.taken_at.between(epoch_ms(low), epoch_ms(high)),
                )
            )
        rows: list[RowData] = []
        for source in sources:
            rows.extend((await conn.execute(source)).mappings().all())
        return rows

    async def retire(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        rows: Iterable[RowData],
    ) -> None:
        for row in rows:
            if row["graph_id"] is not None:
                await self.graph.tombstone_in(
                    conn,
                    table,
                    row_id=row["graph_id"],
                    unit=row["unit"],
                    content_hash=content_hash(row),
                )

    def _span_key(self, table: sa.Table, row: RowData) -> tuple[Any, ...]:
        return tuple(row[c] for c in _POSITIONS[table.name])

    async def presence_planes(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        row: RowData,
        *,
        unit: str | None,
        existing: RowData | None = None,
        graph_id: str | None = None,
    ) -> dict[str, Any]:
        from nora.infra.sql.schema import PRESENCE_FIXES, PRESENCE_TRIPS

        family = table.name

        def unit_filter(relation: sa.Table) -> Any:
            return relation.c.unit.is_not_distinct_from(unit)

        subject = row["subject_id"]
        if family in {"presence_stays", "presence_trips"}:
            source = PRESENCE_FIXES
            source_query = sa.select(source).where(
                unit_filter(source),
                source.c.person_id == row["person_id"],
                source.c.circle_id == row["circle_id"],
                source.c.ts.between(row["started_ts"], row["ended_ts"]),
            )
            semantic_key = (*self._span_key(table, row), unit)
        elif family == "presence_corridors":
            source = PRESENCE_TRIPS
            source_query = sa.select(source).where(
                unit_filter(source),
                source.c.subject_id == subject,
                source.c.build_id == row["build_id"],
                source.c.state == "live",
                source.c.started_ts.between(row["first_seen_ts"], row["last_seen_ts"]),
            )
            semantic_key = (*self._span_key(table, row), unit)
        elif family == "presence_derived_places":
            source = PRESENCE_STAYS
            source_query = sa.select(source).where(
                unit_filter(source),
                source.c.subject_id == subject,
                source.c.build_id == row["build_id"],
                source.c.state == "live",
                source.c.circle_id == row["circle_id"],
                source.c.started_ts.between(row["first_seen_ts"], row["last_seen_ts"]),
            )
            semantic_key = (*self._span_key(table, row), unit)
        else:
            source = PRESENCE_STAYS
            source_query = sa.select(source).where(
                unit_filter(source),
                source.c.subject_id == subject,
                source.c.build_id == row["build_id"],
                source.c.place_id == row["place_id"],
                source.c.state == "live",
            )
            place = (
                await conn.execute(
                    sa.select(
                        PRESENCE_DERIVED_PLACES.c.graph_id,
                        PRESENCE_DERIVED_PLACES.c.circle_id,
                        PRESENCE_DERIVED_PLACES.c.first_seen_ts,
                    ).where(PRESENCE_DERIVED_PLACES.c.id == row["place_id"])
                )
            ).first()
            if place is None:
                raise ValueError("a presence bucket has no registered place")
            semantic_key = (
                subject,
                place.graph_id or (place.circle_id, place.first_seen_ts),
                unit,
                row.get("hour_of_week"),
            )
        inputs = (await conn.execute(source_query)).mappings().all()
        return await self.annotate(
            conn,
            table,
            row,
            key=semantic_key,
            unit=unit,
            inputs=inputs,
            asserted_at=existing["recorded_at"] if existing else None,
            existing_id=existing["graph_id"] if existing else graph_id,
        )

    async def store_presence(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        values: RowData,
        *,
        old: RowData | None = None,
    ) -> int | None:
        reg = self.registration
        units = await reg.scope.person_units(values["subject_id"]) if reg else ()
        picked = None
        for unit in units or (None,):
            existing = old if old is not None and old["unit"] == unit else None
            if old is not None and existing is None:
                columns = _POSITIONS[table.name]
                match = [table.c[name] == old[name] for name in columns]
                existing = (
                    (
                        await conn.execute(
                            sa.select(table).where(
                                *match,
                                table.c.build_id == old["build_id"],
                                table.c.unit.is_not_distinct_from(unit),
                                table.c.state == "live",
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
            if reg is None:
                data = dict(values)
            else:
                data = {
                    **values,
                    **await self.presence_planes(
                        conn,
                        table,
                        values,
                        unit=unit,
                        existing=existing,
                        graph_id=await self._current_span_identity(conn, table, values, unit)
                        if existing is None else None,
                    ),
                }
            if existing is None:
                stmt = sa.insert(table).values(**data)
                if "id" in table.c and picked is None:
                    picked = await conn.scalar(stmt.returning(table.c.id))
                else:
                    await conn.execute(stmt)
            else:
                await conn.execute(
                    sa.update(table)
                    .where(
                        table.c.id == existing["id"],
                    )
                    .values(**data)
                )
                if picked is None:
                    picked = existing.get("id")
        return picked

    async def _current_span_identity(
        self, conn: AsyncConnection, table: sa.Table,
        row: RowData, unit: str | None,
    ) -> str | None:
        if unit is None or table.name not in ("presence_derived_places", "presence_corridors"):
            return None
        return await conn.scalar(
            sa.select(table.c.graph_id)
            .join(ROLLUP_BUILD, ROLLUP_BUILD.c.id == table.c.build_id)
            .where(
                ROLLUP_BUILD.c.status == "current", table.c.build_id != row["build_id"],
                *(table.c[name] == row[name] for name in _POSITIONS[table.name]),
                table.c.state == "live", table.c.unit == unit, table.c.graph_id.is_not(None),
            )
            .order_by(table.c.id).limit(1)
        )

    async def span_copies(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        row_id: int,
    ) -> list[RowData]:
        first = (
            (await conn.execute(sa.select(table).where(table.c.id == row_id))).mappings().first()
        )
        if first is None:
            return []
        columns = _POSITIONS[table.name]
        return list(
            (
                await conn.execute(
                    sa.select(table).where(
                        *(table.c[c] == first[c] for c in columns),
                        table.c.build_id == first["build_id"],
                        table.c.state == "live",
                    )
                )
            )
            .mappings()
            .all()
        )

    async def discard_rows(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        *where: Any,
    ) -> None:
        rows = (
            (
                await conn.execute(
                    sa.select(table).where(*where, table.c.state == "live").with_for_update()
                )
            )
            .mappings()
            .all()
        )
        await self.retire(conn, table, rows)
        await conn.execute(sa.delete(table).where(*where, table.c.graph_id.is_(None)))

    async def retire_missing_build(
        self,
        conn: AsyncConnection,
        previous: int,
        new: int,
    ) -> None:
        for name in (
            "presence_stays",
            "presence_trips",
            "presence_derived_places",
            "presence_corridors",
            "presence_occupancy",
            "presence_place_rhythm",
            "rollup_colocation",
            "rollup_outing",
            "rollup_daybook",
            "rollup_tie",
            "rollup_response",
        ):
            table = METADATA.tables[name]
            newer = table.alias("newer")
            produced = sa.exists().where(
                newer.c.build_id == new,
                newer.c.state == "live",
                newer.c.unit == table.c.unit,
                newer.c.graph_id == table.c.graph_id,
            )
            rows = (
                (
                    await conn.execute(
                        sa.select(table).where(
                            table.c.build_id == previous,
                            table.c.state == "live",
                            table.c.graph_id.is_not(None),
                            ~produced,
                        )
                    )
                )
                .mappings()
                .all()
            )
            await self.retire(conn, table, rows)
