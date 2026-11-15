"""The file backend over the loaded `Persons`/`Policy` snapshots — the
`file` side of `nora.domain.ports`' managed ports (`design/managed-identity-access/`).

Reads answer exactly what the TOML pair already knows: flat sender
resolution, exact account lookup, alias folding, room admission and the
unit/workspace topology policy.toml declares. EVERY managed mutator answers
`denied` — identity.toml and policy.toml are operator-maintained files, so
a write they cannot take is an authoritative refusal (`denied`), never a
silently-dropped success and never an `unavailable` that would read as a
retryable outage.
"""

from __future__ import annotations

from typing import Any

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.managed import (
    AuthorityBlock,
    CheckResult,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    OperationRecord,
    Outcome,
    PersonLifecycle,
    allowed,
    check_denied,
    denied,
    split_room_scope_id,
    unknown_scope,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.ports import AuthorityBackend, BindingStore, TopologySource
from nora.persons import Persons
from nora.policy import DEFAULT_WORKSPACE, Policy

_OPERATOR_MAINTAINED = "operator-maintained: edit identity.toml/policy.toml"


class FileBindingStore(BindingStore):
    """Reads delegate to `Persons`; writes refuse."""

    def __init__(self, persons: Persons) -> None:
        self._persons = persons

    async def resolve(self, platform: str, value: str) -> BoundAccount | None:
        return self._persons.account_for(platform, value)

    async def account_for(
        self, platform: str, kind: str, value: str
    ) -> BoundAccount | None:
        account = self._persons.account_for(platform, value)
        if account is None:
            return None
        # The file's index is flat (platform, value); confirm the account it
        # names really carries an identifier of THIS kind, so an exact
        # (platform, kind, value) question never coalesces two kinds that
        # share a value.
        ident = Identifier(platform=platform, kind=kind, value=value)
        return account if ident in account.identifiers else None

    async def accounts_for(self, person_id: str) -> tuple[BoundAccount, ...]:
        return self._persons.accounts_for(person_id)

    async def canonical_person(self, person_id: str) -> str | None:
        canonical = self._persons.subject_id_for(person_id)
        return canonical if canonical in self._persons.by_person else None

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        # The file has no lifecycle: a mapped person is ACTIVE, an unmapped
        # one is absent — never SUSPENDED out of nowhere.
        canonical = await self.canonical_person(person_id)
        return PersonLifecycle.ACTIVE if canonical is not None else None

    async def person(self, person_id: str) -> ManagedPerson | None:
        canonical = await self.canonical_person(person_id)
        if canonical is None:
            return None
        ref = self._persons.by_person[canonical]
        return ManagedPerson(
            person_id=canonical,
            lifecycle=PersonLifecycle.ACTIVE,
            display_name=ref.name,
        )

    async def account_record(self, account_id: str) -> ManagedAccount | None:
        # File mode has no surrogate account ids — nothing ever matches.
        return None

    async def record_for(self, *, account: Identifier) -> ManagedAccount | None:
        # No revisions exist in file mode — an ownership observed-revision
        # has nothing to compare against, so there is no record.
        return None

    async def register(
        self, *, person: ManagedPerson, account: ManagedAccount,
        membership: ManagedMembership | None = None,
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def correlate_kratos(
        self, *, person_id: str, kratos_identity_id: str
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def set_lifecycle(
        self, *, person_id: str, lifecycle: PersonLifecycle
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def revoke_binding(self, *, account: Identifier) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def set_membership(
        self, *, person_id: str, scope: Scope,
        member: bool, source: str,
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def memberships_for(
        self, person_id: str
    ) -> tuple[ManagedMembership, ...]:
        return ()

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        return ()

    async def add_alias(self, *, former_id: str, person_id: str) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def record_operation(self, record: OperationRecord) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def operation(self, operation_id: str) -> OperationRecord | None:
        return None

    async def update_operation(self, record: OperationRecord) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def incomplete_operations(self) -> tuple[OperationRecord, ...]:
        return ()

    async def block_authority(self, block: AuthorityBlock) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def authority_blocks(self) -> tuple[AuthorityBlock, ...]:
        return ()

    async def clear_block(self, *, person_id: str, scope: Scope) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def aclose(self) -> None:
        ...


class FileTopologySource(TopologySource):
    """Scopes and containment read off `policy.toml`'s own declarations —
    rooms, their units, and the units' workspaces (`authorization.md`
    «topology stays Nora configuration»)."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    async def scope_exists(self, scope: Scope) -> bool:
        if scope.kind is ScopeKind.WORKSPACE:
            return (
                scope.id == DEFAULT_WORKSPACE
                or scope.id in self._policy.unit_workspaces().values()
            )
        if scope.kind is ScopeKind.UNIT:
            return scope.id in self._policy.unit_workspaces()
        platform, conversation_id = split_room_scope_id(scope.id)
        return self._policy.conversation(platform, conversation_id) is not None

    async def parent_of(self, scope: Scope) -> Scope | None:
        if scope.kind is ScopeKind.ROOM:
            platform, conversation_id = split_room_scope_id(scope.id)
            convo = self._policy.conversation(platform, conversation_id)
            if convo is None or not convo.unit.strip():
                return None
            return Scope(ScopeKind.UNIT, convo.unit)
        if scope.kind is ScopeKind.UNIT:
            workspace = self._policy.workspace_of(scope.id)
            return (
                Scope(ScopeKind.WORKSPACE, workspace)
                if workspace is not None
                else None
            )
        return None

    async def is_open_room(self, scope: Scope) -> bool:
        if scope.kind is not ScopeKind.ROOM:
            return False
        platform, conversation_id = split_room_scope_id(scope.id)
        convo = self._policy.conversation(platform, conversation_id)
        return convo is not None and convo.persons is None


class FileAuthorityBackend(AuthorityBackend):
    """Room admission off `Policy.is_allowed`; management verbs and every
    mutator refuse — file mode delegates no scoped authority."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    async def check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        if scope.kind is not ScopeKind.ROOM:
            # policy.toml has no workspace/unit admission — the scoped
            # management layer is the managed backend's alone.
            return check_denied("file mode has no scoped management")
        platform, conversation_id = split_room_scope_id(scope.id)
        if self._policy.conversation(platform, conversation_id) is None:
            return unknown_scope("no such room in policy.toml")
        if permit in ("speak", "view"):
            return (
                allowed()
                if self._policy.is_allowed(
                    platform=platform,
                    conversation_id=conversation_id,
                    person_id=person_id,
                )
                else check_denied()
            )
        return check_denied("file mode delegates no management")

    async def put_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def drop_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def publish_containment(self, *, scope: Scope, parent: Scope) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def relations_on(
        self, *, scope: Scope
    ) -> CheckResult | frozenset[tuple[str, str]]:
        # No relation store: the honest read-back is the empty set.
        return frozenset()

    async def aclose(self) -> None:
        ...


class FileIdentityRegistry:
    """`IdentityRegistry` for file mode: there is no managed identity
    record, so every verb refuses — correlation, session teardown and state
    checks are managed-mode facts and file mode must not pretend them."""

    async def ensure_identity(
        self, *, external_id: str, traits: dict[str, Any]
    ) -> tuple[Outcome, str | None]:
        return denied(_OPERATOR_MAINTAINED), None

    async def identity_state(self, *, external_id: str) -> CheckResult:
        return unknown_scope("file mode holds no identity record")

    async def set_state(self, *, external_id: str, active: bool) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def drop_sessions(self, *, external_id: str) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def aclose(self) -> None:
        ...
