"""The file backend over the loaded `Persons`/`Policy` snapshots — the
`file` side of `nora.domain.managed_ports` (`design/managed-identity-access/`).

Reads answer exactly what the TOML pair already knows: flat sender
resolution, exact account lookup, alias folding, room admission and the
unit/workspace topology policy.toml declares. EVERY managed mutator answers
`denied` — identity.toml and policy.toml are operator-maintained files, so
a write they cannot take is an authoritative refusal (`denied`), never a
silently-dropped success and never an `unavailable` that would read as a
retryable outage.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import datetime
from typing import Any

from nora.domain.account_linking import (
    ConsentProof,
    LinkStateError,
    PendingLink,
)
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
    room_scope_id,
    split_room_scope_id,
    unknown_scope,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    LinkRequestStore,
    TopologySource,
)
from nora.domain.management import LinkSide, Operation, Scope, ScopeKind
from nora.persons import Persons
from nora.policy import DEFAULT_WORKSPACE, Policy

_OPERATOR_MAINTAINED = "operator-maintained: edit identity.toml/policy.toml"


class FileBindingStore(BindingStore):
    """Reads delegate to `Persons`; writes refuse."""

    def __init__(self, persons: Persons) -> None:
        self._persons = persons

    def mutation(self) -> AbstractAsyncContextManager[None]:
        return nullcontext()

    async def identity_held(self, person_id: str) -> bool:
        return False

    async def hold_identity(self, *, person_id: str, operation_id: str) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def release_identity_hold(self, *, person_id: str, operation_id: str) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def resolve(self, platform: str, value: str) -> BoundAccount | None:
        return self._persons.account_for(platform, value)

    async def account_for(
        self, platform: str, kind: str, value: str
    ) -> BoundAccount | None:
        account = self._persons.account_for(platform, value)
        if account is None:
            return None
        # The file's index is flat (platform, value): confirm the account
        # really carries an identifier of THIS kind — an exact question
        # never coalesces kinds sharing a value.
        ident = Identifier(platform=platform, kind=kind, value=value)
        return account if ident in account.identifiers else None

    async def accounts_for(self, person_id: str) -> tuple[BoundAccount, ...]:
        return self._persons.accounts_for(person_id)

    async def canonical_person(self, person_id: str) -> str | None:
        # `subject_id_for` folds retired ids; whether the canonical id is a
        # KNOWN person is `is_allowed`'s scoped question (a room's `persons`
        # list names who the file admits, index card or not), so existence
        # is answered by the check, not here.
        return self._persons.subject_id_for(person_id)

    async def former_persons(self, person_id: str) -> tuple[str, ...]:
        return self._persons.former_ids_for(self._persons.subject_id_for(person_id))

    async def persons(self) -> tuple[ManagedPerson, ...]:
        return tuple(
            ManagedPerson(
                person_id=pid,
                lifecycle=PersonLifecycle.ACTIVE,
                display_name=ref.name,
            )
            for pid, ref in sorted(self._persons.by_person.items())
        )

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        # The file has no lifecycle: a mapped person is ACTIVE, an unmapped
        # one is absent — never SUSPENDED out of nowhere.
        canonical = await self.canonical_person(person_id)
        return PersonLifecycle.ACTIVE if canonical is not None else None

    async def person(self, person_id: str) -> ManagedPerson | None:
        ref = self._persons.by_person.get(
            self._persons.subject_id_for(person_id))
        if ref is None:
            return None
        return ManagedPerson(
            person_id=ref.person_id,
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

    async def drop_membership_support(
        self, *, person_id: str, scope: Scope, source: str
    ) -> Outcome:
        return denied(_OPERATOR_MAINTAINED)

    async def consolidate(self, *, survivor: str, former: str) -> Outcome:
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

    async def scopes(self) -> tuple[Scope, ...]:
        """Every scope policy.toml admits — the default workspace, every
        declared unit workspace and unit, and every room under every id
        form policy would admit a fact against."""
        out = {Scope(ScopeKind.WORKSPACE, DEFAULT_WORKSPACE)}
        out |= {
            Scope(ScopeKind.WORKSPACE, workspace)
            for workspace in self._policy.unit_workspaces().values()
        }
        out |= {
            Scope(ScopeKind.UNIT, unit)
            for unit in self._policy.unit_workspaces()
        }
        for convo in self._policy.rooms():
            out |= {
                Scope(ScopeKind.ROOM, room_scope_id(convo.platform, cid))
                for cid in convo.ids
            }
        return tuple(sorted(out, key=lambda s: (s.kind.value, s.id)))


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


class FileLinkRequests(LinkRequestStore):
    """`LinkRequestStore` for file mode: a durable consent row is a managed
    record — there is no place for it in TOML, so recording or committing
    one refuses, and reads answer honestly: nothing is stored."""

    async def create(
        self, link: PendingLink, *, nonces: Mapping[LinkSide, str]
    ) -> PendingLink:
        raise LinkStateError(_OPERATOR_MAINTAINED)

    async def get(self, request_id: str) -> PendingLink | None:
        return None

    async def challenge(self, request_id: str, *, side: LinkSide) -> str | None:
        return None

    async def decide(
        self, operation: Operation, proof: ConsentProof, *, now: datetime
    ) -> PendingLink:
        raise LinkStateError(_OPERATOR_MAINTAINED)

    async def begin_commit(
        self, request_id: str, *, owners: Mapping[Identifier, str],
        active_people: frozenset[str], revisions: Mapping[str, str],
        now: datetime,
    ) -> PendingLink:
        raise LinkStateError(_OPERATOR_MAINTAINED)

    async def mark_committed(self, request_id: str) -> PendingLink:
        raise LinkStateError(_OPERATOR_MAINTAINED)

    async def aclose(self) -> None:
        ...
