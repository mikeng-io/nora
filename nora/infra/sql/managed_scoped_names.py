"""The `SqlBindingStore` scoped-name row — the canonical person's
descriptive nickname inside ONE exact scope (`authorization.md`).

The caller's journal owns the ordering and its `operation_id` is the
row's provenance; the row itself is the current value only. Descriptive
metadata: never an identifier, grant or linkage proof, and no revision
bumps — the global name stays on `managed_person.display_name`.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.managed import (
    ManagedError,
    Outcome,
    PersonLifecycle,
    complete,
    denied,
)
from nora.domain.management import Scope
from nora.infra.sql.managed_registration import _canonical_person
from nora.infra.sql.schema_managed import (
    MANAGED_PERSON,
    MANAGED_SCOPED_NAME,
)


async def set_scoped_name(
    conn: AsyncConnection, *, person_id: str, scope: Scope, nickname: str,
    operation_id: str,
) -> Outcome:
    """Upsert the (person, scope) nickname inside the caller's transaction."""
    if not nickname.strip():
        raise ManagedError("a scoped nickname cannot be blank")
    canonical = await _canonical_person(conn, person_id)
    if canonical is None:
        return denied("absent-person")
    lifecycle = await conn.scalar(
        sa.select(MANAGED_PERSON.c.lifecycle).where(
            MANAGED_PERSON.c.person_id == canonical
        )
    )
    if lifecycle != PersonLifecycle.ACTIVE.value:
        return denied("person-suspended")
    await conn.execute(
        pg_insert(MANAGED_SCOPED_NAME)
        .values(
            person_id=canonical,
            scope_kind=scope.kind.value,
            scope_id=scope.id,
            nickname=nickname,
            operation_id=operation_id,
        )
        .on_conflict_do_update(
            index_elements=["person_id", "scope_kind", "scope_id"],
            set_={
                "nickname": nickname,
                "operation_id": operation_id,
                "updated_at": sa.func.now(),
            },
        )
    )
    return complete()


async def scoped_name_for(
    conn: AsyncConnection, *, person_id: str, scope: Scope
) -> str | None:
    """The nickname under the EXACT scope — a retired id folds first, an
    absent row is `None` (never another scope's or a stale name)."""
    canonical = await _canonical_person(conn, person_id)
    if canonical is None:
        return None
    return await conn.scalar(
        sa.select(MANAGED_SCOPED_NAME.c.nickname).where(
            MANAGED_SCOPED_NAME.c.person_id == canonical,
            MANAGED_SCOPED_NAME.c.scope_kind == scope.kind.value,
            MANAGED_SCOPED_NAME.c.scope_id == scope.id,
        )
    )
