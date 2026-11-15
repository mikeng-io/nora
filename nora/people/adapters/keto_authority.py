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
    STRUCTURAL_RELATIONS,
    CheckResult,
    ManagedError,
    Outcome,
    check_unavailable,
    keto_namespace,
)
from nora.domain.management import (
    ACCESS_PERMITS,
    MANAGEMENT_PERMITS,
    Scope,
    ScopeKind,
)
from nora.domain.managed_ports import AuthorityBackend
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

    async def aclose(self) -> None:
        """The client shares the caller's session — nothing to close."""


_PARENT_KIND = {
    ScopeKind.UNIT: ScopeKind.WORKSPACE,
    ScopeKind.ROOM: ScopeKind.UNIT,
}
