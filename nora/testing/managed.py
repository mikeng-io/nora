"""Managed-port contract fakes; authority follows deploy/ory/keto/namespaces.ts."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.managed import (
    CONTAINMENT_RELATIONS,
    KETO_NAMESPACES,
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
    SourceObservation,
    allowed,
    check_denied,
    check_unavailable,
    complete,
    denied,
    keto_namespace,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    ScopedRelation,
    TopologySource,
)
from nora.domain.management import (
    Scope,
    ScopeKind,
)
from nora.testing.managed_registry import (
    FakeIdentityRegistry as FakeIdentityRegistry,
)
from nora.testing.managed_registry import (
    FakeLinkRequests as FakeLinkRequests,
)
from nora.testing.managed_registry import (
    unavailable_outcome,
)


class FakeBindingStore(BindingStore):
    """In-memory bindings with switchable outages."""

    def __init__(self) -> None:
        self._mutation_lock = asyncio.Lock()
        self.identity_holds: set[tuple[str, str]] = set()
        self.people: dict[str, ManagedPerson] = {}
        self.accounts: dict[str, ManagedAccount] = {}
        self.owners: dict[Identifier, str] = {}  # identifier -> account_id
        self.memberships: dict[tuple[str, Scope], ManagedMembership] = {}
        self.supports: dict[tuple[str, Scope], set[str]] = {}
        self.aliases: dict[str, str] = {}
        self.operations: dict[str, OperationRecord] = {}
        self.blocks: dict[tuple[str, Scope], AuthorityBlock] = {}
        self.scoped_names: dict[tuple[str, Scope], tuple[str, str]] = {}
        self.observations: dict[tuple[str, str, Scope], SourceObservation] = {}
        self.down = False

    def mutation(self) -> AbstractAsyncContextManager[None]:
        self._up()
        return self._mutation_lock

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
        return pid if pid in self.people else None

    async def identity_ids(self, person_id: str) -> tuple[str, ...] | None:
        self._up()
        canonical = self._canonical(person_id)
        if canonical not in self.people:
            return None
        return (canonical, *sorted(f for f, s in self.aliases.items() if s == canonical))

    async def former_persons(self, person_id: str) -> tuple[str, ...]:
        return (await self.identity_ids(person_id) or ())[1:]

    async def persons(self) -> tuple[ManagedPerson, ...]:
        self._up()
        return tuple(
            p for p in sorted(self.people.values(), key=lambda m: m.person_id)
            if p.lifecycle is PersonLifecycle.ACTIVE
        )

    async def identity_held(self, person_id: str) -> bool:
        self._up()
        return any(person in {person_id, self._canonical(person_id)}
                   for person, _ in self.identity_holds)

    async def hold_identity(self, *, person_id: str, operation_id: str) -> Outcome:
        self._up()
        if not operation_id.strip():
            raise ManagedError("an identity hold requires an owning operation")
        canonical = await self.canonical_person(person_id)
        if canonical is None:
            return denied("unknown-person")
        self.identity_holds.add((canonical, operation_id))
        return complete()

    async def release_identity_hold(self, *, person_id: str, operation_id: str) -> Outcome:
        self._up()
        if not operation_id.strip():
            raise ManagedError("an identity hold requires an owning operation")
        for person in {person_id, self._canonical(person_id)}:
            self.identity_holds.discard((person, operation_id))
        return complete()

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        self._up()
        person = self.people.get(self._canonical(person_id))
        return person.lifecycle if person else None

    async def person(self, person_id: str) -> ManagedPerson | None:
        self._up()
        return self.people.get(self._canonical(person_id))

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
        existing = self.people.get(person.person_id)
        if existing is not None:
            person = existing  # idempotent: keep lifecycle/correlation
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
        self.people.setdefault(person.person_id, person)
        for ident in account.identifiers:
            owner = self.owners.get(ident)
            held = self.accounts.get(owner or "")
            if held is None:
                continue
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
            if membership.person_id not in self.people:
                return denied("membership names an absent person")
            self.memberships[(membership.person_id, membership.scope)] = membership
            self.supports.setdefault(
                (membership.person_id, membership.scope), set()
            ).add(membership.source)
        return complete()

    async def bind_identifiers(
        self, *, account_id: str, identifiers: frozenset[Identifier]
    ) -> Outcome:
        self._up()
        held = self.accounts.get(account_id)
        if held is None or held.state is not AccountState.BOUND:
            return denied("account-not-bound")
        wanted = frozenset(identifiers)
        for ident in wanted:
            if ident.platform != held.platform:
                raise ManagedError(
                    "an identifier disagrees with the account's platform"
                )
        for ident in wanted:
            owner = self.owners.get(ident)
            if owner is not None and owner != account_id:
                # bound or revoked — a revoked binding keeps its ownership
                # reserved; nothing here re-points it.
                raise IdentifierConflictError(
                    f"{ident} is owned by a different account"
                )
        added = False
        for ident in wanted:
            if ident not in self.owners:
                self.owners[ident] = account_id
                added = True
        if added:
            self.accounts[account_id] = replace(
                held,
                identifiers=held.identifiers | wanted,
                revision=held.revision + 1,
            )
        return complete()

    async def correlate_kratos(
        self, *, person_id: str, kratos_identity_id: str
    ) -> Outcome:
        self._up()
        person = self.people.get(person_id)
        if person is None:
            return denied("absent")
        if person.kratos_identity_id is None:
            for other in self.people.values():
                if (
                    other.kratos_identity_id == kratos_identity_id
                    and other.person_id != person_id
                ):
                    return denied("kratos-id-claimed")
            self.people[person_id] = ManagedPerson(
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
        person = self.people.get(person_id)
        if person is None:
            return denied("absent")
        self.people[person_id] = ManagedPerson(
            person_id=person.person_id, lifecycle=lifecycle,
            kratos_identity_id=person.kratos_identity_id,
            display_name=person.display_name,
            revision=person.revision + 1,
        )
        return complete()

    async def set_display_name(
        self, *, person_id: str, display_name: str
    ) -> Outcome:
        self._up()
        person = self.people.get(self._canonical(person_id))
        if person is None:
            return denied("absent")
        self.people[person.person_id] = ManagedPerson(
            person_id=person.person_id, lifecycle=person.lifecycle,
            kratos_identity_id=person.kratos_identity_id,
            display_name=display_name,
            revision=person.revision,
        )
        return complete()

    async def set_scoped_name(
        self, *, person_id: str, scope: Scope, nickname: str,
        operation_id: str
    ) -> Outcome:
        self._up()
        if not nickname.strip():
            raise ManagedError("a scoped nickname cannot be blank")
        canonical = self._canonical(person_id)
        person = self.people.get(canonical)
        if person is None:
            return denied("absent-person")
        if person.lifecycle is not PersonLifecycle.ACTIVE:
            return denied("person-suspended")
        self.scoped_names[(canonical, scope)] = (nickname, operation_id)
        return complete()

    async def scoped_name_for(
        self, *, person_id: str, scope: Scope
    ) -> str | None:
        self._up()
        canonical = self._canonical(person_id)
        row = self.scoped_names.get((canonical, scope))
        return None if row is None else row[0]

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
        if person_id not in self.people:
            return denied("absent")
        key = (person_id, scope)
        current = self.memberships.get(key)
        if member:
            self.supports.setdefault(key, set()).add(source)
        else:
            self.supports.pop(key, None)
        self.memberships[key] = ManagedMembership(
            person_id=person_id, scope=scope,
            state=MembershipState.MEMBER if member else MembershipState.LEFT,
            source=source,
            revision=(current.revision if current else 0) + 1,
        )
        return complete()

    async def drop_membership_support(
        self, *, person_id: str, scope: Scope, source: str
    ) -> Outcome:
        self._up()
        if person_id not in self.people:
            return denied("absent")
        key = (person_id, scope)
        remaining = self.supports.get(key, set()) - {source}
        current = self.memberships.get(key)
        if remaining:
            self.supports[key] = remaining
            return complete()
        self.supports.pop(key, None)
        self.memberships[key] = ManagedMembership(
            person_id=person_id, scope=scope,
            state=MembershipState.LEFT, source=source,
            revision=(current.revision if current else 0) + 1,
        )
        return complete()

    def _with_sources(self, m: ManagedMembership) -> ManagedMembership:
        return replace(
            m, sources=frozenset(self.supports.get((m.person_id, m.scope), ()))
        )

    async def memberships_for(
        self, person_id: str
    ) -> tuple[ManagedMembership, ...]:
        self._up()
        pid = self._canonical(person_id)
        return tuple(
            self._with_sources(m)
            for (p, _), m in self.memberships.items() if p == pid
        )

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        self._up()
        return tuple(
            self._with_sources(m)
            for (_, s), m in self.memberships.items()
            if s == scope and m.state is MembershipState.MEMBER
        )

    async def consolidate(self, *, survivor: str, former: str) -> Outcome:
        self._up()
        if survivor not in self.people:
            return denied("absent-survivor")
        if former not in self.people:
            return complete() if self.aliases.get(former) == survivor else denied("absent-former")
        if (
            self.people[survivor].lifecycle is not PersonLifecycle.ACTIVE
            or self.people[former].lifecycle is not PersonLifecycle.ACTIVE
        ):
            return denied("person-inactive")
        for account_id, account in self.accounts.items():
            if account.person_id == former:
                self.accounts[account_id] = replace(
                    account, person_id=survivor,
                    revision=account.revision + 1,
                )
        for (pid, scope), sources in list(self.supports.items()):
            if pid == former:
                self.supports.setdefault((survivor, scope), set()).update(sources)
                del self.supports[(pid, scope)]
        fallen = {s: m for (p, s), m in self.memberships.items() if p == former}
        for scope in fallen:
            del self.memberships[(former, scope)]
        for scope in {
            s for (p, s) in self.supports if p == survivor
        } | set(fallen):
            existing = self.memberships.get((survivor, scope))
            fallen_m = fallen.get(scope)
            member = bool(self.supports.get((survivor, scope)))
            self.memberships[(survivor, scope)] = ManagedMembership(
                person_id=survivor, scope=scope,
                state=MembershipState.MEMBER if member else MembershipState.LEFT,
                source=(existing.source if existing is not None
                        else fallen_m.source if fallen_m is not None
                        else "consolidation"),
                revision=max(
                    existing.revision if existing is not None else 0,
                    fallen_m.revision if fallen_m is not None else 0,
                ) + 1,
            )
        for (pid, scope), block in list(self.blocks.items()):
            if pid == former and (survivor, scope) not in self.blocks:
                self.blocks[(survivor, scope)] = replace(block, person_id=survivor)
            if pid == former:
                del self.blocks[(pid, scope)]
        # The survivor's own same-scope nickname wins on a collision.
        for (pid, scope), row in list(self.scoped_names.items()):
            if pid == former:
                self.scoped_names.setdefault((survivor, scope), row)
                del self.scoped_names[(pid, scope)]
        self.aliases.update({f: survivor for f, s in self.aliases.items() if s == former})
        self.aliases[former] = survivor
        self.identity_holds = {
            (survivor if p == former else p, op)
            for (p, op) in self.identity_holds
        }
        del self.people[former]
        self.people[survivor] = replace(
            self.people[survivor], revision=self.people[survivor].revision + 1,
        )
        return complete()

    async def add_alias(self, *, former_id: str, person_id: str) -> Outcome:
        self._up()
        if former_id in self.people:
            return denied("former id is a live person")
        existing = self.aliases.get(former_id)
        if existing is not None and existing != person_id:
            return denied("alias points elsewhere")
        if person_id not in self.people:
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

    async def latest_profile_operation(
        self, person_id: str, *, scope: Scope | None = None
    ) -> OperationRecord | None:
        self._up()
        wanted = (
            None if scope is None
            else {"kind": scope.kind.value, "id": scope.id}
        )
        # `operations` is an insertion-ordered journal: the last matching
        # record is the newest intent for THIS profile target.
        for record in reversed(list(self.operations.values())):
            if (record.kind == "update_profile"
                    and record.snapshot.get("target_person") == person_id
                    and record.snapshot.get("profile_scope") == wanted):
                return record
        return None

    async def observation_for(
            self, *, service: str, collection: str,
            scope: Scope) -> SourceObservation | None:
        self._up()
        return self.observations.get((service, collection, scope))

    async def record_observation(self, observation: SourceObservation) -> Outcome:
        self._up()
        o = observation
        self.observations[(o.service, o.collection, o.scope)] = o
        return complete()

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
                isinstance(subject, tuple) and len(subject) == 3
                and subject[0] == "subjectset" and subject[1] in kinds
            ):
                out.add(ScopedRelation(
                    rel, Scope(kinds[subject[1]], subject[2])))
                continue
            return check_unavailable("unsupported subject shape")
        return frozenset(out)

    async def aclose(self) -> None:
        ...


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

    async def scopes(self) -> tuple[Scope, ...]:
        if self.down:
            raise RuntimeError("fake topology is down")
        return tuple(sorted(
            {Scope(ScopeKind.ROOM, r) for r in self.rooms}
            | {Scope(ScopeKind.UNIT, u) for u in self.units}
            | {Scope(ScopeKind.WORKSPACE, w) for w in self.workspaces},
            key=lambda s: (s.kind.value, s.id),
        ))


class UnavailableAccess:
    """An access facade whose grant read is UNAVAILABLE — the authority
    cannot answer, so every check fails closed rather than denied.

    Wraps a real facade and delegates everything but `permitted`, which is
    the read a fail-closed test must see fail.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def permitted(self, **kwargs):
        return check_unavailable()
