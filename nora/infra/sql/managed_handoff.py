"""The SQL side of the installation target —
`nora/people/app/managed_admission.py` consumes these reads through
`nora/people/adapters/managed_handoff.py`.

Two things the port reads cannot give the initial claim:

- the EMPTINESS-or-OWNED proof. `persons()` hides suspended people,
  `record_for`/`accounts_for` hide revoked accounts, and nothing port-level
  reads holds, authority blocks, link rows or foreign journals — every one
  of them is non-empty evidence that refuses an initial import. The probe
  reads all of it directly.
- the person-only insert. `BindingStore.register` writes person+account
  together; a person the policy names but `identity.toml` gives no account
  yet is still a grant subject, so the admission needs the same idempotent
  `_insert_person` the registration path uses — reused here, not
  re-implemented.
"""

from __future__ import annotations

from typing import TypedDict

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.managed import ManagedPerson, Outcome, complete
from nora.infra.sql.managed_registration import _insert_person
from nora.infra.sql.schema_managed import (
    MANAGED_ACCOUNT,
    MANAGED_ALIAS,
    MANAGED_AUTHORITY_BLOCK,
    MANAGED_IDENTIFIER,
    MANAGED_IDENTITY_HOLD,
    MANAGED_LINK_DECISION,
    MANAGED_LINK_DELIVERY,
    MANAGED_LINK_REQUEST,
    MANAGED_MEMBERSHIP,
    MANAGED_MEMBERSHIP_SUPPORT,
    MANAGED_OPERATION,
    MANAGED_PERSON,
)


class AdmissionRows(TypedDict):
    """The claim-proof projection — one field per managed table."""
    persons: frozenset[tuple[str, str, bool]]
    accounts: frozenset[tuple[str, str, str]]
    identifiers: frozenset[tuple[str, str, str]]
    aliases: frozenset[tuple[str, str]]
    memberships: frozenset[tuple[str, str, str, str]]
    supports: frozenset[tuple[str, str, str, str]]
    operations: frozenset[tuple[str, str, str]]
    holds: frozenset[tuple[str, str]]
    blocks: int
    link_rows: int


async def admission_facts(engine: AsyncEngine) -> AdmissionRows:
    """Every managed row, projected to the claim-proof tuples.

    Reads run on plain connections — the caller's `bindings.mutation()` gate
    already serialises this against every managed writer, so a fresh read
    here sees exactly the state the gate protects."""
    async with engine.connect() as conn:
        persons = (await conn.execute(sa.select(
            MANAGED_PERSON.c.person_id,
            MANAGED_PERSON.c.lifecycle,
            MANAGED_PERSON.c.kratos_identity_id,
        ))).all()
        accounts = (await conn.execute(sa.select(
            MANAGED_ACCOUNT.c.account_id,
            MANAGED_ACCOUNT.c.person_id,
            MANAGED_ACCOUNT.c.state,
        ))).all()
        identifiers = (await conn.execute(sa.select(
            MANAGED_IDENTIFIER.c.platform,
            MANAGED_IDENTIFIER.c.kind,
            MANAGED_IDENTIFIER.c.value,
        ))).all()
        aliases = (await conn.execute(sa.select(
            MANAGED_ALIAS.c.former_person_id,
            MANAGED_ALIAS.c.person_id,
        ))).all()
        memberships = (await conn.execute(sa.select(
            MANAGED_MEMBERSHIP.c.person_id,
            MANAGED_MEMBERSHIP.c.scope_kind,
            MANAGED_MEMBERSHIP.c.scope_id,
            MANAGED_MEMBERSHIP.c.state,
        ))).all()
        supports = (await conn.execute(sa.select(
            MANAGED_MEMBERSHIP_SUPPORT.c.person_id,
            MANAGED_MEMBERSHIP_SUPPORT.c.scope_kind,
            MANAGED_MEMBERSHIP_SUPPORT.c.scope_id,
            MANAGED_MEMBERSHIP_SUPPORT.c.source,
        ))).all()
        operations = (await conn.execute(sa.select(
            MANAGED_OPERATION.c.operation_id,
            MANAGED_OPERATION.c.kind,
            MANAGED_OPERATION.c.state,
        ))).all()
        holds = (await conn.execute(sa.select(
            MANAGED_IDENTITY_HOLD.c.person_id,
            MANAGED_IDENTITY_HOLD.c.operation_id,
        ))).all()
        link_rows = 0
        for table in (MANAGED_LINK_REQUEST, MANAGED_LINK_DECISION, MANAGED_LINK_DELIVERY):
            link_rows += int(await conn.scalar(
                sa.select(sa.func.count()).select_from(table)) or 0)
        return {
            "persons": frozenset(
                (row.person_id, row.lifecycle, row.kratos_identity_id is not None)
                for row in persons
            ),
            "accounts": frozenset(
                (row.account_id, row.person_id, row.state) for row in accounts
            ),
            "identifiers": frozenset(
                (row.platform, row.kind, row.value) for row in identifiers
            ),
            "aliases": frozenset(
                (row.former_person_id, row.person_id) for row in aliases
            ),
            "memberships": frozenset(
                (row.person_id, row.scope_kind, row.scope_id, row.state)
                for row in memberships
            ),
            "supports": frozenset(
                (row.person_id, row.scope_kind, row.scope_id, row.source)
                for row in supports
            ),
            "operations": frozenset(
                (row.operation_id, row.kind, row.state) for row in operations
            ),
            "holds": frozenset(
                (row.person_id, row.operation_id) for row in holds
            ),
            "blocks": int(await conn.scalar(
                sa.select(sa.func.count()).select_from(MANAGED_AUTHORITY_BLOCK)
            ) or 0),
            "link_rows": link_rows,
        }


async def insert_person(engine: AsyncEngine, person: ManagedPerson) -> Outcome:
    """The account-less person row — the registration helper itself, so the
    insert stays idempotent and lifecycle-preserving like every other write."""
    async with engine.begin() as conn:
        await _insert_person(conn, person)
    return complete()
