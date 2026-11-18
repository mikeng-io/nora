"""Resolve retained Rollup rows and current heads in their source units."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.aggregate_writes import AggregateWrites
from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.item_registration import RegistrationScope, epoch_ms, item_graph_id
from nora.infra.sql.schema import (
    PLATFORM_OBSERVATION,
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
)

_UNIT_ROLLUP_KEYS = {
    "rollup_colocation": ("persons", "valid_from_ms", "valid_to_ms"),
    "rollup_outing": ("persons", "valid_from_ms", "valid_to_ms"),
    "rollup_daybook": ("person_id", "local_date", "valid_from_ms", "valid_to_ms"),
    "rollup_tie": ("person_a", "person_b", "iso_week", "valid_from_ms", "valid_to_ms"),
}
_HEAD_KEY = ("platform", "account_key", "conversation_id", "thread", "source_kind", "object_ref")


class AggregatePlanes:
    def __init__(self, writes: AggregateWrites, scope: RegistrationScope) -> None:
        self.writes = writes
        self.scope = scope

    def units_for(self, table: sa.Table, row: RowData) -> tuple[str, ...]:
        if table.name.startswith("rollup_"):
            return (row["unit"],)
        if table.name == "platform_object_state":
            unit = self.scope.room_unit(row["platform"], row["conversation_id"])
            return (unit,) if unit else ()
        if table.name == "presence_places":
            return self.scope.circle_units(row["circle_id"])
        return self.scope.person_units(row["subject_id"])

    async def _link(
        self, conn: AsyncConnection, source: sa.Table, id_: int, unit: str,
    ) -> int:
        candidates = await self.writes.span_copies(conn, source, id_)
        return next((row["id"] for row in candidates if row["unit"] == unit), id_)

    async def values(
        self, conn: AsyncConnection, table: sa.Table, row: RowData, unit: str,
    ) -> dict[str, Any]:
        family = table.name
        if family == "platform_object_state":
            return await self._platform_head(conn, table, row, unit)
        if family == "presence_places":
            at = epoch_ms(row["updated_ms"])
            if at is None:
                raise ValueError("missing saved-place timestamp")
            return await self.writes.graph.aggregate_planes(
                conn, table, unit=unit, key=(row["circle_id"], row["place_id"], unit),
                occurred_at=at, asserted_at=at, minimum="internal",
                persons=self.scope.circle_persons(row["circle_id"], unit=unit),
            )
        if family.startswith("rollup_"):
            if family == "rollup_response":
                key = (item_graph_id("messages", (row["message_id"],), unit),)
            else:
                key = tuple(row[name] for name in _UNIT_ROLLUP_KEYS[family])
            inputs = await self.writes.rollup_inputs(conn, table, row, row["build_id"])
            return await self.writes.annotate(
                conn, table, row, unit=unit, key=key, inputs=inputs, live=False,
            )
        copies = {}
        if row.get("place_id") is not None:
            copies["place_id"] = await self._link(
                conn, PRESENCE_DERIVED_PLACES, row["place_id"], unit,
            )
        if row.get("corridor_id") is not None:
            copies["corridor_id"] = await self._link(
                conn, PRESENCE_CORRIDORS, row["corridor_id"], unit,
            )
        return {
            **copies,
            **await self.writes.presence_planes(conn, table, {**row, **copies}, unit=unit),
        }

    async def _platform_head(
        self, conn: AsyncConnection, table: sa.Table, row: RowData, unit: str,
    ) -> dict[str, Any]:
        obs = PLATFORM_OBSERVATION
        key = tuple(row[name] for name in _HEAD_KEY)
        inputs = (await conn.execute(
            sa.select(obs).where(
                *(obs.c[name] == row[name] for name in _HEAD_KEY),
                obs.c.unit == unit, obs.c.state == "live",
                obs.c.id <= (row["head_observation_id"] or 0),
            )
        )).mappings().all()
        return await self.writes.graph.aggregate_planes(
            conn, table, unit=unit, key=(*key, unit), occurred_at=epoch_ms(row["observed_ms"]),
            asserted_at=row["updated_at"], room=row["conversation_id"], inputs=inputs,
            persons={person for source in inputs for person in source["persons"]},
        )
