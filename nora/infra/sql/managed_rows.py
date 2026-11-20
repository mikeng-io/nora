"""Row → domain projections for the managed-identity tables, shared by
`managed_identity`'s reads. One function per table; no state."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from sqlalchemy.engine import Row

from nora.domain.managed import (
    AuthorityBlock,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationJournalState,
    OperationRecord,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind


def _person_from(r: Row[Any]) -> ManagedPerson:
    return ManagedPerson(
        person_id=r.person_id,
        lifecycle=PersonLifecycle(r.lifecycle),
        kratos_identity_id=r.kratos_identity_id,
        display_name=r.display_name,
        revision=r.revision,
    )


def _scope_from(kind: str, scope_id: str) -> Scope:
    return Scope(kind=ScopeKind(kind), id=scope_id)


def _membership_from(
    r: Row[Any], sources: frozenset[str] = frozenset()
) -> ManagedMembership:
    return ManagedMembership(
        person_id=r.person_id,
        scope=_scope_from(r.scope_kind, r.scope_id),
        state=MembershipState(r.state),
        source=r.source,
        revision=r.revision,
        sources=sources,
    )


def _operation_from(r: Row[Any]) -> OperationRecord:
    return OperationRecord(
        operation_id=r.operation_id,
        kind=r.kind,
        state=OperationJournalState(r.state),
        steps=MappingProxyType(dict(r.steps or {})),
        outcome=r.outcome,
        detail=r.detail,
        snapshot=MappingProxyType(dict(r.snapshot or {})),
    )


def _block_from(r: Row[Any]) -> AuthorityBlock:
    return AuthorityBlock(
        person_id=r.person_id,
        scope=_scope_from(r.scope_kind, r.scope_id),
        reason=r.reason,
        operation_id=r.operation_id,
    )
