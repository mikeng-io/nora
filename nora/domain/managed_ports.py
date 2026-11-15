"""Managed identity and scoped access ports (`design/managed-identity-access/`).

The file backend and the managed backend implement the SAME ports — the
contract suites in tests/contract/ hold both to one behaviour, and the file
backend answers managed mutations `denied` rather than pretending it can
apply them: TOML is operator-maintained, a write it cannot take is an
authoritative refusal, not a failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from nora.domain.account_linking import ConsentProof, PendingLink
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
)
from nora.domain.management import Operation, Scope


@runtime_checkable
class PersonDirectory(Protocol):
    """Which person does this account belong to — the read half of binding.

    Both backends answer it. `resolve` is the flat `(platform, value)` lookup
    a sender arrives as; `account_for` names the exact typed identifier;
    `canonical_person` folds a retired id through the alias chain so
    historical `person_id` references keep resolving after a merge
    (`identity.md`). `None` is a first-class answer — an unmapped account —
    never an error; implementations raise on a genuine store failure.
    """

    async def resolve(self, platform: str, value: str) -> BoundAccount | None:
        """The bound account any matchable identifier `(platform, value)`
        names, or `None`."""
        ...

    async def account_for(
        self, platform: str, kind: str, value: str
    ) -> BoundAccount | None:
        """The bound account the exact `Identifier` names, or `None`."""
        ...

    async def accounts_for(self, person_id: str) -> tuple[BoundAccount, ...]:
        """Every active binding on the canonical person — empty for an
        unknown person, and aliases resolve before the read."""
        ...

    async def canonical_person(self, person_id: str) -> str | None:
        """The surviving person id `person_id` resolves to through the alias
        chain, or `None` when no such person exists."""
        ...

    async def person_lifecycle(self, person_id: str) -> PersonLifecycle | None:
        """The canonical person's global state, or `None` when unknown.
        File mode has no lifecycle and answers `ACTIVE` for mapped persons."""
        ...

    async def identity_held(self, person_id: str) -> bool:
        """An unfinished publication blocks the canonical identity across all scopes."""
        ...

    async def aclose(self) -> None:
        ...


@runtime_checkable
class BindingStore(PersonDirectory, Protocol):
    """The managed write half: registrations, lifecycle, membership,
    aliases and the operation journal.

    A registration establishes an INDEPENDENT person per unclaimed account —
    grouping is decided by the bilateral link service, never inferred from a
    name or a claimed number. Identifier ownership is enforced at the write
    boundary: inserting an identifier another account already owns raises
    `IdentifierConflictError`, it does not merge.

    Implementations raise on a genuine store failure; the service maps that
    to `unavailable`. Every mutator returns an `Outcome` so the file backend
    can refuse honestly.
    """

    def mutation(self) -> AbstractAsyncContextManager[None]:
        """Serialize managed orchestration while preserving individually committed saga steps."""
        ...

    async def hold_identity(self, *, person_id: str, operation_id: str) -> Outcome:
        """Persist the owning operation's publication hold before changing dependent stores."""
        ...

    async def release_identity_hold(self, *, person_id: str, operation_id: str) -> Outcome:
        """Release this operation's hold without clearing another operation's hold."""
        ...

    async def person(self, person_id: str) -> ManagedPerson | None:
        """The person's record including lifecycle and Kratos correlation."""
        ...

    async def account_record(self, account_id: str) -> ManagedAccount | None:
        """One account by its surrogate id — the stable handle a revocation
        names."""
        ...

    async def record_for(self, *, account: Identifier) -> ManagedAccount | None:
        """The binding an exact identifier resolves to, WITH its revision —
        the record an `ownership` observed-revision compares against."""
        ...

    async def register(
        self, *, person: ManagedPerson, account: ManagedAccount,
        membership: ManagedMembership | None = None,
    ) -> Outcome:
        """Atomically register the person, the account and (when given) its
        onboarding membership — one transaction, so a partial registration
        is never visible.

        Idempotent: replaying the same effect returns `complete` with the
        recorded state; an identifier owned by a DIFFERENT account raises
        `IdentifierConflictError`.
        """
        ...

    async def correlate_kratos(
        self, *, person_id: str, kratos_identity_id: str
    ) -> Outcome:
        """Record the person↔Kratos correlation read back after create —
        `denied` when the person already correlates to a DIFFERENT identity."""
        ...

    async def set_lifecycle(
        self, *, person_id: str, lifecycle: PersonLifecycle
    ) -> Outcome:
        """Global suspension or reinstatement — bumps the person revision."""
        ...

    async def revoke_binding(self, *, account: Identifier) -> Outcome:
        """End one account's binding — `denied` when the identifier is not
        bound. Other accounts and the person are untouched."""
        ...

    async def set_membership(
        self, *, person_id: str, scope: Scope,
        member: bool, source: str,
    ) -> Outcome:
        """The (person, scope) standing — leaving is a state, not a delete,
        so provenance survives a rejoin. `member=True` adds `source` to the
        row's supports; `member=False` ends the standing, clearing every
        support (an operator removal is never a per-source correction)."""
        ...

    async def drop_membership_support(
        self, *, person_id: str, scope: Scope, source: str,
    ) -> Outcome:
        """Retract ONE source's support for the membership — the roster-
        departure verb: a sync withdrawal cannot erase an independently
        established standing. The row goes `left` only when its last
        support is gone."""
        ...

    async def memberships_for(
        self, person_id: str
    ) -> tuple[ManagedMembership, ...]:
        ...

    async def members_of(self, scope: Scope) -> tuple[ManagedMembership, ...]:
        ...

    async def consolidate(self, *, survivor: str, former: str) -> Outcome:
        """Retire `former` into `survivor` atomically (`identity.md`
        consolidation): accounts move keeping their ownership records,
        membership supports merge per scope, authority blocks follow the
        canonical person, the former id becomes an alias and its row
        retires. History is preserved, never rewritten — the survivor's
        revision and the moved accounts' revisions bump so observers see
        the identity change. `denied` when either side is inactive or the
        former id is not a live person; idempotent on replay."""
        ...

    async def add_alias(self, *, former_id: str, person_id: str) -> Outcome:
        """Record a retired id for a surviving person — `denied` when the
        former id is a LIVE person or already aliases a different one."""
        ...

    # --- the operation journal ------------------------------------------------

    async def record_operation(self, record: OperationRecord) -> Outcome:
        """Journal the intended effect before the first store write —
        `denied` when `operation_id` already records a DIFFERENT effect."""
        ...

    async def operation(self, operation_id: str) -> OperationRecord | None:
        ...

    async def update_operation(
        self, record: OperationRecord
    ) -> Outcome:
        """Persist the record's new state/steps — the caller passes the whole
        revised record so step transitions are explicit."""
        ...

    async def incomplete_operations(self) -> tuple[OperationRecord, ...]:
        """Every `pending`/`incomplete`/`blocked` operation — the
        reconciler's queue, and the restart recovery set."""
        ...

    # --- revocation-first authority blocks -------------------------------------

    async def block_authority(self, block: AuthorityBlock) -> Outcome:
        """Raise a local block on (person, scope) — persists across restart
        until reconciliation clears it."""
        ...

    async def authority_blocks(self) -> tuple[AuthorityBlock, ...]:
        """Every standing block — check() consults this first."""
        ...

    async def clear_block(self, *, person_id: str, scope: Scope) -> Outcome:
        """Remove the block once reconciliation confirms the revocation —
        not before."""
        ...


@runtime_checkable
class TopologySource(Protocol):
    """Where scopes and their containment come from — Nora's own
    configuration (`authorization.md`), never Keto and never a caller's
    claim. Implementations raise on a genuine read failure; the caller maps
    that to `unavailable`, distinct from a scope that provably does not
    exist."""

    async def scope_exists(self, scope: Scope) -> bool:
        """The scope is a real workspace, unit or room in the topology."""
        ...

    async def parent_of(self, scope: Scope) -> Scope | None:
        """The one parent this scope sits inside — a room's declared unit, a
        unit's workspace; `None` for a workspace and for an unknown scope."""
        ...

    async def is_open_room(self, scope: Scope) -> bool:
        """The room's explicit open-admission flag (file mode's `persons =
        "*"`). `False` for non-room scopes and unknown rooms."""
        ...

    async def scopes(self) -> tuple[Scope, ...]:
        """Every canonical scope the topology declares — workspaces, units
        and rooms under every admitted id form. The enumeration a
        person-scoped authority read uses; Keto object existence is never
        evidence, so the set comes from Nora's own topology only."""
        ...


@runtime_checkable
class AuthorityBackend(Protocol):
    """The scoped-grant engine behind `check` — Keto in the managed
    deployment, the file adapter answering the same verdicts from
    `policy.toml`.

    Semantics are the committed OPL's: `grant_<permit>` and `deny_<permit>`
    are the write relations, deny wins at every scope and down every
    ancestor, `restricted` denies everything on its scope, `member` confers
    speak+view on a ROOM only, and management permits inherit downward while
    parent membership confers nothing. A direct tuple named like a permit
    never authorizes (strict mode).

    Answers are VALUES, not exceptions: a transport failure, malformed
    verdict or unreachable engine is `unavailable` — the port's whole job is
    that distinction. `relations_on` is the read-back a mutation verifies
    itself against before reporting `complete`.
    """

    async def check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        ...

    async def put_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        ...

    async def drop_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        ...

    async def publish_containment(self, *, scope: Scope, parent: Scope) -> Outcome:
        """Write `parent` as this scope's containment link — only called
        after the topology source verified the pairing is real."""
        ...

    async def relations_on(
        self, *, scope: Scope
    ) -> CheckResult | frozenset[tuple[str, str]]:
        """The (relation, person_id) pairs currently asserted on the scope —
        the mutation read-back. `CheckResult` carries `unavailable` when the
        read itself cannot answer."""
        ...

    async def aclose(self) -> None:
        ...


@runtime_checkable
class IdentityRegistry(Protocol):
    """The managed identity record Kratos owns (`identity.md`): creation
    keyed by `external_id`, lifecycle state, session teardown. It never
    decides account ownership — that is the binding store's job — and it
    never holds platform identifiers in traits."""

    async def ensure_identity(
        self, *, external_id: str, traits: dict[str, Any]
    ) -> tuple[Outcome, str | None]:
        """Create (or read back) the identity for `external_id` — returns
        the kratos identity id. `unavailable` when the service cannot
        answer; a conflict on an external id resolves by read-back, never by
        trusting the create."""
        ...

    async def identity_state(self, *, external_id: str) -> CheckResult:
        """`allowed` while the identity is active, `denied` when inactive
        (state=inactive gates real session checks in v26.2.0), `unknown` —
        `CheckResult(UNKNOWN_SCOPE)` — when absent."""
        ...

    async def set_state(
        self, *, external_id: str, active: bool
    ) -> Outcome:
        ...

    async def drop_sessions(self, *, external_id: str) -> Outcome:
        """End every derived session for the identity — absent sessions are
        a completed effect, not an error."""
        ...

    async def aclose(self) -> None:
        ...


@runtime_checkable
class LinkRequestStore(Protocol):
    """Durable bilateral link consent (`account-linking.md`) — the request
    row and its decision evidence, serialized under the request's lock.
    The store records consent; it never publishes ownership."""

    async def create(self, link: PendingLink) -> PendingLink:
        """Persist a new request — `LinkStateError` when the id already
        names a different effect or a recorded decision."""
        ...

    async def get(self, request_id: str) -> PendingLink | None:
        ...

    async def decide(
        self, operation: Operation, proof: ConsentProof, *, now: datetime
    ) -> PendingLink:
        """Record one holder decision — `LinkStateError` when the proof
        does not authenticate it, and idempotent on a true retry."""
        ...

    async def begin_commit(
        self, request_id: str, *, owners: Mapping[Identifier, str],
        active_people: frozenset[str], revisions: Mapping[str, str],
        now: datetime,
    ) -> PendingLink:
        """Atomically move a fully-approved request to `COMMITTING`,
        revalidating the recorded owners/lifecycle/revisions against these
        live reads. `LinkStateError` on any drift."""
        ...

    async def mark_committed(self, request_id: str) -> PendingLink:
        """Terminal publication marker — a `COMMITTING` request becomes
        `COMMITTED`; already-committed replays return the same row."""
        ...

    async def aclose(self) -> None:
        ...
