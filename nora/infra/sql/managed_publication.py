"""Atomic person consolidation — the write side of `identity.md`'s stable
aliases (`design/managed-identity-access/`).

One transaction retires the former person: accounts move to the survivor
keeping their account rows and identifiers (ownership provenance stays
exact), membership supports merge per scope, authority blocks follow the
canonical person, the former id becomes an alias and its person row retires
— cascading its publication holds, which the owning operation holds open on
the survivor instead. Historical rows (operations, decisions, holds of other
operations) are never rewritten or copied.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Row
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.managed import (
    MembershipState,
    Outcome,
    complete,
    denied,
)
from nora.domain.management import Scope
from nora.infra.sql.schema_managed import (
    MANAGED_ACCOUNT,
    MANAGED_ALIAS,
    MANAGED_AUTHORITY_BLOCK,
    MANAGED_IDENTITY_HOLD,
    MANAGED_MEMBERSHIP,
    MANAGED_MEMBERSHIP_SUPPORT,
    MANAGED_PERSON,
    MANAGED_SCOPED_NAME,
)


async def drop_membership_support(
    conn: AsyncConnection, *, person_id: str, scope: Scope
) -> None:
    """Delete every support row behind one (person, scope) standing."""
    await conn.execute(sa.delete(MANAGED_MEMBERSHIP_SUPPORT).where(
        MANAGED_MEMBERSHIP_SUPPORT.c.person_id == person_id,
        MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind == scope.kind.value,
        MANAGED_MEMBERSHIP_SUPPORT.c.scope_id == scope.id,
    ))


async def retract_membership_support(
    conn: AsyncConnection, *, person_id: str, scope: Scope, source: str
) -> None:
    """Retract ONE source's support; the membership row falls `left`
    when its last support is gone (the roster-departure verb)."""
    await conn.execute(sa.delete(MANAGED_MEMBERSHIP_SUPPORT).where(
        MANAGED_MEMBERSHIP_SUPPORT.c.person_id == person_id,
        MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind == scope.kind.value,
        MANAGED_MEMBERSHIP_SUPPORT.c.scope_id == scope.id,
        MANAGED_MEMBERSHIP_SUPPORT.c.source == source,
    ))
    remaining = await conn.scalar(sa.select(
        sa.exists(MANAGED_MEMBERSHIP_SUPPORT).where(
            MANAGED_MEMBERSHIP_SUPPORT.c.person_id == person_id,
            MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind == scope.kind.value,
            MANAGED_MEMBERSHIP_SUPPORT.c.scope_id == scope.id,
        )
    ))
    if remaining:
        return
    await conn.execute(
        pg_insert(MANAGED_MEMBERSHIP)
        .values(
            person_id=person_id,
            scope_kind=scope.kind.value,
            scope_id=scope.id,
            state=MembershipState.LEFT.value,
            source=source,
        )
        .on_conflict_do_update(
            index_elements=["person_id", "scope_kind", "scope_id"],
            set_={
                "state": MembershipState.LEFT.value,
                "source": source,
                "revision": MANAGED_MEMBERSHIP.c.revision + 1,
                "updated_at": sa.func.now(),
            },
        )
    )


async def supports_for(
    conn: AsyncConnection, rows: Sequence[Row[Any]]
) -> dict[tuple[str, str, str], frozenset[str]]:
    """The live supports for the given membership rows, keyed by
    (person, scope_kind, scope_id) — `members_of` returns many persons
    on one scope and must not merge their sources."""
    keys = {(r.person_id, r.scope_kind, r.scope_id) for r in rows}
    out: dict[tuple[str, str, str], set[str]] = {}
    for person_id, kind, scope_id in keys:
        for row in (
            await conn.execute(
                sa.select(MANAGED_MEMBERSHIP_SUPPORT.c.source).where(
                    MANAGED_MEMBERSHIP_SUPPORT.c.person_id == person_id,
                    MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind == kind,
                    MANAGED_MEMBERSHIP_SUPPORT.c.scope_id == scope_id,
                )
            )
        ).fetchall():
            out.setdefault((person_id, kind, scope_id), set()).add(row.source)
    return {key: frozenset(value) for key, value in out.items()}


async def _support_scopes(
    conn: AsyncConnection, person_id: str
) -> set[tuple[str, str]]:
    rows = (await conn.execute(
        sa.select(MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind,
                  MANAGED_MEMBERSHIP_SUPPORT.c.scope_id)
        .where(MANAGED_MEMBERSHIP_SUPPORT.c.person_id == person_id)
    )).all()
    return {(row.scope_kind, row.scope_id) for row in rows}


async def _has_support(
    conn: AsyncConnection, person_id: str, scope_kind: str, scope_id: str
) -> bool:
    return bool(await conn.scalar(sa.select(
        sa.exists(MANAGED_MEMBERSHIP_SUPPORT).where(
            MANAGED_MEMBERSHIP_SUPPORT.c.person_id == person_id,
            MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind == scope_kind,
            MANAGED_MEMBERSHIP_SUPPORT.c.scope_id == scope_id,
        )
    )))


async def _merge_membership_rows(
    conn: AsyncConnection, survivor: str, former: str
) -> None:
    """Rewrite the survivor's membership rows from the merged supports."""
    former_rows = (await conn.execute(
        sa.select(MANAGED_MEMBERSHIP)
        .where(MANAGED_MEMBERSHIP.c.person_id == former)
        .with_for_update()
    )).all()
    scopes = {(row.scope_kind, row.scope_id) for row in former_rows}
    scopes |= await _support_scopes(conn, former)
    scopes |= {(row.scope_kind, row.scope_id) for row in (await conn.execute(
        sa.select(MANAGED_MEMBERSHIP.c.scope_kind, MANAGED_MEMBERSHIP.c.scope_id)
        .where(MANAGED_MEMBERSHIP.c.person_id == survivor)
    )).all()}
    await conn.execute(sa.delete(MANAGED_MEMBERSHIP).where(
        MANAGED_MEMBERSHIP.c.person_id == former,
    ))
    await conn.execute(
        pg_insert(MANAGED_MEMBERSHIP_SUPPORT)
        .from_select(
            ["person_id", "scope_kind", "scope_id", "source"],
            sa.select(
                sa.literal(survivor), MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind,
                MANAGED_MEMBERSHIP_SUPPORT.c.scope_id,
                MANAGED_MEMBERSHIP_SUPPORT.c.source,
            ).where(MANAGED_MEMBERSHIP_SUPPORT.c.person_id == former),
        )
        .on_conflict_do_nothing()
    )
    await conn.execute(sa.delete(MANAGED_MEMBERSHIP_SUPPORT).where(
        MANAGED_MEMBERSHIP_SUPPORT.c.person_id == former,
    ))
    former_by_scope = {(r.scope_kind, r.scope_id): r for r in former_rows}
    survivor_rows = {(r.scope_kind, r.scope_id): r for r in (await conn.execute(
        sa.select(MANAGED_MEMBERSHIP)
        .where(MANAGED_MEMBERSHIP.c.person_id == survivor)
        .with_for_update()
    )).all()}
    for scope_kind, scope_id in scopes:
        member = await _has_support(conn, survivor, scope_kind, scope_id)
        existing = survivor_rows.get((scope_kind, scope_id))
        fallen = former_by_scope.get((scope_kind, scope_id))
        if existing is not None:
            await conn.execute(sa.update(MANAGED_MEMBERSHIP).where(
                MANAGED_MEMBERSHIP.c.person_id == survivor,
                MANAGED_MEMBERSHIP.c.scope_kind == scope_kind,
                MANAGED_MEMBERSHIP.c.scope_id == scope_id,
            ).values(
                state="member" if member else "left",
                revision=existing.revision + 1,
                updated_at=sa.func.now(),
            ))
        else:
            await conn.execute(sa.insert(MANAGED_MEMBERSHIP).values(
                person_id=survivor, scope_kind=scope_kind, scope_id=scope_id,
                state="member" if member else "left",
                source=(fallen.source if fallen is not None
                        else "consolidation"),
                revision=(fallen.revision + 1 if fallen is not None else 1),
            ))


async def consolidate_people(
    conn: AsyncConnection, *, survivor: str, former: str
) -> Outcome:
    """The whole retirement inside the caller's transaction — the caller
    holds the store's mutation lock, so a concurrent consolidate on the
    same pair cannot interleave."""
    rows = (await conn.execute(
        sa.select(MANAGED_PERSON)
        .where(MANAGED_PERSON.c.person_id.in_((survivor, former)))
        .order_by(MANAGED_PERSON.c.person_id)
        .with_for_update()
    )).all()
    found = {row.person_id: row for row in rows}
    if survivor not in found:
        return denied("absent-survivor")
    if former not in found:
        alias = await conn.scalar(sa.select(MANAGED_ALIAS.c.person_id).where(
            MANAGED_ALIAS.c.former_person_id == former,
        ))
        return complete() if alias == survivor else denied("absent-former")
    if (found[survivor].lifecycle != "active"
            or found[former].lifecycle != "active"):
        return denied("person-inactive")
    await conn.execute(sa.update(MANAGED_ACCOUNT).where(
        MANAGED_ACCOUNT.c.person_id == former,
    ).values(
        person_id=survivor,
        revision=MANAGED_ACCOUNT.c.revision + 1,
    ))
    await _merge_membership_rows(conn, survivor, former)
    await conn.execute(
        pg_insert(MANAGED_AUTHORITY_BLOCK)
        .from_select(
            ["person_id", "scope_kind", "scope_id", "reason", "operation_id"],
            sa.select(
                sa.literal(survivor), MANAGED_AUTHORITY_BLOCK.c.scope_kind,
                MANAGED_AUTHORITY_BLOCK.c.scope_id,
                MANAGED_AUTHORITY_BLOCK.c.reason,
                MANAGED_AUTHORITY_BLOCK.c.operation_id,
            ).where(MANAGED_AUTHORITY_BLOCK.c.person_id == former),
        )
        .on_conflict_do_nothing()
    )
    await conn.execute(sa.delete(MANAGED_AUTHORITY_BLOCK).where(
        MANAGED_AUTHORITY_BLOCK.c.person_id == former,
    ))
    # Holds are FK-CASCADE on person: another operation's hold on the
    # former must survive the retirement, re-expressed on the survivor.
    await conn.execute(
        pg_insert(MANAGED_IDENTITY_HOLD)
        .from_select(
            ["person_id", "operation_id"],
            sa.select(
                sa.literal(survivor), MANAGED_IDENTITY_HOLD.c.operation_id,
            ).where(MANAGED_IDENTITY_HOLD.c.person_id == former),
        )
        .on_conflict_do_nothing()
    )
    await conn.execute(sa.delete(MANAGED_IDENTITY_HOLD).where(
        MANAGED_IDENTITY_HOLD.c.person_id == former,
    ))
    # Scoped nicknames follow the canonical person like authority blocks;
    # on a same-scope collision the survivor's own name wins.
    await conn.execute(
        pg_insert(MANAGED_SCOPED_NAME)
        .from_select(
            ["person_id", "scope_kind", "scope_id", "nickname",
             "operation_id"],
            sa.select(
                sa.literal(survivor), MANAGED_SCOPED_NAME.c.scope_kind,
                MANAGED_SCOPED_NAME.c.scope_id,
                MANAGED_SCOPED_NAME.c.nickname,
                MANAGED_SCOPED_NAME.c.operation_id,
            ).where(MANAGED_SCOPED_NAME.c.person_id == former),
        )
        .on_conflict_do_nothing()
    )
    await conn.execute(sa.delete(MANAGED_SCOPED_NAME).where(
        MANAGED_SCOPED_NAME.c.person_id == former,
    ))
    await conn.execute(sa.insert(MANAGED_ALIAS).values(
        former_person_id=former, person_id=survivor,
    ))
    await conn.execute(sa.delete(MANAGED_PERSON).where(
        MANAGED_PERSON.c.person_id == former,
    ))
    await conn.execute(sa.update(MANAGED_PERSON).where(
        MANAGED_PERSON.c.person_id == survivor,
    ).values(
        revision=MANAGED_PERSON.c.revision + 1,
        updated_at=sa.func.now(),
    ))
    return complete()
