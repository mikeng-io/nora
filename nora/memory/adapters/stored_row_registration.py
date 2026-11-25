"""Register existing source rows in bounded Postgres transactions."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.aggregate_writes import AggregateWrites
from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.cognitive_graph_store import RowData, SqlCognitiveGraph
from nora.infra.sql.item_registration import RegistrationScope
from nora.infra.sql.schema import METADATA, PRESENCE_FIXES, PRESENCE_PLACES
from nora.memory.adapters.stored_aggregate_planes import AggregatePlanes
from nora.memory.adapters.stored_item_planes import ItemPlanes
from nora.memory.app.register_stored_rows import TableRegistration
from nora.memory.domain.registration import BACKFILL_AGGREGATE_FAMILIES, BACKFILL_ITEM_SOURCES

Rows = Sequence[RowData]


def _key(table: sa.Table) -> tuple[str, ...]:
    return ("circle_id", "place_id") if table is PRESENCE_PLACES else tuple(
        col.name for col in table.primary_key
    )


async def _write_items(
    conn: AsyncConnection, table: sa.Table, keys: tuple[str, ...],
    rows: Sequence[RowData], copies: Sequence[RowData],
) -> None:
    records = sa.func.unnest(
        sa.bindparam("graph_ids", type_=ARRAY(sa.Text)),
        sa.bindparam("units", type_=ARRAY(sa.Text)),
    ).table_valued("id", "unit").render_derived()
    registry = pg_insert(REGISTRY).from_select(
        ("id", "unit", "kind"), sa.select(records.c.id, records.c.unit, sa.literal("item")),
    ).on_conflict_do_nothing(index_elements=["id", "unit"])
    all_rows = (*rows, *copies)
    # The registry's history must predate the source row that references it.
    await conn.execute(registry, {
        "graph_ids": [row["graph_id"] for row in all_rows],
        "units": [row["unit"] for row in all_rows],
    })

    key_columns = {f"_source_{name}": table.c[name].type for name in keys}
    shared = {
        name: value for name, value in rows[0].items()
        if name not in key_columns and all(row[name] == value for row in rows)
    }
    plane_columns = {
        name: table.c[name].type for name in rows[0]
        if name not in key_columns and name not in shared
    }
    columns = key_columns | plane_columns
    payload = [
        {name: row[name].isoformat() if isinstance(row[name], datetime) else row[name]
         for name in columns}
        for row in rows
    ]
    records = sa.func.jsonb_to_recordset(sa.bindparam("items", type_=JSONB)).table_valued(
        *(sa.column(name, type_) for name, type_ in columns.items()),
    ).render_derived(with_types=True)
    update = sa.update(table).where(
        *(table.c[name] == records.c[f"_source_{name}"] for name in keys),
    ).values({**shared, **{name: records.c[name] for name in plane_columns}})
    await conn.execute(update, {"items": payload})


async def _ids(conn: AsyncConnection, table: sa.Table, count: int) -> Iterator[Any]:
    if count == 0:
        return iter(())
    if table.autoincrement_column is None:
        return iter(str(uuid4()) for _ in range(count))
    sequence = sa.func.pg_get_serial_sequence(table.name, "id")
    values = await conn.scalars(sa.select(sa.func.nextval(sequence)).select_from(
        sa.func.generate_series(1, count).table_valued(),
    ))
    return iter(values)


class SqlStoredRows:
    def __init__(self, engine: AsyncEngine, scope: RegistrationScope) -> None:
        self._engine = engine
        self._scope = scope
        graph = SqlCognitiveGraph(engine)
        self._items = ItemPlanes(scope)
        self._aggregates = AggregatePlanes(AggregateWrites(graph, None), scope)

    async def register_table(self, name: str, *, batch_size: int) -> TableRegistration:
        if name not in BACKFILL_ITEM_SOURCES and name not in BACKFILL_AGGREGATE_FAMILIES:
            raise ValueError(f"not a stored-row registration source: {name}")
        table = METADATA.tables[name]
        keys = _key(table)
        key = sa.tuple_(*(table.c[key] for key in keys))
        stmt = (
            sa.select(table).where(table.c.graph_id.is_(None))
            .order_by(*(table.c[name] for name in keys)).limit(batch_size)
            .with_for_update()
        )
        cursor = None
        registered = unscoped = 0
        while True:
            async with self._engine.begin() as conn:
                batch = (await conn.execute(
                    stmt if cursor is None else stmt.where(key > cursor),
                )).mappings().all()
                if not batch:
                    return TableRegistration(registered, unscoped)
                cursor = tuple(batch[-1][name] for name in keys)
                if name in BACKFILL_ITEM_SOURCES:
                    n, outside = await self._register_items(conn, table, keys, batch)
                else:
                    n, outside = await self._register_aggregates(conn, table, keys, batch)
                registered += n
                unscoped += outside

    async def _register_items(
        self, conn: AsyncConnection, table: sa.Table, keys: tuple[str, ...], batch: Rows,
    ) -> tuple[int, int]:
        name = table.name
        parents = await self._items.dependencies(conn, name, batch)
        inputs = [(row, await self._items.scope_for(name, row, parents)) for row in batch]
        count = sum(max(len(unit_info[0]) - 1, 0) for _, unit_info in inputs)
        ids = await _ids(conn, table, count)
        changes, copies = [], []
        unscoped = 0
        for row, (units, origin, identities) in inputs:
            if not units:
                unscoped += 1
                continue
            original = tuple(row[name] for name in keys)
            for index, unit in enumerate(units):
                key = original if index == 0 else (next(ids),)
                values = self._items.values(
                    name, row, unit=unit, key=key, origin=origin, identities=identities,
                )
                if index == 0:
                    changes.append({**values, **{
                        f"_source_{name}": value for name, value in zip(keys, key, strict=True)
                    }})
                else:
                    copies.append({**row, **values, "id": key[0]})
        if changes:
            await _write_items(conn, table, keys, changes, copies)
            if copies:
                await conn.execute(sa.insert(table), copies)
        return len(changes) + len(copies), unscoped

    async def _register_aggregates(
        self, conn: AsyncConnection, table: sa.Table, keys: tuple[str, ...], batch: Rows,
    ) -> tuple[int, int]:
        registered = unscoped = 0
        for row in batch:
            units = await self._aggregates.units_for(table, row)
            if not units:
                unscoped += 1
                continue
            if table is PRESENCE_PLACES:
                await conn.execute(PRESENCE_PLACES.delete().where(
                    PRESENCE_PLACES.c.unit.is_(None),
                    PRESENCE_PLACES.c.circle_id == row["circle_id"],
                    PRESENCE_PLACES.c.place_id == row["place_id"],
                ))
            for index, unit in enumerate(units):
                planes = await self._aggregates.values(conn, table, row, unit)
                if index == 0 and table is not PRESENCE_PLACES:
                    await conn.execute(sa.update(table).where(
                        *(table.c[name] == row[name] for name in keys),
                    ).values(planes))
                else:
                    copy = {**row, **planes}
                    copy.pop("id", None)
                    await conn.execute(sa.insert(table).values(copy))
                registered += 1
        return registered, unscoped

    async def backfill_presence_fix_units(self, *, batch_size: int) -> TableRegistration:
        """Assign pre-cutover raw fixes to their observing units, without graph writes."""
        table = PRESENCE_FIXES
        stmt = (
            sa.select(table).where(table.c.unit.is_(None))
            .order_by(table.c.id).limit(batch_size).with_for_update()
        )
        cursor = None
        registered = unscoped = 0
        while True:
            async with self._engine.begin() as conn:
                batch = (await conn.execute(
                    stmt if cursor is None else stmt.where(table.c.id > cursor),
                )).mappings().all()
                if not batch:
                    return TableRegistration(registered, unscoped)
                cursor = batch[-1]["id"]
                for row in batch:
                    units = tuple(sorted(await self._scope.person_units(row["person_id"])))
                    if not units:
                        unscoped += 1
                        continue
                    represented = set((await conn.execute(sa.select(table.c.unit).where(
                        table.c.circle_id == row["circle_id"],
                        table.c.person_id == row["person_id"],
                        table.c.ts == row["ts"],
                        table.c.unit.in_(units),
                    ))).scalars())
                    missing = [unit for unit in units if unit not in represented]
                    if not missing:
                        await conn.execute(sa.delete(table).where(table.c.id == row["id"]))
                        continue
                    await conn.execute(sa.update(table).where(
                        table.c.id == row["id"],
                    ).values(unit=missing[0]))
                    registered += 1
                    for unit in missing[1:]:
                        copy = {**row, "unit": unit}
                        copy.pop("id", None)
                        await conn.execute(sa.insert(table).values(copy))
                        registered += 1

    async def validate_pairs(self) -> None:
        async with self._engine.begin() as conn:
            for name in (*BACKFILL_ITEM_SOURCES, *BACKFILL_AGGREGATE_FAMILIES):
                ident = conn.dialect.identifier_preparer.quote_identifier
                check = ident(f"ck_{name}_graph_pair")
                stmt = sa.text(f"ALTER TABLE {ident(name)} VALIDATE CONSTRAINT {check}")
                await conn.execute(stmt)
