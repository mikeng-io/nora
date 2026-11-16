"""`ExportProbe` over the managed PostgreSQL tables —
`nora/infra/sql/managed_export.py` holds the queries; this adapter is the
`app/managed_export.py`-facing seam, the same way `SqlAdmissionProbe`
adapts `managed_handoff` for the initial import."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.managed import Outcome, complete, unavailable
from nora.domain.management import Scope, ScopeKind
from nora.infra.ory import KratosIdentity
from nora.infra.sql import managed_export
from nora.people.app.managed_export_prepare import (
    ExportInventory,
    IdentitySnapshot,
    InventoryAccount,
    InventoryBlock,
    InventoryIdentifier,
    InventoryMembership,
    InventoryOperation,
    InventoryPerson,
)


class _KratosReader(Protocol):
    """The read-only correlated-identity proof the export needs — satisfied
    by the existing `KratosClient` (and by test readers over the fake
    registry's records). `get_identity` never creates or mutates."""

    async def get_identity(
        self, identity_id: str
    ) -> tuple[Outcome, KratosIdentity | None]: ...


def _scope(kind: str, scope_id: str) -> Scope:
    return Scope(ScopeKind(kind), scope_id)


class SqlExportProbe:
    """Probe the concrete installation — the complete inventory, including
    the hidden rows the public port reads omit, and the correlated Kratos
    record behind each `kratos_identity_id`."""

    def __init__(
        self, engine: AsyncEngine, kratos: _KratosReader | None = None
    ) -> None:
        self._engine = engine
        self._kratos = kratos

    async def identity_snapshot(
        self, identity_id: str
    ) -> tuple[Outcome, IdentitySnapshot | None]:
        """The Kratos record at `identity_id` — read-only correlation proof."""
        if self._kratos is None:
            return unavailable("no-kratos-reader"), None
        outcome, identity = await self._kratos.get_identity(identity_id)
        if identity is None:
            return outcome, None
        return complete(), IdentitySnapshot(
            identity_id=identity.identity_id,
            external_id=identity.external_id,
            state=identity.state,
            traits=identity.traits,
        )

    async def inventory(self) -> ExportInventory:
        rows = await managed_export.export_rows(self._engine)
        supports: dict[tuple[str, str, str], set[str]] = {}
        for person_id, kind, scope_id, source in rows["supports"]:
            supports.setdefault((person_id, kind, scope_id), set()).add(source)
        return ExportInventory(
            persons=tuple(
                InventoryPerson(
                    person_id=pid, lifecycle=lifecycle,
                    kratos_identity_id=kratos_id, display_name=name,
                )
                for pid, lifecycle, kratos_id, name in rows["persons"]
            ),
            accounts=tuple(
                InventoryAccount(
                    account_id=account_id, person_id=pid, platform=platform,
                    state=state, label=label,
                )
                for account_id, pid, platform, state, label in rows["accounts"]
            ),
            identifiers=tuple(
                InventoryIdentifier(
                    platform=platform, kind=kind, value=value,
                    account_id=account_id,
                )
                for platform, kind, value, account_id in rows["identifiers"]
            ),
            aliases=tuple(rows["aliases"]),
            memberships=tuple(
                InventoryMembership(
                    person_id=pid, scope=_scope(kind, scope_id), state=state,
                    sources=frozenset(supports.get((pid, kind, scope_id), ())),
                )
                for pid, kind, scope_id, state in rows["memberships"]
            ),
            operations=tuple(
                InventoryOperation(
                    operation_id=op_id, kind=kind, state=state,
                    created_at=created_at,
                )
                for op_id, kind, state, created_at in rows["operations"]
            ),
            holds=tuple(rows["holds"]),
            blocks=tuple(
                InventoryBlock(
                    person_id=pid, scope=_scope(kind, scope_id), reason=reason,
                )
                for pid, kind, scope_id, reason in rows["blocks"]
            ),
            link_states=tuple(rows["link_states"]),
            archive=rows["archive"],
        )
