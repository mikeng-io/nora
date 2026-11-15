"""Scoped authority: membership, grants, delegation, `check` and
reconciliation (`design/managed-identity-access/authorization.md`).

`AuthorityService.check` is the one authoritative check path — revocation-
first blocks, real topology, live person state, then the grant engine;
nothing consults a snapshot or falls back to a file, so a failure to answer
is `unavailable`, never a pass. Mutations journal their effects before
applying, and a revocation step that cannot confirm raises an
`AuthorityBlock` on the affected (person, scope): the stale grant must not
keep working while a store is down.
"""

from __future__ import annotations

import logging
from types import MappingProxyType

from nora.domain.managed import (
    AuthorityBlock,
    CheckResult,
    ManagedError,
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
    relation_for,
    unavailable,
    unknown_scope,
)
from nora.domain.managed_ports import AuthorityBackend, BindingStore, TopologySource
from nora.domain.management import (
    AccessChange,
    AccessDirection,
    ActorKind,
    Delegation,
    MembershipStanding,
    Operation,
    RosterSync,
    Scope,
)
from nora.people.app.managed_ops import (
    Effect,
    Effects,
    _finish,
    _pending,
    _rebuild,
    _record,
    _recorded_replay,
    _ReplayOp,
    _revisions_fresh,
    _step_done,
)

log = logging.getLogger("nora.people.managed")

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
        """`None` when the actor's standing holds at commitment; an `Outcome` is the refusal."""
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
        """Publish the real topology parents — a caller's containment claim is never written."""
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
        """Apply a journaled authority effect — first pass journals, replay rebuilds and resumes."""
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
        """One journaled step of several idempotent calls — done only after ALL apply."""
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
        """Who a failed remove-side effect must block — grants that fail block nobody."""
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
        kind = effect.kind
        if kind == "set_membership":
            return await self._membership_effects(effect, record, scope)
        if kind in ("grant_access", "revoke_access"):
            return await self._access_effects(effect, record, scope)
        if kind in ("delegate", "revoke_delegation"):
            return await self._delegation_effects(effect, record, scope)
        return await self._sync_effects(effect, record, scope)

    async def _membership_effects(
        self, effect: _ReplayOp, record: OperationRecord, scope: Scope
    ) -> Outcome:
        """set_membership's relation write, then its binding row."""
        payload = effect.payload
        kind = effect.kind
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

    async def _access_effects(
        self, effect: _ReplayOp, record: OperationRecord, scope: Scope
    ) -> Outcome:
        """grant_access/revoke_access: one relation call per permit."""
        payload = effect.payload
        kind = effect.kind
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

    async def _delegation_effects(
        self, effect: _ReplayOp, record: OperationRecord, scope: Scope
    ) -> Outcome:
        """delegate/revoke_delegation: one relation call per permit."""
        payload = effect.payload
        kind = effect.kind
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

    async def _sync_effects(
        self, effect: _ReplayOp, record: OperationRecord, scope: Scope
    ) -> Outcome:
        """sync_membership's removes BEFORE adds, then the binding rows."""
        payload = effect.payload
        kind = effect.kind
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
            # Revocation-first here too: a departed member whose drop could
            # not confirm is blocked until reconciliation clears it — a
            # stale `member` still grants real speak+view.
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
        """The (relation, person) pairs that must be present and absent after the effect."""
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
