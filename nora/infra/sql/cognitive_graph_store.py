"""The Cognitive Graph's one Postgres store (design/cognition/storage.md):
create a registry row and its kind row, append a revision against a base, and
tombstone — each in a single transaction, so no half-write exists."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_registration import (
    Exposure,
    InputScope,
    StaleBaseError,
    aggregate_graph_id,
    inherited_scope,
)
from nora.domain.lineage import Lineage
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    ENTITY,
    INTENTION,
    REGISTRY,
)

RowData = Mapping[str, Any] | sa.RowMapping


#: The content a tombstone clears per kind; identity, basis, scope,
#: provenance, times and lifecycle stay.
_CONTENT: dict[sa.Table, tuple[str, ...]] = {
    ENTITY: ("name", "aliases", "pointer_kind", "pointer_ref"),
    CLAIM: ("content", "prediction", "window_end"),
    INTENTION: (
        "done_when_form",
        "done_when",
        "holds_while_form",
        "holds_while",
        "drop_when_form",
        "drop_when",
        "trigger",
    ),
    EDGE: (),
}

_SOURCE_CONTENT: dict[str, tuple[str, ...]] = {
    "presence_places": ("name", "lat", "lng", "radius_m", "address"),
    "platform_object_state": ("source_state", "source_position", "coverage", "head_observation_id"),
    "messages": ("body", "raw_json", "media_json", "external_files_json"),
    "message_edit": ("body", "raw_json", "mentions_json", "mention_persons_json"),
    "message_delete": ("raw_json",),
    "message_reaction": ("raw_json",),
    "social_item": ("caption", "alt_text", "location", "media_ref"),
    "social_item_count": ("likes", "comments", "reposts"),
    "social_item_state": ("reason", "place"),
    "social_poll": (
        "latest_reel_media",
        "media_count",
        "total_clips_count",
        "cursor",
        "end_reason",
    ),
    "fetched_pages": ("blob_key", "provenance"),
    "presence_fixes": ("lat", "lng", "address", "speed_kmh", "accuracy_m", "battery"),
    "platform_observation": ("payload", "coverage"),
    "source_health": ("detail",),
    "presence_stays": ("lat", "lng", "radius_m", "fix_count", "place_id", "address"),
    "presence_trips": ("distance_m", "avg_speed_kmh", "fix_count", "sequence", "corridor_id"),
    "presence_derived_places": ("lat", "lng", "radius_m", "place_confidence", "label"),
    "presence_corridors": ("sequence", "sample_count"),
    "presence_occupancy": ("dwell_ms",),
    "presence_place_rhythm": (
        "mean_arrival_minute",
        "mean_departure_minute",
        "duration_median_ms",
        "duration_mad_ms",
        "sample_count",
    ),
    "rollup_colocation": (
        "lat",
        "lng",
        "radius_m",
        "place_label",
        "at_home_of",
        "homes_unknown",
        "stay_count",
    ),
    "rollup_outing": ("lat", "lng", "place_label", "district", "city"),
    "rollup_daybook": (
        "stays",
        "stay_ms",
        "home_ms",
        "colocation_ms",
        "with_persons",
        "outings",
        "rooms",
        "messages",
        "reactions",
        "items_posted",
    ),
    "rollup_tie": (
        "replies_ab",
        "replies_ba",
        "mentions_ab",
        "mentions_ba",
        "reactions_ab",
        "reactions_ba",
        "colocation_ms",
    ),
    "rollup_response": (
        "replies",
        "reactions",
        "edits",
        "follows",
        "responders",
        "named_persons",
        "intent",
        "outcome",
    ),
}


#: The state a revision stamps on its base. An entity's lifecycle has no
#: superseded state (graph.md «Five kinds»), so `replaces` alone records
#: that the base was replaced.
_SUCCESSOR: dict[sa.Table, str | None] = {
    ENTITY: None,
    CLAIM: "superseded",
    INTENTION: "superseded",
    EDGE: "superseded",
}


class GraphWriteError(Exception):
    """An admission transaction failed, possibly during commit."""

    def __init__(self, *, commit_uncertain: bool) -> None:
        self.commit_uncertain = commit_uncertain


class SqlCognitiveGraph:
    """Writes only; the reads a subject makes are their own stores."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, table: sa.Table, row: Mapping[str, Any]) -> None:
        """The registry row and the kind row in one transaction."""
        async with self._engine.begin() as conn:
            await self._insert(conn, table, row)

    @staticmethod
    async def _insert(
        conn: AsyncConnection, table: sa.Table, row: Mapping[str, Any],
    ) -> None:
        await conn.execute(
            sa.insert(REGISTRY).values(id=row["id"], kind=row["kind"], unit=row["unit"])
        )
        try:
            await conn.execute(sa.insert(table).values(dict(row)))
        except sa.exc.IntegrityError as exc:
            orig = getattr(exc, "orig", None)
            driver = getattr(orig, "orig", None) or orig
            name = (
                getattr(driver, "constraint_name", None)
                or getattr(getattr(driver, "diag", None), "constraint_name", "")
            )
            if row.get("replaces") is not None and name == f"uq_{table.name}_replaces":
                raise StaleBaseError(str(row["replaces"])) from exc
            raise

    async def revise(self, table: sa.Table, row: Mapping[str, Any]) -> None:
        """Append a revision and mark the replaced row in one transaction."""
        async with self._engine.begin() as conn:
            await self._revise(conn, table, row)

    async def _revise(
        self, conn: AsyncConnection, table: sa.Table, row: Mapping[str, Any],
    ) -> None:
        await self._insert(conn, table, row)
        successor = _SUCCESSOR[table]
        if successor is not None:
            await conn.execute(
                sa.update(table)
                .where(table.c.id == row["replaces"], table.c.unit == row["unit"])
                .values(state=successor)
            )

    async def write_admission(
        self,
        table: sa.Table,
        row: Mapping[str, Any],
        edges: Sequence[Mapping[str, Any]],
        *,
        base_state: str | None = None,
        retire: bool = False,
        merge: tuple[str, str] | None = None,
        after_write: Callable[[AsyncConnection], Awaitable[None]] | None = None,
    ) -> None:
        """Write a model proposal and all its edges as one graph transaction."""
        committing = False
        try:
            async with self._engine.begin() as conn:
                if merge is not None:
                    await self._merge(conn, row, merge)
                if base_state is None:
                    await self._insert(conn, table, row)
                else:
                    base = await self._admission_base(conn, table, row, base_state)
                    if retire:
                        row = dict(base) | dict(row)
                    await self._revise(conn, table, row)
                for edge in edges:
                    await self._insert(conn, EDGE, edge)
                if after_write is not None:
                    await after_write(conn)
                committing = True
        except sa.exc.SQLAlchemyError as exc:
            raise GraphWriteError(commit_uncertain=committing) from exc

    @staticmethod
    async def _admission_base(
        conn: AsyncConnection, table: sa.Table, row: Mapping[str, Any], state: str,
    ) -> sa.RowMapping:
        unit, base_id = row["unit"], row["replaces"]
        current = (await conn.execute(
            sa.select(table).where(table.c.unit == unit, table.c.id == base_id).with_for_update()
        )).mappings().one_or_none()
        if current is not None and current["state"] == state:
            return current
        successor = await conn.scalar(
            sa.select(table.c.id).where(table.c.unit == unit, table.c.replaces == base_id)
        )
        if successor is not None:
            raise StaleBaseError(str(base_id))
        raise GraphWriteError(commit_uncertain=False)

    @staticmethod
    async def _merge(
        conn: AsyncConnection, edge: Mapping[str, Any], endpoints: tuple[str, str],
    ) -> None:
        unit = edge["unit"]
        locked = (await conn.execute(
            sa.select(ENTITY.c.id, ENTITY.c.state, ENTITY.c.basis, ENTITY.c.pointer_kind)
            .where(ENTITY.c.unit == unit, ENTITY.c.id.in_(endpoints))
            .order_by(ENTITY.c.id).with_for_update()
        )).all()
        replaced = await conn.scalar(
            sa.select(ENTITY.c.replaces)
            .where(ENTITY.c.unit == unit, ENTITY.c.replaces.in_(endpoints))
            .order_by(ENTITY.c.replaces).limit(1)
        )
        if replaced is not None:
            raise StaleBaseError(replaced)
        if len(locked) != 2 or any(
            row.state != "admitted" or row.basis != "inferred" or row.pointer_kind is not None
            for row in locked
        ):
            raise GraphWriteError(commit_uncertain=False)
        await conn.execute(
            sa.update(ENTITY).where(ENTITY.c.unit == unit, ENTITY.c.id == endpoints[0])
            .values(
                state="merged", exposure=edge["exposure"], lineage=edge["lineage"],
                excluded_persons=edge["excluded_persons"], origin_room=edge["origin_room"],
            )
        )

    async def end_intention_in(
        self, conn: AsyncConnection, intention: RowData, ending: str, *,
        witness_id: str, witness_at: datetime, witness_scope: Mapping[str, Any],
    ) -> None:
        scope = inherited_scope(
            (InputScope(tuple(row["excluded_persons"]), row["exposure"],
                        Lineage.from_json(row["lineage"])) for row in (intention, witness_scope)),
            build_lineage=Lineage(), minimum="internal",
        )
        unit, intention_id = intention["unit"], intention["id"]
        ended = await conn.scalar(sa.update(INTENTION).where(
            INTENTION.c.unit == unit, INTENTION.c.id == intention_id, INTENTION.c.state == "open",
        ).values(state="ended", ending=ending, ended_at=sa.func.clock_timestamp())
          .returning(INTENTION.c.ended_at))
        if ended is None:
            return
        await self._insert(conn, EDGE, {
            "id": f"edge:{uuid4().hex}", "unit": unit, "kind": "edge",
            "src_id": intention_id, "dst_id": witness_id, "type": "closed_by",
            "basis": "computed", "state": "live", "writer": "ConditionMatcher",
            "origin_room": intention["origin_room"], "occurred_at": witness_at,
            "asserted_at": ended, "persons": intention["persons"],
            "exposure": scope.exposure, "lineage": scope.lineage.to_json(),
            "excluded_persons": list(scope.excluded_persons),
        })
        if intention["form"] == "maintain":
            await conn.execute(sa.update(EDGE).where(
                EDGE.c.unit == unit, EDGE.c.src_id == intention_id,
                EDGE.c.type == "watches", EDGE.c.state == "live",
            ).values(state="retired"))

    async def aggregate_planes(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        *,
        unit: str | None,
        key: tuple[Any, ...],
        occurred_at: datetime | None,
        asserted_at: datetime,
        interval: tuple[datetime, datetime] | None = None,
        persons: Iterable[str] = (),
        room: str | None = None,
        minimum: Exposure = "external",
        build_lineage: Any = None,
        inputs: Iterable[RowData] = (),
        revisions: Mapping[str, Any] | None = None,
        existing_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare aggregate planes and register their identity in the caller's transaction."""
        scope = inherited_scope(
            (
                InputScope(
                    tuple(row["excluded_persons"]),
                    row["exposure"],
                    Lineage.from_json(row["lineage"]),
                )
                for row in inputs
            ),
            build_lineage=Lineage.from_json(build_lineage),
            minimum=minimum,
        )
        graph_id = None
        if unit is not None:
            graph_id = existing_id or aggregate_graph_id(table.name, key, interval)
            stmt = pg_insert(REGISTRY).values(id=graph_id, unit=unit, kind="aggregate")
            await conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["id", "unit"],
                    set_={"state": "live", "content_hash": None, "tombstoned_at": None},
                    where=(REGISTRY.c.kind == "aggregate") & (REGISTRY.c.state == "tombstoned"),
                )
            )
        return {
            "graph_id": graph_id,
            "unit": unit,
            "state": "live",
            "persons": sorted(set(persons)),
            "excluded_persons": list(scope.excluded_persons),
            "exposure": scope.exposure,
            "lineage": scope.lineage.to_json(),
            "origin_room": room,
            "occurred_at": occurred_at,
            "asserted_at": asserted_at,
            "valid_from": interval[0] if interval else None,
            "valid_to": interval[1] if interval else None,
            **(revisions or {}),
        }

    async def tombstone(
        self,
        table: sa.Table,
        *,
        row_id: str,
        unit: str,
        content_hash: str,
    ) -> None:
        """Tombstone the registry and kind row together, retaining the edge endpoint."""
        async with self._engine.begin() as conn:
            await self.tombstone_in(
                conn,
                table,
                row_id=row_id,
                unit=unit,
                content_hash=content_hash,
            )

    async def tombstone_in(
        self,
        conn: AsyncConnection,
        table: sa.Table,
        *,
        row_id: str,
        unit: str,
        content_hash: str,
    ) -> None:
        """Tombstone within an existing source transaction."""
        if table in _CONTENT:
            identity = table.c.id
            clear = dict.fromkeys(_CONTENT[table], sa.null())
        elif "graph_id" in table.c and table.name in _SOURCE_CONTENT:
            identity = table.c.graph_id
            clear = dict.fromkeys(_SOURCE_CONTENT[table.name], sa.null())
        else:
            raise ValueError(f"cannot tombstone {table.name}")
        await conn.execute(
            sa.update(REGISTRY)
            .where(REGISTRY.c.id == row_id, REGISTRY.c.unit == unit)
            .values(
                state="tombstoned",
                content_hash=content_hash,
                tombstoned_at=sa.func.now(),
            )
        )
        await conn.execute(
            sa.update(table)
            .where(identity == row_id, table.c.unit == unit)
            .values(state="tombstoned", **clear)
        )
