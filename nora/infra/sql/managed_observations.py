"""The durable per-source roster observation mark (`management.md`).

`SqlBindingStore`'s two observation methods delegate here so the store's
file stays inside the size limit; every call is one statement inside the
store's own connection pattern, serialized by the mutation gate's
advisory lock like every other write."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nora.domain.managed import Outcome, SourceObservation, complete
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql.schema_managed import MANAGED_OBSERVATION


def _scope_from(kind: str, scope_id: str) -> Scope:
    return Scope(kind=ScopeKind(kind), id=scope_id)


async def observation_for(
    engine: Any, *, service: str, collection: str, scope: Scope
) -> SourceObservation | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.select(MANAGED_OBSERVATION).where(
                    MANAGED_OBSERVATION.c.service == service,
                    MANAGED_OBSERVATION.c.collection == collection,
                    MANAGED_OBSERVATION.c.scope_kind == scope.kind.value,
                    MANAGED_OBSERVATION.c.scope_id == scope.id,
                )
            )
        ).fetchone()
        if row is None:
            return None
        return SourceObservation(
            service=row.service, collection=row.collection,
            scope=_scope_from(row.scope_kind, row.scope_id),
            observed_ms=row.observed_ms,
            operation_id=row.operation_id, effect=row.effect,
        )


async def record_observation(
    engine: Any, observation: SourceObservation
) -> Outcome:
    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(MANAGED_OBSERVATION)
            .values(
                service=observation.service,
                collection=observation.collection,
                scope_kind=observation.scope.kind.value,
                scope_id=observation.scope.id,
                observed_ms=observation.observed_ms,
                operation_id=observation.operation_id,
                effect=observation.effect,
            )
            .on_conflict_do_update(
                index_elements=[
                    "service", "collection", "scope_kind", "scope_id",
                ],
                set_={
                    "observed_ms": observation.observed_ms,
                    "operation_id": observation.operation_id,
                    "effect": observation.effect,
                    "updated_at": sa.func.now(),
                },
            )
        )
        return complete()
