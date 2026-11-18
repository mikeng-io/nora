"""The Cognitive Graph's one Postgres store (design/cognition/storage.md):
create a registry row and its kind row, append a revision against a base, and
tombstone — each in a single transaction, so no half-write exists."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

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


class SqlCognitiveGraph:
    """Writes only; the reads a subject makes are their own stores."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, table: sa.Table, row: Mapping[str, Any]) -> None:
        """The registry row and the kind row in one transaction."""
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(REGISTRY).values(
                    id=row["id"],
                    kind=row["kind"],
                    unit=row["unit"],
                )
            )
            await conn.execute(sa.insert(table).values(dict(row)))

    async def revise(self, table: sa.Table, row: Mapping[str, Any]) -> None:
        """A revision: the new row's `replaces` names its base, and the base
        takes its kind's successor state, in one transaction. The `replaces`
        unique constraint is the stale-base refusal: a base that already has
        a successor cannot take a second child."""
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(REGISTRY).values(
                    id=row["id"],
                    kind=row["kind"],
                    unit=row["unit"],
                )
            )
            try:
                await conn.execute(sa.insert(table).values(dict(row)))
            except sa.exc.IntegrityError as exc:
                orig = getattr(exc, "orig", None)
                driver = getattr(orig, "orig", None) or orig
                name = (
                    getattr(driver, "constraint_name", None)
                    or getattr(getattr(driver, "diag", None), "constraint_name", "")
                    or ""
                )
                if name == f"uq_{table.name}_replaces":
                    raise StaleBaseError(str(row["replaces"])) from exc
                raise
            successor = _SUCCESSOR[table]
            if successor is not None:
                await conn.execute(
                    sa.update(table)
                    .where(
                        table.c.id == row["replaces"],
                        table.c.unit == row["unit"],
                    )
                    .values(state=successor)
                )

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
        inputs: Iterable[Mapping[str, Any]] = (),
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
