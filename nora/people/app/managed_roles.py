"""Platform-role authority effects: the roster-fed holder index, the
`Role#member` grant writes a role delegation resolves to, and their
read-back terms (`design/managed-identity-access/authorization.md`)."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from nora.domain.managed import (
    ROLE_OBSERVED_RELATION,
    CheckResult,
    Outcome,
    relation_for,
    role_label,
    role_object_id,
    unavailable,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    RoleRef,
)
from nora.domain.management import (
    RoleDelegation,
    RosterSync,
    Scope,
)


async def role_retractions(
    backend: AuthorityBackend,
    bindings: BindingStore,
    *,
    scope: Scope,
    payload: RosterSync,
) -> Outcome | tuple[dict[str, set[str]], list[tuple[str, str]], list[str]]:
    """Observed holders resolved through bound accounts — a complete roster
    also diffs the scope's role indexes: holders it no longer names drop,
    an unreported role's index drops whole."""
    role_joined: dict[str, set[str]] = {}
    for observed in payload.roles:
        account = await bindings.account_for(
            observed.member.platform, observed.member.kind,
            observed.member.value,
        )
        if account is not None:
            role_joined.setdefault(observed.role, set()).add(account.person_id)
    role_dropped: list[tuple[str, str]] = []
    roles_gone: list[str] = []
    if payload.complete:
        rows = await backend.scope_relations_on(scope=scope)
        if isinstance(rows, CheckResult):
            return unavailable(rows.detail)
        for row in rows:
            if (row.relation != ROLE_OBSERVED_RELATION
                    or not isinstance(row.subject, RoleRef)
                    or row.subject.members):
                continue
            label = role_label(scope, row.subject.role)
            if label is None:
                return unavailable("foreign-role-index")
            holders = await backend.role_members(scope=scope, role=label)
            if isinstance(holders, CheckResult):
                return unavailable(holders.detail)
            desired = role_joined.get(label, set())
            role_dropped += [(label, p) for p in sorted(holders - desired)]
            if not desired:
                roles_gone.append(label)
    return role_joined, role_dropped, roles_gone


def role_grant_calls(
    backend: AuthorityBackend,
    *,
    scope: Scope,
    kind: str,
    payload: RoleDelegation,
) -> tuple[Coroutine[Any, Any, Outcome], ...]:
    """One `grant_<permit>` write per delegated permit, put or dropped."""
    write = (
        backend.put_role_grant if kind == "delegate"
        else backend.drop_role_grant
    )
    return tuple(
        write(scope=scope, relation=relation_for("grant", permit),
              role=payload.role)
        for permit in sorted(payload.permits)
    )


def role_grant_terms(
    scope: Scope, kind: str, payload: RoleDelegation
) -> tuple[set[tuple[str, RoleRef]], set[tuple[str, RoleRef]]]:
    """The `grant_<permit>@Role:<scope>/<role>#member` rows a replay must
    read back after delegate/revoke_delegation."""
    holder = RoleRef(role_object_id(scope, payload.role), members=True)
    pairs = {(relation_for("grant", p), holder) for p in payload.permits}
    return (pairs if kind == "delegate" else set(),
            set() if kind == "delegate" else pairs)
