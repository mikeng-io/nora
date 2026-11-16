"""The SQL side of the managed-to-file export —
`nora/people/app/managed_export.py` consumes these rows through
`nora/people/adapters/managed_export.py`.

Like `managed_handoff.admission_facts`, this reads what the port reads
hide — but for the opposite reason. Admission needs an empty-or-owned
proof; export needs the COMPLETE inventory: revoked accounts (they must not
reappear as bound), suspended people (they must not regain access),
`left` memberships, holds, unfinished journals, authority blocks, link
state, and the private `file_admission` journal's archived source texts,
which carry authored metadata no managed table keeps (account `username`s,
Life360 `circle_id`s, first/last names, workspace bindings).

Reads run on plain connections — the caller's `bindings.mutation()` gate
already serialises against every managed writer (the same Postgres
advisory lock the writers take), so each row set is the state the gate
protects.
"""

from __future__ import annotations

from typing import Any, TypedDict

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema_managed import (
    MANAGED_ACCOUNT,
    MANAGED_ALIAS,
    MANAGED_AUTHORITY_BLOCK,
    MANAGED_IDENTIFIER,
    MANAGED_IDENTITY_HOLD,
    MANAGED_LINK_REQUEST,
    MANAGED_MEMBERSHIP,
    MANAGED_MEMBERSHIP_SUPPORT,
    MANAGED_OPERATION,
    MANAGED_PERSON,
)


class ExportRows(TypedDict):
    """The complete managed projection — one field per export input."""
    persons: tuple[tuple[str, str, str | None, str], ...]
    accounts: tuple[tuple[str, str, str, str, str | None], ...]
    identifiers: tuple[tuple[str, str, str, str], ...]
    aliases: tuple[tuple[str, str], ...]
    memberships: tuple[tuple[str, str, str, str], ...]
    supports: tuple[tuple[str, str, str, str], ...]
    operations: tuple[tuple[str, str, str, str], ...]
    holds: tuple[tuple[str, str], ...]
    blocks: tuple[tuple[str, str, str, str], ...]
    link_states: tuple[tuple[str, str], ...]
    # The newest COMPLETE file_admission journal's archived source texts —
    # (operation_id, identity_toml, policy_toml) — or None when the
    # installation never admitted a file mapping.
    archive: tuple[str, str, str] | None


async def export_rows(engine: AsyncEngine) -> ExportRows:
    async with engine.connect() as conn:
        persons = (await conn.execute(sa.select(
            MANAGED_PERSON.c.person_id,
            MANAGED_PERSON.c.lifecycle,
            MANAGED_PERSON.c.kratos_identity_id,
            MANAGED_PERSON.c.display_name,
        ).order_by(MANAGED_PERSON.c.person_id))).all()
        accounts = (await conn.execute(sa.select(
            MANAGED_ACCOUNT.c.account_id,
            MANAGED_ACCOUNT.c.person_id,
            MANAGED_ACCOUNT.c.platform,
            MANAGED_ACCOUNT.c.state,
            MANAGED_ACCOUNT.c.label,
        ).order_by(MANAGED_ACCOUNT.c.account_id))).all()
        identifiers = (await conn.execute(sa.select(
            MANAGED_IDENTIFIER.c.platform,
            MANAGED_IDENTIFIER.c.kind,
            MANAGED_IDENTIFIER.c.value,
            MANAGED_IDENTIFIER.c.account_id,
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
            MANAGED_OPERATION.c.created_at,
        ).order_by(MANAGED_OPERATION.c.created_at))).all()
        holds = (await conn.execute(sa.select(
            MANAGED_IDENTITY_HOLD.c.person_id,
            MANAGED_IDENTITY_HOLD.c.operation_id,
        ))).all()
        blocks = (await conn.execute(sa.select(
            MANAGED_AUTHORITY_BLOCK.c.person_id,
            MANAGED_AUTHORITY_BLOCK.c.scope_kind,
            MANAGED_AUTHORITY_BLOCK.c.scope_id,
            MANAGED_AUTHORITY_BLOCK.c.reason,
        ))).all()
        links = (await conn.execute(sa.select(
            MANAGED_LINK_REQUEST.c.request_id,
            MANAGED_LINK_REQUEST.c.snapshot,
        ))).all()
        # The archived admission sources: newest COMPLETE import wins — an
        # installation only ever holds one (a second import is a different
        # target), but ordering by created_at is the honest rule anyway.
        archive_rows = (await conn.execute(sa.select(
            MANAGED_OPERATION.c.operation_id,
            MANAGED_OPERATION.c.snapshot,
        ).where(
            MANAGED_OPERATION.c.kind == "file_admission",
            MANAGED_OPERATION.c.state == "complete",
        ).order_by(MANAGED_OPERATION.c.created_at.desc()))).all()
        archive: tuple[str, str, str] | None = None
        for row in archive_rows:
            snapshot: Any = row.snapshot
            sources = snapshot.get("sources") if isinstance(snapshot, dict) else None
            if isinstance(sources, dict) and isinstance(
                sources.get("identity_toml"), str
            ) and isinstance(sources.get("policy_toml"), str):
                archive = (
                    row.operation_id, sources["identity_toml"],
                    sources["policy_toml"],
                )
                break
        return {
            "persons": tuple(
                (r.person_id, r.lifecycle, r.kratos_identity_id, r.display_name)
                for r in persons
            ),
            "accounts": tuple(
                (r.account_id, r.person_id, r.platform, r.state, r.label)
                for r in accounts
            ),
            "identifiers": tuple(
                (r.platform, r.kind, r.value, r.account_id) for r in identifiers
            ),
            "aliases": tuple((r.former_person_id, r.person_id) for r in aliases),
            "memberships": tuple(
                (r.person_id, r.scope_kind, r.scope_id, r.state) for r in memberships
            ),
            "supports": tuple(
                (r.person_id, r.scope_kind, r.scope_id, r.source) for r in supports
            ),
            "operations": tuple(
                (r.operation_id, r.kind, r.state, str(r.created_at))
                for r in operations
            ),
            "holds": tuple((r.person_id, r.operation_id) for r in holds),
            "blocks": tuple(
                (r.person_id, r.scope_kind, r.scope_id, r.reason) for r in blocks
            ),
            "link_states": tuple(
                (
                    r.request_id,
                    str(r.snapshot.get("state", ""))
                    if isinstance(r.snapshot, dict) else "",
                )
                for r in links
            ),
            "archive": archive,
        }
