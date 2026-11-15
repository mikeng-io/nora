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
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    IdentifierConflictError,
    ManagedAccount,
    ManagedPerson,
)
from nora.infra.sql.schema_managed import (
    MANAGED_ACCOUNT,
    MANAGED_IDENTIFIER,
    MANAGED_PERSON,
)

_OwnerRow = Row[Any, Any, Any]


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
        if owner.person_id != person.person_id:
            raise IdentifierConflictError(
                f"identifier {ident.platform}:{ident.kind} is owned "
                "by another person"
            )
        if (
            owner.state == AccountState.BOUND.value
            and str(owner.account_id) != account.account_id
        ):
            raise IdentifierConflictError(
                f"identifier {ident.platform}:{ident.kind} is held "
                "by a different bound account"
            )


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
