"""The managed-backend use cases: registration, scoped authority and
reconciliation (`design/managed-identity-access/management.md`,
`authorization.md`).

Every mutation flows one path: journal the intended effect → revalidate the
actor at commitment → apply the store effects step by step → read back →
mark `complete`. A step that cannot apply leaves the journal row
`incomplete`, and a revocation step that fails raises an `AuthorityBlock`
on the affected (person, scope) — the stale grant must not keep working
while a store is down. `reconcile` resumes exactly the steps a crash left,
which is why each step is idempotent on its own and replay needs no actor:
the actor was validated at first commitment, and the journaled effect is
the authority for finishing it.

Actors arrive already constructed — `Operation` itself refuses a kind/
target/payload mismatch and the actor-kind admission table, so the service
never re-checks those. What construction CANNOT check is live authority:
an operator's delegation standing NOW, a holder's binding standing NOW, the
scope's real containment NOW. That revalidation is this file's job.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast
from uuid import uuid4

from nora.domain.identity import Identifier
from nora.domain.managed import (
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
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    check_denied,
    check_unavailable,
    complete,
    denied,
    incomplete,
    relation_for,
    unavailable,
    unknown_scope,
)
from nora.domain.management import (
    AccessChange,
    AccessDirection,
    AccountObservation,
    AccountRegistration,
    ActorKind,
    Delegation,
    MembershipStanding,
    Operation,
    RosterSync,
    Scope,
    ScopeKind,
    Unlink,
)
from nora.domain.ports import (
    AuthorityBackend,
    BindingStore,
    IdentityRegistry,
    TopologySource,
)

log = logging.getLogger("nora.people.managed")

_REFUSED = "operation not wired in this slice"

#: The management permit a scoped operation requires of an OPERATOR actor,
#: revalidated live at commitment against the TARGET scope (a parent-scope
#: delegation reaches the target through the OPL traversal itself).
_REQUIRED_PERMIT: MappingProxyType[str, str] = MappingProxyType(
    {
        "set_membership": "manage_members",
        "grant_access": "manage_grants",
        "revoke_access": "manage_grants",
        "delegate": "delegate",
        "revoke_delegation": "delegate",
        "register_account": "manage_members",
        "update_profile": "manage_members",
    }
)

#: The kinds `AuthorityService` owns — reconcile routes on this.
_AUTHORITY_KINDS = frozenset(
    {"set_membership", "grant_access", "revoke_access",
     "delegate", "revoke_delegation", "sync_membership"}
)

#: A journaled store effect, produced LAZILY: a step replayed as already
#: done never builds the call it skips, so no coroutine is ever created
#: un-awaited.
Effect = Callable[[], Awaitable[Outcome]]
Effects = Callable[[], Iterable[Awaitable[Outcome]]]


def _scope_snapshot(scope: Scope) -> dict[str, str]:
    return {"kind": scope.kind.value, "id": scope.id}


def _scope_from(snapshot: dict[str, str]) -> Scope:
    return Scope(ScopeKind(snapshot["kind"]), snapshot["id"])


def _ident_snapshot(ident: Identifier) -> dict[str, str]:
    return {"platform": ident.platform, "kind": ident.kind, "value": ident.value}


def _ident_from(snapshot: dict[str, str]) -> Identifier:
    return Identifier(
        platform=snapshot["platform"], kind=snapshot["kind"], value=snapshot["value"]
    )


def _op_snapshot(op: Operation) -> MappingProxyType[str, Any]:
    """The effect the journal row records — enough to rebuild the op's
    write side after a restart (the actor is authority, not effect, so it
    is deliberately not journaled)."""
    payload = op.payload
    snap: dict[str, Any] = {"kind": op.kind}
    if isinstance(payload, AccountObservation):
        snap["identifiers"] = [_ident_snapshot(i) for i in payload.identifiers]
    elif isinstance(payload, AccountRegistration):
        snap["person_id"] = payload.person_id
        snap["scope"] = _scope_snapshot(payload.scope)
        snap["identifiers"] = [_ident_snapshot(i) for i in payload.identifiers]
    elif isinstance(payload, RosterSync):
        snap["members"] = [_ident_snapshot(i) for i in payload.members]
        snap["complete"] = payload.complete
    elif isinstance(payload, MembershipStanding):
        snap["member"] = payload.member
    elif isinstance(payload, AccessChange):
        snap["direction"] = payload.direction.value
        snap["grantee"] = payload.grantee
        snap["permits"] = sorted(payload.permits)
    elif isinstance(payload, Delegation):
        snap["delegatee"] = payload.delegatee
        snap["permits"] = sorted(payload.permits)
    elif isinstance(payload, Unlink):
        snap["expected_person"] = payload.expected_person
    if op.target.account is not None:
        snap["account"] = _ident_snapshot(op.target.account)
    if op.target.person_id is not None:
        snap["target_person"] = op.target.person_id
    if op.target.scope is not None:
        snap["target_scope"] = _scope_snapshot(op.target.scope)
    return MappingProxyType(snap)


async def _record(
    store: BindingStore, op: Operation, steps: tuple[str, ...]
) -> OperationRecord | Outcome:
    """Journal the intended effect. A recorded record is returned; an
    `Outcome` means the journal refused (conflict) or could not answer —
    the caller returns it and touches nothing."""
    record = OperationRecord(
        operation_id=op.operation_id,
        kind=op.kind,
        state=OperationJournalState.PENDING,
        steps=MappingProxyType(dict.fromkeys(steps, "pending")),
        snapshot=_op_snapshot(op),
    )
    outcome = await store.record_operation(record)
    return record if outcome.status is OutcomeStatus.COMPLETE else outcome


async def _step_done(store: BindingStore, record: OperationRecord, step: str) -> None:
    steps = dict(record.steps)
    steps[step] = "done"
    await store.update_operation(
        OperationRecord(
            operation_id=record.operation_id,
            kind=record.kind,
            state=OperationJournalState.INCOMPLETE,
            steps=MappingProxyType(steps),
            snapshot=record.snapshot,
        )
    )
    object.__setattr__(record, "steps", MappingProxyType(steps))


async def _finish(
    store: BindingStore,
    record: OperationRecord,
    state: OperationJournalState,
    outcome: str,
    detail: str = "",
) -> None:
    await store.update_operation(
        OperationRecord(
            operation_id=record.operation_id,
            kind=record.kind,
            state=state,
            steps=record.steps,
            outcome=outcome,
            detail=detail,
            snapshot=record.snapshot,
        )
    )


def _pending(record: OperationRecord, step: str) -> bool:
    return record.steps.get(step) != "done"


async def _recorded_replay(store: BindingStore, op: Operation) -> Outcome | None:
    """Non-`None` when `operation_id` is already journaled — BUT ONLY after
    the incoming effect is compared: the same effect returns the recorded
    state (the idempotency story is the journal row, not a re-run); a
    DIFFERENT effect under a used id is a conflict, never a silent replay of
    someone else's recorded outcome."""
    recorded = await store.operation(op.operation_id)
    if recorded is None:
        return None
    if recorded.kind != op.kind or recorded.snapshot != _op_snapshot(op):
        return denied("operation-conflict")
    if recorded.state is OperationJournalState.COMPLETE:
        return complete(recorded.detail or "replayed")
    return incomplete("replayed: still incomplete")


async def _revisions_fresh(store: BindingStore, op: Operation) -> Outcome | None:
    """Revalidate the revisions the spec required the submitter to observe —
    run at commitment, before ANY effect (and before a first commitment's
    journal row exists, so this is apply-only, never replay).

    `identity` compares the named (or acting) person's current revision;
    `ownership` compares the target account's binding revision — both via
    live reads. `authority` is unversioned this slice: Keto has no revision
    counter, so the mutation's own relation read-back is the check. An
    observed value that cannot be compared refuses rather than passing."""
    for name, observed in op.observed_revisions.items():
        if name == "identity":
            person_id = op.target.person_id or op.actor.person_id
            if person_id is None:
                continue
            person = await store.person(person_id)
            if person is None:
                return denied("unknown-person")
            if str(person.revision) != observed:
                return denied("stale-identity-revision")
        elif name == "ownership" and op.target.account is not None:
            record = await store.record_for(account=op.target.account)
            if record is not None and str(record.revision) != observed:
                return denied("stale-ownership-revision")
            # No binding yet: nothing to be stale about — discovery's own
            # conflict path decides.
        # "authority": unversioned — read-back is the write-path proof.
    return None


@dataclass(frozen=True)
class _ReplayOp:
    """The write side of a journaled operation, rebuilt from the journal
    snapshot — NOT a fresh `Operation`: no actor (authority was validated at
    first commitment; the journaled effect is the warrant for finishing it),
    and no re-admission, so replay cannot be refused by a check the original
    commitment already passed."""

    operation_id: str
    kind: str
    person_id: str | None
    scope: Scope
    payload: object


def _rebuild(record: OperationRecord) -> _ReplayOp | None:
    """Reconstruct the effect from the journal snapshot; `None` when the
    snapshot cannot describe a known kind."""
    snap = dict(record.snapshot)
    kind = snap.get("kind")
    if "target_scope" not in snap:
        return None
    try:
        scope = _scope_from(snap["target_scope"])
        payload: object
        if kind == "set_membership":
            payload = MembershipStanding(member=bool(snap["member"]))
        elif kind in ("grant_access", "revoke_access"):
            payload = AccessChange(
                direction=AccessDirection(snap["direction"]),
                grantee=snap["grantee"],
                permits=frozenset(snap["permits"]),
            )
        elif kind in ("delegate", "revoke_delegation"):
            payload = Delegation(
                delegatee=snap["delegatee"],
                permits=frozenset(snap["permits"]),
            )
        elif kind == "sync_membership":
            payload = RosterSync(
                members=frozenset(
                    _ident_from(i) for i in snap.get("members", [])
                ),
                complete=bool(snap["complete"]),
            )
        else:
            return None
        return _ReplayOp(
            operation_id=record.operation_id,
            kind=str(kind),
            person_id=snap.get("target_person"),
            scope=scope,
            payload=payload,
        )
    except (KeyError, ValueError, TypeError):
        return None


class ManagedServices:
    """The assembled managed-backend graph — what `nora.people.wire` hands
    composition and what tests assemble directly."""

    def __init__(
        self,
        bindings: BindingStore,
        backend: AuthorityBackend,
        topology: TopologySource,
        registry: IdentityRegistry,
    ) -> None:
        self.bindings = bindings
        self.backend = backend
        self.topology = topology
        self.registry = registry
        self.registration = RegistrationService(bindings, registry)
        self.authority = AuthorityService(bindings, backend, topology)

    async def aclose(self) -> None:
        await self.bindings.aclose()
        await self.backend.aclose()
        await self.registry.aclose()


class RegistrationService:
    """Discovery, registration, lifecycle and unlink — the binding-half
    operations (`identity.md`)."""

    def __init__(self, bindings: BindingStore, registry: IdentityRegistry) -> None:
        self._bindings = bindings
        self._registry = registry

    async def apply(self, op: Operation) -> Outcome:
        try:
            return await self._apply(op)
        except IdentifierConflictError:
            # A concurrent claim landed between the pre-flight read and the
            # write — the store's unique ownership is the authority, and a
            # conflict is a denial, not a failure.
            return denied("identifier-conflict")
        except OperationConflictError:
            # Two first-commitments raced one operation_id and the journal
            # recorded the other effect.
            return denied("operation-conflict")
        except ManagedError:
            raise  # a violated invariant is a bug, not an outage
        except Exception:
            log.exception("binding store failed")
            return unavailable("store-failure")

    async def _apply(self, op: Operation) -> Outcome:
        stale = await _revisions_fresh(self._bindings, op)
        if stale is not None:
            return stale
        replay = await _recorded_replay(self._bindings, op)
        if replay is not None:
            return replay
        handler = {
            "observe_account": self._observe,
            "register_account": self._register,
            "suspend_identity": self._suspend,
            "reinstate_identity": self._reinstate,
            "unlink_account": self._unlink,
        }.get(op.kind)
        if handler is None:
            return denied(_REFUSED)
        return await handler(op)

    # -- observe / register -------------------------------------------------

    async def _observe(self, op: Operation) -> Outcome:
        payload = cast(AccountObservation, op.payload)
        # The flat lookup first: an identifier that already resolves is a
        # replayed observation, not a new person.
        if op.target.account is not None:
            existing = await self._bindings.account_for(
                op.target.account.platform,
                op.target.account.kind,
                op.target.account.value,
            )
            if existing is not None:
                return complete("already-observed")
        person_id = f"person_{uuid4().hex[:20]}"
        account = ManagedAccount(
            account_id=str(uuid4()),
            person_id=person_id,
            platform=next(iter(payload.identifiers)).platform,
            identifiers=payload.identifiers,
            state=AccountState.BOUND,
        )
        person = ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE)
        return await self._register_effects(
            op, ("binding", "kratos", "correlation"),
            person=person, account=account, membership=None,
        )

    async def _register(self, op: Operation) -> Outcome:
        payload = cast(AccountRegistration, op.payload)
        existing_person = await self._bindings.person(payload.person_id)
        if existing_person is not None:
            # Registration never attaches an account to an EXISTING person
            # — only the bilateral link can (management.md «Registration
            # of an observed account is not permission to claim it for an
            # existing person»). The one pass is an exact replay under a
            # different operation id: every presented identifier already
            # resolves to the same bound account of that person, so the
            # effect adds and widens nothing.
            if await self._already_owned(payload) is not None:
                return complete(existing_person.person_id)
            if existing_person.lifecycle is PersonLifecycle.SUSPENDED:
                return denied("person-suspended")
            return denied("person-exists")
        person = ManagedPerson(
            person_id=payload.person_id, lifecycle=PersonLifecycle.ACTIVE
        )
        account = ManagedAccount(
            account_id=str(uuid4()),
            person_id=person.person_id,
            platform=next(iter(payload.identifiers)).platform,
            identifiers=payload.identifiers,
            state=AccountState.BOUND,
        )
        membership = ManagedMembership(
            person_id=person.person_id,
            scope=payload.scope,
            state=MembershipState.MEMBER,
            source="registration",
        )
        return await self._register_effects(
            op, ("binding", "membership", "kratos", "correlation"),
            person=person, account=account, membership=membership,
        )

    async def _already_owned(
        self, payload: AccountRegistration
    ) -> ManagedAccount | None:
        """The bound account this payload exactly re-describes — every
        identifier resolves to ONE account of the named person. An
        unbound identifier would widen ownership, and identifiers split
        across accounts (or persons) are the store's conflict case, so
        both refuse."""
        owned: ManagedAccount | None = None
        for ident in payload.identifiers:
            record = await self._bindings.record_for(account=ident)
            if record is None or record.person_id != payload.person_id:
                return None
            if owned is not None and record.account_id != owned.account_id:
                return None
            owned = record
        return owned

    async def _register_effects(
        self,
        op: Operation,
        steps: tuple[str, ...],
        *,
        person: ManagedPerson,
        account: ManagedAccount,
        membership: ManagedMembership | None,
    ) -> Outcome:
        record = await _record(self._bindings, op, steps)
        if isinstance(record, Outcome):
            return record
        outcome = await self._run_binding(
            record, "binding",
            lambda: self._bindings.register(
                person=person, account=account, membership=membership
            ),
        )
        if outcome.status is OutcomeStatus.COMPLETE and membership is not None:
            outcome = await self._run_binding(
                record, "membership",
                lambda: self._bindings.set_membership(
                    person_id=membership.person_id, scope=membership.scope,
                    member=True, source=membership.source,
                ),
            )
        if outcome.status is OutcomeStatus.COMPLETE and "kratos" in steps:
            outcome, identity_id = await self._registry.ensure_identity(
                # The person id is `external_id`, never a trait — the
                # nora_person schema carries descriptive fields only and
                # holds no ownership evidence.
                external_id=person.person_id,
                traits={"display_name": person.display_name},
            )
            if outcome.status is OutcomeStatus.COMPLETE and identity_id:
                await self._mark(record, "kratos")
                outcome = await self._run_binding(
                    record, "correlation", lambda: self._bindings.correlate_kratos(
                        person_id=person.person_id, kratos_identity_id=identity_id,
                    )
                )
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete", person.person_id)
        return complete(person.person_id)

    async def _run_binding(
        self, record: OperationRecord, step: str, effect: Effect
    ) -> Outcome:
        """Run one journaled store effect — or skip it when the journal
        already marks it done (replay)."""
        if not _pending(record, step):
            return complete()
        outcome = await effect()
        if outcome.status is OutcomeStatus.COMPLETE:
            await self._mark(record, step)
        return outcome

    async def _mark(self, record: OperationRecord, step: str) -> None:
        await _step_done(self._bindings, record, step)

    # -- lifecycle ------------------------------------------------------------

    async def _suspend(self, op: Operation) -> Outcome:
        return await self._lifecycle(op, PersonLifecycle.SUSPENDED)

    async def _reinstate(self, op: Operation) -> Outcome:
        return await self._lifecycle(op, PersonLifecycle.ACTIVE)

    async def _lifecycle(self, op: Operation, to: PersonLifecycle) -> Outcome:
        person = await self._bindings.person(op.target.person_id or "")
        if person is None:
            return denied("unknown-person")
        record = await _record(
            self._bindings, op, ("binding", "kratos", "sessions")
        )
        if isinstance(record, Outcome):
            return record
        outcome = await self._run_binding(
            record, "binding",
            lambda: self._bindings.set_lifecycle(
                person_id=person.person_id, lifecycle=to
            ),
        )
        if outcome.status is OutcomeStatus.COMPLETE and person.kratos_identity_id:
            # The Kratos identity mirrors the hold: suspension gates real
            # session checks (state=inactive) and ends derived sessions;
            # reinstatement only flips state back.
            outcome = await self._run_binding(
                record, "kratos",
                lambda: self._registry.set_state(
                    external_id=person.person_id,
                    active=to is PersonLifecycle.ACTIVE,
                ),
            )
            if outcome.status is OutcomeStatus.COMPLETE and to is PersonLifecycle.SUSPENDED:
                outcome = await self._run_binding(
                    record, "sessions",
                    lambda: self._registry.drop_sessions(external_id=person.person_id),
                )
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()

    # -- unlink -----------------------------------------------------------------

    async def _unlink(self, op: Operation) -> Outcome:
        payload = cast(Unlink, op.payload)
        if op.target.account is None:
            raise ManagedError("unlink_account carries no account target")
        bound = await self._bindings.account_for(
            op.target.account.platform,
            op.target.account.kind,
            op.target.account.value,
        )
        if bound is None or bound.person_id != payload.expected_person:
            return denied("binding-mismatch")
        record = await _record(self._bindings, op, ("binding",))
        if isinstance(record, Outcome):
            return record
        outcome = await self._run_binding(
            record, "binding",
            lambda: self._bindings.revoke_binding(account=op.target.account),
        )
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()


class AuthorityService:
    """Scoped membership, grants and delegation plus the authoritative
    `check` (`authorization.md`)."""

    def __init__(
        self,
        bindings: BindingStore,
        backend: AuthorityBackend,
        topology: TopologySource,
    ) -> None:
        self._bindings = bindings
        self._backend = backend
        self._topology = topology

    async def check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        """The one authoritative check path — revocation-first blocks, real
        topology, live person state, then the grant engine. Nothing here
        consults a snapshot or falls back to a file; any failure to answer
        is `unavailable`, never a pass."""
        try:
            return await self._check(scope=scope, person_id=person_id, permit=permit)
        except Exception:
            log.exception("authority check failed")
            return check_unavailable("store-failure")

    async def _check(
        self, *, scope: Scope, person_id: str, permit: str
    ) -> CheckResult:
        for block in await self._bindings.authority_blocks():
            if block.person_id == person_id and block.scope == scope:
                return check_unavailable("authority-blocked")
        if not await self._topology.scope_exists(scope):
            return unknown_scope("scope-not-in-topology")
        lifecycle = await self._bindings.person_lifecycle(person_id)
        if lifecycle is None:
            return check_denied("unknown-person")
        if lifecycle is PersonLifecycle.SUSPENDED:
            return check_denied("person-suspended")
        return await self._backend.check(
            scope=scope, person_id=person_id, permit=permit
        )

    async def apply(self, op: Operation) -> Outcome:
        try:
            return await self._apply(op)
        except OperationConflictError:
            # Two first-commitments raced one operation_id and the journal
            # recorded the other effect.
            return denied("operation-conflict")
        except ManagedError:
            raise  # a violated invariant is a bug, not an outage
        except Exception:
            log.exception("authority store failed")
            return unavailable("store-failure")

    async def _apply(self, op: Operation) -> Outcome:
        stale = await _revisions_fresh(self._bindings, op)
        if stale is not None:
            return stale
        replay = await _recorded_replay(self._bindings, op)
        if replay is not None:
            return replay
        scope = op.target.scope
        if scope is None or not await self._topology.scope_exists(scope):
            return denied("unknown-scope")
        refuse = await self._actor_allowed(op, scope)
        if refuse is not None:
            return refuse
        if op.kind == "set_membership":
            lifecycle = await self._bindings.person_lifecycle(
                op.target.person_id or ""
            )
            if lifecycle is not PersonLifecycle.ACTIVE:
                return denied("target-person-inactive")
        return await self._run(op, None)

    async def apply_replay(self, record: OperationRecord) -> Outcome:
        """Resume a journaled operation's pending steps under its existing
        row — no fresh journal, no actor revalidation (the actor was checked
        at first commitment; a revoked actor's journaled intent completing
        is the design's revocation-first story)."""
        try:
            return await self._replay(record)
        except ManagedError:
            raise
        except Exception:
            log.exception("authority replay failed")
            return unavailable("store-failure")

    async def _replay(self, record: OperationRecord) -> Outcome:
        outcome = await self._run(None, record)
        if outcome.status is OutcomeStatus.COMPLETE:
            # The operation's own blocks clear — whoever it blocked (the
            # revoked grantee, every unconfirmed departure) is covered by
            # the read-back the replay just passed.
            for block in await self._bindings.authority_blocks():
                if block.operation_id == record.operation_id:
                    await self._bindings.clear_block(
                        person_id=block.person_id, scope=block.scope
                    )
        return outcome

    # -- shared validation ----------------------------------------------------

    async def _actor_allowed(self, op: Operation, scope: Scope) -> Outcome | None:
        """Revalidate the actor's standing at commitment. `None` means the
        actor may proceed; an `Outcome` is the refusal to return."""
        actor = op.actor
        if actor.kind is ActorKind.INSTALLATION:
            return None  # established out of band; the boundary proved it
        if actor.kind is ActorKind.SERVICE:
            return None  # approved sources were enforced at construction
        if actor.person_id is None:
            return denied("actor-carries-no-person")
        lifecycle = await self._bindings.person_lifecycle(actor.person_id)
        if lifecycle is not PersonLifecycle.ACTIVE:
            return denied("actor-suspended")
        if actor.kind is ActorKind.OPERATOR:
            permit = _REQUIRED_PERMIT[op.kind]
            verdict = await self._backend.check(
                scope=scope, person_id=actor.person_id, permit=permit
            )
            if verdict.verdict is Verdict.UNAVAILABLE:
                # The actor's standing could not be read — that is an
                # outage, not an authoritative refusal.
                return unavailable("actor-check-unavailable")
            if not verdict.allowed:
                return denied(
                    f"actor-lacks-{permit}:{verdict.detail or verdict.verdict.value}"
                )
        elif actor.kind is ActorKind.HOLDER:
            account = await self._bindings.account_for(
                actor.account.platform,
                actor.account.kind,
                actor.account.value,
            ) if actor.account else None
            if account is None or account.person_id != actor.person_id:
                return denied("holder-binding-absent")
        return None

    async def _publish_containment(self, scope: Scope) -> Outcome:
        """Walk the REAL parents from topology and publish each link — a
        caller's claim about containment is never written."""
        cursor = scope
        while True:
            parent = await self._topology.parent_of(cursor)
            if parent is None:
                return complete()
            outcome = await self._backend.publish_containment(
                scope=cursor, parent=parent
            )
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
            cursor = parent

    async def _readback(
        self, scope: Scope, want: set[tuple[str, str]], unwant: set[tuple[str, str]]
    ) -> Outcome:
        rows = await self._backend.relations_on(scope=scope)
        if isinstance(rows, CheckResult):
            return unavailable(rows.detail)
        have = set(rows)
        if not want <= have or unwant & have:
            return unavailable("read-back-mismatch")
        return complete()

    # -- the effect runner -----------------------------------------------------

    async def _run(
        self, op: Operation | None, replay: OperationRecord | None
    ) -> Outcome:
        """Apply one authority operation's journaled effects.

        First pass (`op` set): validate the target person, journal the op,
        then run. Replay (`replay` set): rebuild the effect from the
        journal snapshot and run its pending steps — nothing re-validates,
        nothing re-journals.
        """
        if replay is not None:
            effect = _rebuild(replay)
            if effect is None:
                return unavailable("journal-unreplayable")
            record = replay
        else:
            if op is None or op.target.scope is None:
                raise ManagedError("an authority operation names a scope target")
            recorded = await _record(self._bindings, op, self._steps_for(op))
            if isinstance(recorded, Outcome):
                return recorded
            record = recorded
            effect = _ReplayOp(
                operation_id=op.operation_id,
                kind=op.kind,
                person_id=op.target.person_id,
                scope=op.target.scope,
                payload=op.payload,
            )
        scope = effect.scope
        outcome = await self._run_step(
            record, "containment", lambda: self._publish_containment(scope)
        )
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._effects(effect, record, scope)
        if outcome.status is OutcomeStatus.COMPLETE:
            want, unwant = self._readback_terms(effect)
            outcome = await self._run_step(
                record, "readback", lambda: self._readback(scope, want, unwant)
            )
        if outcome.status is not OutcomeStatus.COMPLETE:
            person_id = self._affected_person(effect)
            # Revocation-first: a remove-side effect that could not confirm
            # raises a durable block so the stale grant stops answering NOW.
            if person_id is not None:
                await self._bindings.block_authority(
                    AuthorityBlock(
                        person_id=person_id, scope=scope,
                        reason=f"{effect.kind}-unconfirmed",
                        operation_id=effect.operation_id,
                    )
                )
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()

    async def _run_step(
        self, record: OperationRecord, step: str, effect: Effect
    ) -> Outcome:
        if not _pending(record, step):
            return complete()
        outcome = await effect()
        if outcome.status is OutcomeStatus.COMPLETE:
            await _step_done(self._bindings, record, step)
        return outcome

    async def _run_batch(
        self, record: OperationRecord, step: str, effects: Effects
    ) -> Outcome:
        """One journaled step made of several idempotent store calls.
        Every call replays cleanly, so the step marks done only after ALL
        of them — marking per call would silently skip the rest on both
        the first pass and every replay."""
        if not _pending(record, step):
            return complete()
        for effect in effects():
            outcome = await effect
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
        await _step_done(self._bindings, record, step)
        return complete()

    def _steps_for(self, op: Operation) -> tuple[str, ...]:
        if op.kind in ("set_membership", "sync_membership"):
            return ("containment", "authority", "binding", "readback")
        return ("containment", "authority", "readback")

    def _affected_person(self, effect: _ReplayOp) -> str | None:
        """Who a failed REVOKE must be blocked for — grants that fail block
        nobody (nothing stale was left), removes block the affected person."""
        payload = effect.payload
        if effect.kind == "set_membership":
            return (
                effect.person_id
                if isinstance(payload, MembershipStanding) and not payload.member
                else None
            )
        if effect.kind == "revoke_access" and isinstance(payload, AccessChange):
            return payload.grantee
        if effect.kind == "revoke_delegation" and isinstance(payload, Delegation):
            return payload.delegatee
        if effect.kind == "sync_membership":
            # A departure set may be mixed; block the affected person the
            # op names when the remove half failed — the joined gain no
            # stale authority either way.
            return effect.person_id
        return None

    async def _effects(
        self, effect: _ReplayOp, record: OperationRecord, scope: Scope
    ) -> Outcome:
        """The `authority` + `binding` steps for each kind."""
        payload = effect.payload
        kind = effect.kind
        if kind == "set_membership":
            if not isinstance(payload, MembershipStanding):
                raise ManagedError(f"{kind} payload is {type(payload).__name__}")
            person_id = effect.person_id or ""
            outcome = await self._run_step(
                record, "authority",
                lambda: (
                    self._backend.put_relation(
                        scope=scope, relation="member", person_id=person_id
                    )
                    if payload.member
                    else self._backend.drop_relation(
                        scope=scope, relation="member", person_id=person_id
                    )
                ),
            )
            if outcome.status is not OutcomeStatus.COMPLETE:
                return outcome
            return await self._run_step(
                record, "binding",
                lambda: self._bindings.set_membership(
                    person_id=person_id, scope=scope,
                    member=payload.member, source="delegation",
                ),
            )
        if kind in ("grant_access", "revoke_access"):
            if not isinstance(payload, AccessChange):
                raise ManagedError(f"{kind} payload is {type(payload).__name__}")
            direction = (
                "grant" if payload.direction is AccessDirection.GRANT else "deny"
            )
            return await self._run_batch(
                record, "authority",
                lambda: (
                    self._backend.put_relation(
                        scope=scope,
                        relation=relation_for(direction, permit),
                        person_id=payload.grantee,
                    )
                    if kind == "grant_access"
                    else self._backend.drop_relation(
                        scope=scope,
                        relation=relation_for(direction, permit),
                        person_id=payload.grantee,
                    )
                    for permit in sorted(payload.permits)
                ),
            )
        if kind in ("delegate", "revoke_delegation"):
            if not isinstance(payload, Delegation):
                raise ManagedError(f"{kind} payload is {type(payload).__name__}")
            return await self._run_batch(
                record, "authority",
                lambda: (
                    self._backend.put_relation(
                        scope=scope, relation=relation_for("grant", permit),
                        person_id=payload.delegatee,
                    )
                    if kind == "delegate"
                    else self._backend.drop_relation(
                        scope=scope, relation=relation_for("grant", permit),
                        person_id=payload.delegatee,
                    )
                    for permit in sorted(payload.permits)
                ),
            )
        # sync_membership — removes BEFORE adds, so a partial application
        # owes an addition, never leaves a stale member tuple unaccounted.
        if not isinstance(payload, RosterSync):
            raise ManagedError(f"{kind} payload is {type(payload).__name__}")
        joined: set[str] = set()
        for ident in payload.members:
            account = await self._bindings.account_for(
                ident.platform, ident.kind, ident.value
            )
            if account is not None:
                joined.add(account.person_id)
        departed: set[str] = set()
        if payload.complete:
            departed = {
                m.person_id
                for m in await self._bindings.members_of(scope)
                if m.state is MembershipState.MEMBER and m.source == "sync"
            } - joined
        outcome = await self._run_batch(
            record, "authority",
            lambda: [
                *(
                    self._backend.drop_relation(
                        scope=scope, relation="member", person_id=p
                    )
                    for p in sorted(departed)
                ),
                *(
                    self._backend.put_relation(
                        scope=scope, relation="member", person_id=p
                    )
                    for p in sorted(joined)
                ),
            ],
        )
        if outcome.status is not OutcomeStatus.COMPLETE:
            # Revocation-first on the roster path too: every departed
            # member whose tuple could not confirm is blocked until
            # reconciliation clears them — a stale `member` grants
            # real speak+view on this room.
            for blocked in sorted(departed):
                await self._bindings.block_authority(
                    AuthorityBlock(
                        person_id=blocked, scope=scope,
                        reason="sync-departure-unconfirmed",
                        operation_id=effect.operation_id,
                    )
                )
            return outcome
        return await self._run_batch(
            record, "binding",
            lambda: [
                *(
                    self._bindings.set_membership(
                        person_id=p, scope=scope, member=False, source="sync"
                    )
                    for p in sorted(departed)
                ),
                *(
                    self._bindings.set_membership(
                        person_id=p, scope=scope, member=True, source="sync"
                    )
                    for p in sorted(joined)
                ),
            ],
        )

    def _readback_terms(
        self, effect: _ReplayOp
    ) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        """The (relation, person) pairs that must be present (`want`) and
        must be absent (`unwant`) after the effect."""
        payload = effect.payload
        kind = effect.kind
        if kind == "set_membership":
            pair = ("member", effect.person_id or "")
            if not isinstance(payload, MembershipStanding):
                raise ManagedError(f"{kind} payload is {type(payload).__name__}")
            return ({pair} if payload.member else set(),
                    set() if payload.member else {pair})
        if kind in ("grant_access", "revoke_access"):
            if not isinstance(payload, AccessChange):
                raise ManagedError(f"{kind} payload is {type(payload).__name__}")
            direction = (
                "grant" if payload.direction is AccessDirection.GRANT else "deny"
            )
            pairs = {(relation_for(direction, p), payload.grantee)
                     for p in payload.permits}
            return (pairs if kind == "grant_access" else set(),
                    set() if kind == "grant_access" else pairs)
        if kind in ("delegate", "revoke_delegation"):
            if not isinstance(payload, Delegation):
                raise ManagedError(f"{kind} payload is {type(payload).__name__}")
            pairs = {(relation_for("grant", p), payload.delegatee)
                     for p in payload.permits}
            return (pairs if kind == "delegate" else set(),
                    set() if kind == "delegate" else pairs)
        # sync_membership's readback is membership-presence for joined;
        # departures on an incomplete roster are never written, so nothing
        # is required absent there.
        return (set(), set())

    # -- reconciliation -----------------------------------------------------------

    async def reconcile(self) -> tuple[OperationRecord, ...]:
        """Resume every journaled-but-unfinished operation — restart recovery
        and outage recovery are the same path: replay the pending steps,
        re-read back, then clear the blocks the operation raised."""
        finished: list[OperationRecord] = []
        for record in await self._bindings.incomplete_operations():
            if record.kind not in _AUTHORITY_KINDS:
                continue
            outcome = await self.apply_replay(record)
            if outcome.status is OutcomeStatus.COMPLETE:
                finished.append(record)
        return tuple(finished)
