"""The Cognitive Graph's one Postgres store (design/cognition/storage.md):
create a registry row and its kind row, append a revision against a base, and
tombstone — each in a single transaction, so no half-write exists."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    ENTITY,
    INTENTION,
    REGISTRY,
)
from nora.memory.domain.graph import StaleBaseError

#: The content a tombstone clears per kind; identity, basis, scope,
#: provenance, times and lifecycle stay.
_CONTENT: dict[sa.Table, tuple[str, ...]] = {
    ENTITY: ("name", "aliases", "pointer_kind", "pointer_ref"),
    CLAIM: ("content", "prediction", "window_end"),
    INTENTION: (
        "done_when_form", "done_when", "holds_while_form", "holds_while",
        "drop_when_form", "drop_when", "trigger",
    ),
    EDGE: (),
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
            await conn.execute(sa.insert(REGISTRY).values(
                id=row["id"], kind=row["kind"], unit=row["unit"],
            ))
            await conn.execute(sa.insert(table).values(dict(row)))

    async def revise(self, table: sa.Table, row: Mapping[str, Any]) -> None:
        """A revision: the new row's `replaces` names its base, and the base
        takes its kind's successor state, in one transaction. The `replaces`
        unique constraint is the stale-base refusal: a base that already has
        a successor cannot take a second child."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(REGISTRY).values(
                id=row["id"], kind=row["kind"], unit=row["unit"],
            ))
            try:
                await conn.execute(sa.insert(table).values(dict(row)))
            except sa.exc.IntegrityError as exc:
                orig = getattr(exc, "orig", None)
                driver = getattr(orig, "orig", None) or orig
                name = getattr(driver, "constraint_name", None) or getattr(
                    getattr(driver, "diag", None), "constraint_name", ""
                ) or ""
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

    async def tombstone(
        self, table: sa.Table, *, row_id: str, unit: str, content_hash: str,
    ) -> None:
        """The registry row and the kind row tombstoned together in one
        transaction: content removed; id, kind, unit, time and a content hash
        kept; edges still resolve."""
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(REGISTRY)
                .where(REGISTRY.c.id == row_id, REGISTRY.c.unit == unit)
                .values(
                    state="tombstoned", content_hash=content_hash,
                    tombstoned_at=sa.func.now(),
                )
            )
            await conn.execute(
                sa.update(table)
                .where(table.c.id == row_id, table.c.unit == unit)
                .values(state="tombstoned",
                        **dict.fromkeys(_CONTENT[table]))
            )
