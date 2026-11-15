"""In-memory second implementations of the managed ports — the fake half of
the binding-store contract suite
(`tests/contract/test_binding_store_contract.py`) and the stand-in the unit
services run against. The Keto/Kratos halves are covered live:
`scripts.check_ory_policy` probes the real OPL engine and the deployed
adapters answer to the real admin APIs.

These are REAL implementations, not answer stubs: `FakeAuthorityBackend`
evaluates the committed OPL semantics (`deploy/ory/keto/namespaces.ts`) —
deny wins at the scope and down every ancestor, `restricted` denies
everything on its scope, `member` confers speak+view on a Room only,
management permits inherit downward while parent membership confers
nothing. `FakeBindingStore` enforces the same one-owner-per-identifier and
journal-conflict rules the SQL store does. `down` flips each fake into the
unavailable path a real outage produces, so fail-closed tests don't need a
socket.
"""

from __future__ import annotations

from typing import Any

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.managed import (
    CONTAINMENT_RELATIONS,
    AccountState,
    AuthorityBlock,
    CheckResult,
    IdentifierConflictError,
    ManagedAccount,
    ManagedError,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationConflictError,
    OperationJournalState,
    OperationRecord,
    Outcome,
    PersonLifecycle,
    allowed,
    check_denied,
    check_unavailable,
    complete,
    denied,
    keto_namespace,
    unavailable,
    unknown_scope,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.ports import (
    AuthorityBackend,
    BindingStore,
    IdentityRegistry,
    TopologySource,
)


class FakeBindingStore(BindingStore):
    """In-memory bindings. `down=True` makes every call raise — the
    service maps a genuine store failure to `unavailable`."""

    def __init__(self) -> None:
        self.persons: dict[str, ManagedPerson] = {}
        self.accounts: dict[str, ManagedAccount] = {}
        self.owners: dict[Identifier, str] = {}  # identifier -> account_id
        self.memberships: dict[tuple[str, Scope], ManagedMembership] = {}
        self.aliases: dict[str, str] = {}
        self.operations: dict[str, OperationRecord] = {}
        self.blocks: dict[tuple[str, Scope], AuthorityBlock] = {}
        self.down = False

    def _up(self) -> None:
        if self.down:
            raise RuntimeError("fake store is down")

    def _canonical(self, person_id: str) -> str:
        seen: set[str] = set()
        pid = person_id
        while pid in self.aliases and pid not in seen:
            seen.add(pid)
            pid = self.aliases[pid]
        return pid

    async def resolve(self, platform: str, value: str) -> BoundAccount | None:
        self._up()
        for ident, account_id in self.owners.items():
            if ident.platform != platform or ident.value != value:
                continue
            account = self.accounts.get(account_id)
            if account is not None and account.state is AccountState.BOUND:
                return account.bound_account()
        return None

    async def account_for(
        self, platform: str, kind: str, value: str
    ) -> BoundAccount | None:
        self._up()
        ident = Identifier(platform=platform, kind=kind, value=value)
        account = self.accounts.get(self.owners.get(ident, ""))
        if account is None or account.state is not AccountState.BOUND:
            return None
        return account.bound_account()

    async def accounts_for(self, person_id: str) -> tuple[BoundAccount, ...]:
        self._up()
        pid = self._canonical(person_id)
        return tuple(
            a.bound_account()
            for a in self.accounts.values()
            if a.person_id == pid and a.state is AccountState.BOUND
        )

    async def canonical_person(self, person_id: str) -> str | None:
        self._up()
        pid = self._canonical(person_id)
        return pid if pid in self.persons else None

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        self._up()
        person = self.persons.get(self._canonical(person_id))
        return person.lifecycle if person else None

    async def person(self, person_id: str) -> ManagedPerson | None:
        self._up()
        return self.persons.get(self._canonical(person_id))

    async def account_record(self, account_id: str) -> ManagedAccount | None:
        self._up()
        return self.accounts.get(account_id)

    async def record_for(self, *, account: Identifier) -> ManagedAccount | None:
        self._up()
        held = self.accounts.get(self.owners.get(account, ""))
        if held is None or held.state is not AccountState.BOUND:
            return None
        return held

    async def register(
        self, *, person: ManagedPerson, account: ManagedAccount,
        membership: ManagedMembership | None = None,
    ) -> Outcome:
        self._up()
        if account.person_id != person.person_id:
            raise ManagedError(
                "account.person_id disagrees with the registering person"
            )
        existing = self.persons.get(person.person_id)
        if existing is not None:
            person = existing  # idempotent: keep lifecycle/correlation
        else:
            self.persons[person.person_id] = person
        current = self.accounts.get(account.account_id)
        if current is not None and current.person_id != person.person_id:
            raise IdentifierConflictError("account id claimed by another person")
        for ident in account.identifiers:
            owner = self.owners.get(ident)
            if owner is None:
                continue
            held = self.accounts.get(owner)
            if held is None:
                continue
            if held.person_id != person.person_id:
                raise IdentifierConflictError(
                    f"{ident} is owned by a different person"
                )
            if owner != account.account_id and held.state is AccountState.BOUND:
                raise IdentifierConflictError(
                    f"{ident} is owned by a different bound account"
                )
            # revoked owner on the same person: re-point to this account
            if owner != account.account_id:
                self.owners[ident] = account.account_id
            elif held.state is AccountState.REVOKED:
                held = ManagedAccount(
                    account_id=held.account_id, person_id=held.person_id,
                    platform=held.platform, identifiers=held.identifiers,
                    state=AccountState.BOUND, label=held.label,
                    revision=held.revision + 1,
                )
                self.accounts[owner] = held
        for ident in account.identifiers:
            self.owners.setdefault(ident, account.account_id)
        if current is None:
            self.accounts[account.account_id] = account
        if membership is not None:
            if membership.person_id not in self.persons:
                return denied("membership names an absent person")
            self.memberships[(membership.person_id, membership.scope)] = membership
        return complete()

    async def correlate_kratos(
        self, *, person_id: str, kratos_identity_id: str
    ) -> Outcome:
        self._up()
        person = self.persons.get(person_id)
        if person is None:
            return denied("absent")
        if person.kratos_identity_id is None:
            for other in self.persons.values():
                if (
                    other.kratos_identity_id == kratos_identity_id
                    and other.person_id != person_id
                ):
                    return denied("kratos-id-claimed")
            self.persons[person_id] = ManagedPerson(
                person_id=person.person_id, lifecycle=person.lifecycle,
                kratos_identity_id=kratos_identity_id,
                display_name=person.display_name,
                revision=person.revision + 1,
            )
            return complete()
        if person.kratos_identity_id == kratos_identity_id:
            return complete()
        return denied("kratos-id-mismatch")

    async def set_lifecycle(
        self, *, person_id: str, lifecycle: PersonLifecycle
    ) -> Outcome:
        self._up()
        person = self.persons.get(person_id)
        if person is None:
            return denied("absent")
        self.persons[person_id] = ManagedPerson(
            person_id=person.person_id, lifecycle=lifecycle,
            kratos_identity_id=person.kratos_identity_id,
            display_name=person.display_name,
            revision=person.revision + 1,
        )
        return complete()

    async def revoke_binding(self, *, account: Identifier) -> Outcome:
        self._up()
        account_id = self.owners.get(account)
        held = self.accounts.get(account_id or "")
        if held is None or held.state is not AccountState.BOUND:
            return denied("not-bound")
        self.accounts[held.account_id] = ManagedAccount(
            account_id=held.account_id, person_id=held.person_id,
            platform=held.platform, identifiers=held.identifiers,
            state=AccountState.REVOKED, label=held.label,
            revision=held.revision + 1,
        )
        return complete()

    async def set_membership(
        self, *, person_id: str, scope: Scope,
        member: bool, source: str,
    ) -> Outcome:
        self._up()
        if person_id not in self.persons:
            return denied("absent")
        key = (person_id, scope)
        current = self.memberships.get(key)
        self.memberships[key] = ManagedMembership(
            person_id=person_id, scope=scope,
            state=MembershipState.MEMBER if member else MembershipState.LEFT,
            source=source,
            revision=(current.revision if current else 0) + 1,
        )
        return complete()

    async def memberships_for(
        self, person_id: str
    ) -> tuple[ManagedMembership, ...]:
        self._up()
        pid = self._canonical(person_id)
        return tuple(m for (p, _), m in self.memberships.items() if p == pid)

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        self._up()
        return tuple(m for (_, s), m in self.memberships.items() if s == scope)

    async def add_alias(self, *, former_id: str, person_id: str) -> Outcome:
        self._up()
        if former_id in self.persons:
            return denied("former id is a live person")
        existing = self.aliases.get(former_id)
        if existing is not None and existing != person_id:
            return denied("alias points elsewhere")
        if person_id not in self.persons:
            return denied("absent")
        self.aliases[former_id] = person_id
        return complete()

    async def record_operation(self, record: OperationRecord) -> Outcome:
        self._up()
        existing = self.operations.get(record.operation_id)
        if existing is None:
            self.operations[record.operation_id] = record
            return complete()
        if existing.kind != record.kind or existing.snapshot != record.snapshot:
            raise OperationConflictError(
                f"{record.operation_id} recorded a different effect"
            )
        return complete()  # replay of the SAME effect is a no-op

    async def operation(self, operation_id: str) -> OperationRecord | None:
        self._up()
        return self.operations.get(operation_id)

    async def update_operation(self, record: OperationRecord) -> Outcome:
        self._up()
        if record.operation_id not in self.operations:
            return denied("absent")
        self.operations[record.operation_id] = record
        return complete()

    async def incomplete_operations(self) -> tuple[OperationRecord, ...]:
        self._up()
        return tuple(
            r for r in self.operations.values()
            if r.state is not OperationJournalState.COMPLETE
        )

    async def block_authority(self, block: AuthorityBlock) -> Outcome:
        self._up()
        self.blocks[(block.person_id, block.scope)] = block
        return complete()

    async def authority_blocks(self) -> tuple[AuthorityBlock, ...]:
        self._up()
        return tuple(self.blocks.values())

    async def clear_block(self, *, person_id: str, scope: Scope) -> Outcome:
        self._up()
        self.blocks.pop((person_id, scope), None)
        return complete()

    async def aclose(self) -> None:
        ...


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
        """The containment chain above (ns, obj), published as subject-set
        tuples — Room→unit→workspace, matching CONTAINMENT_RELATIONS."""
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

    async def aclose(self) -> None:
        ...


def unavailable_outcome() -> Outcome:
    return unavailable("down")


class FakeTopologySource(TopologySource):
    """Declared scopes + parents. `down=True` raises like a real read
    failure; an undeclared scope answers `False`, distinctly."""

    def __init__(
        self,
        *,
        rooms: dict[str, tuple[str, bool]] | None = None,
        units: dict[str, str] | None = None,
        workspaces: set[str] | None = None,
    ) -> None:
        # rooms: room_scope_id -> (unit, open); units: unit -> workspace
        self.rooms = dict(rooms or {})
        self.units = dict(units or {})
        self.workspaces = set(workspaces or set())
        self.down = False

    async def scope_exists(self, scope: Scope) -> bool:
        if self.down:
            raise RuntimeError("fake topology is down")
        if scope.kind is ScopeKind.WORKSPACE:
            return scope.id in self.workspaces
        if scope.kind is ScopeKind.UNIT:
            return scope.id in self.units
        return scope.id in self.rooms

    async def parent_of(self, scope: Scope) -> Scope | None:
        if self.down:
            raise RuntimeError("fake topology is down")
        if scope.kind is ScopeKind.ROOM:
            unit = self.rooms.get(scope.id)
            return Scope(ScopeKind.UNIT, unit[0]) if unit else None
        if scope.kind is ScopeKind.UNIT:
            ws = self.units.get(scope.id)
            return Scope(ScopeKind.WORKSPACE, ws) if ws else None
        return None

    async def is_open_room(self, scope: Scope) -> bool:
        if self.down:
            raise RuntimeError("fake topology is down")
        entry = self.rooms.get(scope.id)
        return bool(entry and entry[1]) if scope.kind is ScopeKind.ROOM else False


class FakeIdentityRegistry(IdentityRegistry):
    """In-memory Kratos records keyed by `external_id` — same rules: one
    identity per external id, `active` gates state, `down` is the outage."""

    def __init__(self) -> None:
        self.identities: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, int] = {}
        self.down = False
        self._counter = 0

    async def ensure_identity(
        self, *, external_id: str, traits: dict[str, Any]
    ) -> tuple[Outcome, str | None]:
        if self.down:
            return unavailable_outcome(), None
        existing = self.identities.get(external_id)
        if existing is not None:
            return complete("read-back"), existing["id"]
        self._counter += 1
        identity_id = f"kratos-{self._counter}"
        self.identities[external_id] = {
            "id": identity_id, "state": "active", "traits": dict(traits),
        }
        return complete(), identity_id

    async def identity_state(self, *, external_id: str) -> CheckResult:
        if self.down:
            return check_unavailable("down")
        ident = self.identities.get(external_id)
        if ident is None:
            return unknown_scope("absent")
        return allowed() if ident["state"] == "active" else check_denied("inactive")

    async def set_state(self, *, external_id: str, active: bool) -> Outcome:
        if self.down:
            return unavailable_outcome()
        ident = self.identities.get(external_id)
        if ident is None:
            return denied("absent")
        ident["state"] = "active" if active else "inactive"
        return complete()

    async def drop_sessions(self, *, external_id: str) -> Outcome:
        if self.down:
            return unavailable_outcome()
        if external_id not in self.identities:
            return denied("absent")
        self.sessions.pop(external_id, None)
        return complete()

    async def aclose(self) -> None:
        ...
