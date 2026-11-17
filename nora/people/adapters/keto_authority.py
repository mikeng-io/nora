"""`AuthorityBackend` over the pinned Keto client — the managed deployment's
scoped-grant engine (`design/managed-identity-access/authorization.md`).

Relation hygiene is enforced HERE, at the write boundary: only the
declared relations may be written — `member`, `admin`, `restricted`,
`grant_<permit>`, `deny_<permit>` — never a computed permit's name, which
strict mode would treat as authoritative and the model forbids writers
from using. Containment links go through `publish_containment` alone.
"""

from __future__ import annotations

from nora.domain.managed import (
    CONTAINMENT_RELATIONS,
    KETO_NAMESPACES,
    ROLE_NAMESPACE,
    ROLE_OBSERVED_RELATION,
    STRUCTURAL_RELATIONS,
    CheckResult,
    ManagedError,
    Outcome,
    OutcomeStatus,
    check_unavailable,
    keto_namespace,
    role_object_id,
)
from nora.domain.managed_ports import AuthorityBackend, RoleRef, ScopedRelation
from nora.domain.management import (
    ACCESS_PERMITS,
    MANAGEMENT_PERMITS,
    Scope,
    ScopeKind,
)
from nora.infra.ory import KetoClient, RelationTuple

_PERMITS = ACCESS_PERMITS | MANAGEMENT_PERMITS


def _check_relation_name(relation: str) -> None:
    if relation in STRUCTURAL_RELATIONS:
        return
    if relation.split("_", 1)[0] in ("grant", "deny") and relation.split(
        "_", 1
    )[1] in _PERMITS:
        return
    raise ManagedError(
        f"{relation!r} is not a writable relation — grants and denials move "
        "through grant_<permit>/deny_<permit>, never a computed permit name"
    )


class KetoAuthorityBackend(AuthorityBackend):
    """One call per port verb, over `KetoClient`. Every transport failure is
    already an `unavailable` value on the client side; this layer adds only
    the relation vocabulary and the scope→namespace encoding."""

    def __init__(self, client: KetoClient) -> None:
        self._client = client

    async def check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        if permit not in _PERMITS:
            raise ManagedError(f"{permit!r} is not a declared permit")
        return await self._client.check(
            scope=scope, relation=permit, person_id=person_id
        )

    async def put_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        _check_relation_name(relation)
        return await self._client.put_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind),
                object=scope.id,
                relation=relation,
                subject_id=person_id,
            )
        )

    async def drop_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        _check_relation_name(relation)
        return await self._client.drop_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind),
                object=scope.id,
                relation=relation,
                subject_id=person_id,
            )
        )

    async def publish_containment(
        self, *, scope: Scope, parent: Scope
    ) -> Outcome:
        """The `room -> unit` / `unit -> workspace` link, written only after
        the caller verified the pairing against real topology — Keto itself
        never checks that the room really sits inside the named unit."""
        relation = CONTAINMENT_RELATIONS.get(scope.kind)
        if relation is None or parent.kind is not _PARENT_KIND[scope.kind]:
            raise ManagedError(
                f"{scope.kind.value} has no containment relation into "
                f"{parent.kind.value}"
            )
        return await self._client.put_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind),
                object=scope.id,
                relation=relation,
                subject_set=(keto_namespace(parent.kind), parent.id),
            )
        )

    async def relations_on(
        self, *, scope: Scope
    ) -> CheckResult | frozenset[tuple[str, str]]:
        rows = await self._client.tuples_on(
            namespace=keto_namespace(scope.kind), object_id=scope.id
        )
        if isinstance(rows, CheckResult):
            return check_unavailable(rows.detail)
        return frozenset(
            (r.relation, r.subject_id)
            for r in rows
            if r.subject_id is not None
        )

    async def scope_relations_on(
        self, *, scope: Scope
    ) -> CheckResult | frozenset[ScopedRelation]:
        rows = await self._client.tuples_on(
            namespace=keto_namespace(scope.kind), object_id=scope.id
        )
        if isinstance(rows, CheckResult):
            return check_unavailable(rows.detail)
        out: set[ScopedRelation] = set()
        for row in rows:
            if row.subject_id is not None:
                out.add(ScopedRelation(row.relation, row.subject_id))
                continue
            if row.subject_set is None:
                return check_unavailable("subject-less tuple")
            if row.subject_set[0] == ROLE_NAMESPACE:
                out.add(ScopedRelation(
                    row.relation,
                    RoleRef(
                        row.subject_set[1],
                        members=len(row.subject_set) > 2
                        and row.subject_set[2] == _ROLE_MEMBER,
                    ),
                ))
                continue
            kind = _SCOPE_KIND.get(row.subject_set[0])
            if kind is None:
                return check_unavailable(
                    f"unsupported subject-set namespace:{row.subject_set[0]}"
                )
            if len(row.subject_set) > 2:
                # A userset on a scope namespace is a shape the supported
                # vocabulary cannot place — a foreign grant, not a parent.
                return check_unavailable(
                    f"unsupported subject-set userset:{row.subject_set[0]}"
                )
            out.add(ScopedRelation(row.relation, Scope(kind, row.subject_set[1])))
        return frozenset(out)

    async def put_role_member(
        self, *, scope: Scope, role: str, person_id: str
    ) -> Outcome:
        obj = role_object_id(scope, role)
        outcome = await self._client.put_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind), object=scope.id,
                relation=ROLE_OBSERVED_RELATION,
                subject_set=(ROLE_NAMESPACE, obj),
            )
        )
        if outcome.status is not OutcomeStatus.COMPLETE:
            return outcome
        return await self._client.put_tuple(
            RelationTuple(
                namespace=ROLE_NAMESPACE, object=obj,
                relation=_ROLE_MEMBER, subject_id=person_id,
            )
        )

    async def drop_role_member(
        self, *, scope: Scope, role: str, person_id: str
    ) -> Outcome:
        return await self._client.drop_tuple(
            RelationTuple(
                namespace=ROLE_NAMESPACE,
                object=role_object_id(scope, role),
                relation=_ROLE_MEMBER, subject_id=person_id,
            )
        )

    async def drop_role_observation(
        self, *, scope: Scope, role: str
    ) -> Outcome:
        return await self._client.drop_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind), object=scope.id,
                relation=ROLE_OBSERVED_RELATION,
                subject_set=(ROLE_NAMESPACE, role_object_id(scope, role)),
            )
        )

    async def role_members(
        self, *, scope: Scope, role: str
    ) -> CheckResult | frozenset[str]:
        rows = await self._client.tuples_on(
            namespace=ROLE_NAMESPACE,
            object_id=role_object_id(scope, role),
        )
        if isinstance(rows, CheckResult):
            return check_unavailable(rows.detail)
        return frozenset(
            row.subject_id for row in rows
            if row.relation == _ROLE_MEMBER and row.subject_id is not None
        )

    async def put_role_grant(
        self, *, scope: Scope, relation: str, role: str
    ) -> Outcome:
        _check_role_grant(relation)
        return await self._client.put_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind), object=scope.id,
                relation=relation,
                subject_set=(
                    ROLE_NAMESPACE, role_object_id(scope, role), _ROLE_MEMBER,
                ),
            )
        )

    async def drop_role_grant(
        self, *, scope: Scope, relation: str, role: str
    ) -> Outcome:
        _check_role_grant(relation)
        return await self._client.drop_tuple(
            RelationTuple(
                namespace=keto_namespace(scope.kind), object=scope.id,
                relation=relation,
                subject_set=(
                    ROLE_NAMESPACE, role_object_id(scope, role), _ROLE_MEMBER,
                ),
            )
        )

    async def aclose(self) -> None:
        """The client shares the caller's session — nothing to close."""


_SCOPE_KIND = {namespace: kind for kind, namespace in KETO_NAMESPACES.items()}

_ROLE_MEMBER = "member"


def _check_role_grant(relation: str) -> None:
    if (not relation.startswith("grant_")
            or relation.removeprefix("grant_") not in MANAGEMENT_PERMITS):
        raise ManagedError(
            f"{relation!r} is not a role grant — delegated roles carry "
            "grant_<management permit> alone, never membership or denials"
        )


_PARENT_KIND = {
    ScopeKind.UNIT: ScopeKind.WORKSPACE,
    ScopeKind.ROOM: ScopeKind.UNIT,
}
