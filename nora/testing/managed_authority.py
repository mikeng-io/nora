"""Fake `AuthorityBackend`; its evaluator follows deploy/ory/keto/namespaces.ts."""

from __future__ import annotations

from typing import Any

from nora.domain.managed import (
    CONTAINMENT_RELATIONS,
    KETO_NAMESPACES,
    ROLE_NAMESPACE,
    ROLE_OBSERVED_RELATION,
    CheckResult,
    Outcome,
    allowed,
    check_denied,
    check_unavailable,
    complete,
    denied,
    keto_namespace,
    role_object_id,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    RoleRef,
    ScopedRelation,
)
from nora.domain.management import (
    Scope,
    ScopeKind,
)
from nora.testing.managed_registry import (
    unavailable_outcome,
)


class FakeAuthorityBackend(AuthorityBackend):
    """In-memory relation tuples with the committed OPL's evaluator —
    containment-aware, deny-wins, member confers only on Room. `down=True`
    answers every call `unavailable`, the shape a real Keto outage takes."""

    def __init__(self) -> None:
        # (namespace, object, relation, subject) — subject is a person_id,
        # or ("subjectset", ns, obj) for containment links.
        self.tuples: set[tuple[str, str, str, Any]] = set()
        self.down = False

    def _ancestors(self, namespace: str, obj: str) -> list[tuple[str, str]]:
        """The containment chain above (ns, obj) — Room→unit→workspace tuples."""
        chain: list[tuple[str, str]] = []
        ns_kind = {
            "Workspace": ScopeKind.WORKSPACE,
            "Unit": ScopeKind.UNIT,
            "Room": ScopeKind.ROOM,
        }[namespace]
        relation = CONTAINMENT_RELATIONS.get(ns_kind)
        cursor = (namespace, obj)
        while relation is not None:
            parent = next(
                (s for (ns_, ob, rel, s) in self.tuples
                 if (ns_, ob, rel) == (*cursor, relation)
                 and isinstance(s, tuple)),
                None,
            )
            if parent is None:
                break
            chain.append((parent[1], parent[2]))
            parent_kind = {"Workspace": ScopeKind.WORKSPACE,
                           "Unit": ScopeKind.UNIT,
                           "Room": ScopeKind.ROOM}[parent[1]]
            relation = CONTAINMENT_RELATIONS.get(parent_kind)
            cursor = (parent[1], parent[2])
        return chain

    def _eval(self, namespace: str, obj: str, permit: str, person: str) -> bool:
        chain = [(namespace, obj), *self._ancestors(namespace, obj)]
        for ns, ob in chain:
            if (ns, ob, "restricted", person) in self.tuples or (
                ns, ob, f"deny_{permit}", person
            ) in self.tuples:
                return False
        for ns, ob in chain:
            if (ns, ob, f"grant_{permit}", person) in self.tuples or (
                ns, ob, "admin", person
            ) in self.tuples:
                return True
        # A delegated role's `grant_<management>@Role:S/R#member` resolves
        # through observed holders (the OPL userset). Grants only — member,
        # deny and `restricted` stay person subjects.
        if permit.startswith("manage_") or permit == "delegate":
            for ns, ob in chain:
                for ns_, ob_, rel_, subj in self.tuples:
                    if (
                        (ns_, ob_, rel_) == (ns, ob, f"grant_{permit}")
                        and isinstance(subj, tuple)
                        and len(subj) == 4
                        and subj[:2] == ("subjectset", ROLE_NAMESPACE)
                        and subj[3] == "member"
                        and (ROLE_NAMESPACE, subj[2], "member", person)
                        in self.tuples
                    ):
                        return True
        # member confers speak+view on a Room alone — never on its parents.
        return (
            namespace == "Room"
            and permit in ("speak", "view")
            and (namespace, obj, "member", person) in self.tuples
        )

    async def check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        if self.down:
            return check_unavailable("down")
        return (
            allowed()
            if self._eval(keto_namespace(scope.kind), scope.id, permit, person_id)
            else check_denied()
        )

    async def put_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        self.tuples.add(
            (keto_namespace(scope.kind), scope.id, relation, person_id)
        )
        return complete()

    async def drop_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        self.tuples.discard(
            (keto_namespace(scope.kind), scope.id, relation, person_id)
        )
        return complete()

    async def publish_containment(self, *, scope: Scope, parent: Scope) -> Outcome:
        if self.down:
            return unavailable_outcome()
        relation = CONTAINMENT_RELATIONS.get(scope.kind)
        if relation is None:
            return denied("workspace has no parent")
        self.tuples.add(
            (
                keto_namespace(scope.kind),
                scope.id,
                relation,
                ("subjectset", keto_namespace(parent.kind), parent.id),
            )
        )
        return complete()

    async def relations_on(
        self, *, scope: Scope
    ) -> CheckResult | frozenset[tuple[str, str]]:
        if self.down:
            return check_unavailable("down")
        ns = keto_namespace(scope.kind)
        return frozenset(
            (rel, s) for (ns_, ob, rel, s) in self.tuples
            if ns_ == ns and ob == scope.id and isinstance(s, str)
        )

    async def scope_relations_on(
        self, *, scope: Scope
    ) -> CheckResult | frozenset[ScopedRelation]:
        if self.down:
            return check_unavailable("down")
        ns = keto_namespace(scope.kind)
        kinds = {name: kind for kind, name in KETO_NAMESPACES.items()}
        out: set[ScopedRelation] = set()
        for ns_, ob, rel, subject in self.tuples:
            if (ns_, ob) != (ns, scope.id):
                continue
            if isinstance(subject, str):
                out.add(ScopedRelation(rel, subject))
                continue
            if (
                isinstance(subject, tuple) and len(subject) in (3, 4)
                and subject[0] == "subjectset"
            ):
                if subject[1] == ROLE_NAMESPACE:
                    out.add(ScopedRelation(
                        rel,
                        RoleRef(
                            subject[2],
                            members=len(subject) == 4
                            and subject[3] == "member",
                        ),
                    ))
                    continue
                if subject[1] in kinds and len(subject) == 3:
                    out.add(ScopedRelation(
                        rel, Scope(kinds[subject[1]], subject[2])))
                    continue
            return check_unavailable("unsupported subject shape")
        return frozenset(out)

    async def put_role_member(
        self, *, scope: Scope, role: str, person_id: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        obj = role_object_id(scope, role)
        self.tuples.add(
            (keto_namespace(scope.kind), scope.id, ROLE_OBSERVED_RELATION,
             ("subjectset", ROLE_NAMESPACE, obj))
        )
        self.tuples.add((ROLE_NAMESPACE, obj, "member", person_id))
        return complete()

    async def drop_role_member(
        self, *, scope: Scope, role: str, person_id: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        self.tuples.discard(
            (ROLE_NAMESPACE, role_object_id(scope, role), "member", person_id)
        )
        return complete()

    async def drop_role_observation(
        self, *, scope: Scope, role: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        self.tuples.discard(
            (keto_namespace(scope.kind), scope.id, ROLE_OBSERVED_RELATION,
             ("subjectset", ROLE_NAMESPACE, role_object_id(scope, role)))
        )
        return complete()

    async def role_members(
        self, *, scope: Scope, role: str
    ) -> CheckResult | frozenset[str]:
        if self.down:
            return check_unavailable("down")
        obj = role_object_id(scope, role)
        return frozenset(
            s for (ns_, ob, rel, s) in self.tuples
            if (ns_, ob, rel) == (ROLE_NAMESPACE, obj, "member")
            and isinstance(s, str)
        )

    async def put_role_grant(
        self, *, scope: Scope, relation: str, role: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        self.tuples.add(
            (keto_namespace(scope.kind), scope.id, relation,
             ("subjectset", ROLE_NAMESPACE, role_object_id(scope, role),
              "member"))
        )
        return complete()

    async def drop_role_grant(
        self, *, scope: Scope, relation: str, role: str
    ) -> Outcome:
        if self.down:
            return unavailable_outcome()
        self.tuples.discard(
            (keto_namespace(scope.kind), scope.id, relation,
             ("subjectset", ROLE_NAMESPACE, role_object_id(scope, role),
              "member"))
        )
        return complete()

    async def aclose(self) -> None:
        ...
