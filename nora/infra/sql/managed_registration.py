"""The `SqlBindingStore.register` transaction's named steps — person,
identifier ownership, re-binding and the account row, all run inside the
caller's one `engine.begin()` (`design/managed-identity-access/`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    IdentifierConflictError,
    ManagedAccount,
    ManagedError,
    ManagedPerson,
    Outcome,
    complete,
    denied,
)
from nora.infra.sql.schema_managed import (
    MANAGED_ACCOUNT,
    MANAGED_ALIAS,
    MANAGED_IDENTIFIER,
    MANAGED_PERSON,
)

_OwnerRow = Row[Any, Any, Any]

# PostgreSQL SQLSTATE 23505, unique_violation — matched EXACTLY, the same
# rule `nora.infra.sql.reminders` applies: a CHECK violation is also an
# IntegrityError and must not be misread as a duplicate.
_PG_UNIQUE_VIOLATION = "23505"


async def _insert_person(conn: AsyncConnection, person: ManagedPerson) -> None:
    # Idempotent insert — a replay never touches an existing person's
    # lifecycle (a suspension is not undone by re-registration) or its
    # Kratos correlation.
    await conn.execute(
        pg_insert(MANAGED_PERSON)
        .values(
            person_id=person.person_id,
            lifecycle=person.lifecycle.value,
            kratos_identity_id=person.kratos_identity_id,
            display_name=person.display_name,
        )
        .on_conflict_do_nothing(index_elements=["person_id"])
    )


async def _identifier_owners(
    conn: AsyncConnection, identifiers: Iterable[Identifier]
) -> dict[Identifier, _OwnerRow | None]:
    """Current owner of every claimed identifier, read in this transaction."""
    owners: dict[Identifier, _OwnerRow | None] = {}
    for ident in identifiers:
        owners[ident] = (
            await conn.execute(
                sa.select(
                    MANAGED_IDENTIFIER.c.account_id,
                    MANAGED_ACCOUNT.c.person_id,
                    MANAGED_ACCOUNT.c.state,
                )
                .select_from(
                    MANAGED_IDENTIFIER.join(
                        MANAGED_ACCOUNT,
                        MANAGED_IDENTIFIER.c.account_id
                        == MANAGED_ACCOUNT.c.account_id,
                    )
                )
                .where(
                    MANAGED_IDENTIFIER.c.platform == ident.platform,
                    MANAGED_IDENTIFIER.c.kind == ident.kind,
                    MANAGED_IDENTIFIER.c.value == ident.value,
                )
            )
        ).fetchone()
    return owners


def _check_ownership(
    owners: dict[Identifier, _OwnerRow | None],
    person: ManagedPerson,
    account: ManagedAccount,
) -> None:
    # An identifier owned by a DIFFERENT person is a conflict forever — a
    # link/unlink is the only path that changes ownership. A different BOUND
    # account on the same person holds it too; a REVOKED one may re-bind.
    for ident, owner in owners.items():
        if owner is None:
            continue


async def _rebind_revoked(
    conn: AsyncConnection,
    owners: dict[Identifier, _OwnerRow | None],
    account: ManagedAccount,
) -> None:
    # Re-bind what a REVOKED binding on this person was holding: the same
    # account revives, a different account takes over the identifiers.
    revoked_ids = {
        str(o.account_id)
        for o in owners.values()
        if o is not None and o.state == AccountState.REVOKED.value
    }
    if account.account_id in revoked_ids:
        await conn.execute(
            sa.update(MANAGED_ACCOUNT)
            .where(
                MANAGED_ACCOUNT.c.account_id == account.account_id,
                MANAGED_ACCOUNT.c.state == AccountState.REVOKED.value,
            )
            .values(
                state=AccountState.BOUND.value,
                revoked_at=None,
                revision=MANAGED_ACCOUNT.c.revision + 1,
            )
        )
    elif revoked_ids:
        await conn.execute(
            sa.update(MANAGED_IDENTIFIER)
            .where(MANAGED_IDENTIFIER.c.account_id.in_(revoked_ids))
            .values(account_id=account.account_id)
        )


async def _insert_account(
    conn: AsyncConnection,
    account: ManagedAccount,
    owners: dict[Identifier, _OwnerRow | None],
) -> None:
    # Idempotent by surrogate id; identifiers with no row insert fresh — an
    # existing row is already ours or was just re-pointed. No conflict-skip:
    # a concurrent claim is a REAL conflict the caller must see.
    await conn.execute(
        pg_insert(MANAGED_ACCOUNT)
        .values(
            account_id=account.account_id,
            person_id=account.person_id,
            platform=account.platform,
            label=account.label,
            state=account.state.value,
        )
        .on_conflict_do_nothing(index_elements=["account_id"])
    )
    for ident in account.identifiers:
        if owners[ident] is not None:
            continue
        await conn.execute(
            sa.insert(MANAGED_IDENTIFIER).values(
                platform=ident.platform,
                kind=ident.kind,
                value=ident.value,
                account_id=account.account_id,
            )
        )


async def _bind_account(
    conn: AsyncConnection, *, person: ManagedPerson, account: ManagedAccount
) -> None:
    """`register`'s account write on the caller's transaction."""
    owners = await _identifier_owners(conn, account.identifiers)
    _check_ownership(owners, person, account)
    await _rebind_revoked(conn, owners, account)
    try:
        await _insert_account(conn, account, owners)
    except IntegrityError as exc:
        if _PG_UNIQUE_VIOLATION in str(getattr(exc, "orig", exc)):
            raise IdentifierConflictError(
                "an identifier is owned by another account"
            ) from exc
        raise


async def bind_identifiers(
    engine: AsyncEngine, *, account_id: str, identifiers: frozenset[Identifier]
) -> Outcome:
    """Enrich ONE bound account with more of its own matchable
    identifiers, atomically — the grouped-observation write.

    One owner, ever: an identifier row naming a DIFFERENT account is an
    `IdentifierConflictError`, bound owner or revoked — a revoked
    binding keeps its ownership reserved; nothing here re-points it.
    An identifier the account already holds is a no-op, and a
    concurrent claim surfaces through the identifier primary key as the
    same conflict."""
    wanted = frozenset(identifiers)
    if not wanted:
        return complete()
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                sa.select(
                    MANAGED_ACCOUNT.c.platform,
                    MANAGED_ACCOUNT.c.state,
                ).where(MANAGED_ACCOUNT.c.account_id == account_id)
            )
        ).fetchone()
        if row is None or row.state != AccountState.BOUND.value:
            return denied("account-not-bound")
        for ident in wanted:
            if ident.platform != row.platform:
                raise ManagedError(
                    "an identifier disagrees with the account's platform"
                )
        owners = await _identifier_owners(conn, wanted)
        if any(
            owner is not None and str(owner.account_id) != account_id
            for owner in owners.values()
        ):
            raise IdentifierConflictError(
                "an identifier is owned by another account"
            )
        added = False
        try:
            for ident, owner in owners.items():
                if owner is not None:
                    continue
                await conn.execute(
                    sa.insert(MANAGED_IDENTIFIER).values(
                        platform=ident.platform,
                        kind=ident.kind,
                        value=ident.value,
                        account_id=account_id,
                    )
                )
                added = True
        except IntegrityError as exc:
            if "23505" in str(getattr(exc, "orig", exc)):
                raise IdentifierConflictError(
                    "an identifier is owned by another account"
                ) from exc
            raise
        if added:
            await conn.execute(
                sa.update(MANAGED_ACCOUNT)
                .where(MANAGED_ACCOUNT.c.account_id == account_id)
                .values(revision=MANAGED_ACCOUNT.c.revision + 1)
            )
    return complete()


async def _canonical_person(conn: AsyncConnection, person_id: str) -> str | None:
    """Fold `person_id` through the alias chain — one hop (aliases point at LIVE rows)."""
    exists = await conn.execute(
        sa.select(MANAGED_PERSON.c.person_id).where(
            MANAGED_PERSON.c.person_id == person_id
        )
    )
    if exists.fetchone() is not None:
        return person_id
    alias = await conn.execute(
        sa.select(MANAGED_ALIAS.c.person_id).where(
            MANAGED_ALIAS.c.former_person_id == person_id
        )
    )
    row = alias.fetchone()
    return None if row is None else row.person_id
