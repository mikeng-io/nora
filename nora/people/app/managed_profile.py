"""The holder's global display-name update (`design/managed-identity-access/management.md`).

Kratos owns the authoritative descriptive profile; the SQL
`managed_person.display_name` row is the projection every consumer reads.
The operation therefore writes in that order — authoritative trait first,
projection second, read-back last — inside the same journaled
retry/restart model every managed mutation uses:

    journal -> kratos patch -> sql projection -> read-back -> complete

A crash between the Kratos patch and the projection leaves an
`incomplete` journal `reconcile` resumes. A replay NEVER writes the
journaled name over a newer intent: the journal's order is the intents'
order, so an op that is no longer the newest `update_profile` for its
person closes `superseded` without touching the trait or the
projection; and before projecting, the step re-reads the authoritative
trait and refuses (`denied`, journaled as such) when it no longer
carries this operation's name.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from nora.domain.authentication import AccountAuthentication, AuthenticatedPrincipal
from nora.domain.managed import (
    ManagedError,
    OperationConflictError,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
    complete,
    denied,
    unavailable,
)
from nora.domain.managed_ports import (
    AuthorityBackend,
    BindingStore,
    IdentityRegistry,
    TopologySource,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    Operation,
    OperationError,
    ProfileEdit,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.people.app.authentication import AccountSessions
from nora.people.app.link_publication import LinkPublicationService
from nora.people.app.managed_ops import (
    _finish,
    _pending,
    _publication_refusal,
    _record,
    _recorded_replay,
    _revisions_fresh,
    _step_done,
)

log = logging.getLogger("nora.people.managed")

_REFUSED = "operation not wired in this slice"
_PROFILE_KIND = "update_profile"
_PROFILE_STEPS = ("kratos", "projection", "readback")


class ProfileService:
    """`update_profile` for managed holders — the global display-name edit.

    Owns no state of its own: the session check derives the exact actor,
    the operation journal owns the retry/restart contract, Kratos owns
    the authoritative trait and the binding store owns the projection.
    Scoped nicknames are an operator capability outside this slice —
    their payload is refused, never silently re-routed.
    """

    def __init__(
        self,
        bindings: BindingStore,
        registry: IdentityRegistry,
        publication: LinkPublicationService,
    ) -> None:
        self._bindings = bindings
        self._registry = registry
        self._sessions = AccountSessions(bindings, registry)
        self._publication = publication

    # -- the authenticated entry point --------------------------------------

    async def update_display_name(
        self,
        authentication: AccountAuthentication,
        *,
        operation_id: str,
        display_name: str,
    ) -> Outcome:
        """Rename the caller's own canonical person — actor AND target both
        come from the verified session binding; a body can name neither."""
        checked = await self._principal(authentication)
        if isinstance(checked, Outcome):
            return checked
        binding = checked.binding
        if not display_name.strip():
            return denied("display-name-required")
        try:
            authority = await self._publication.authority_revision(binding.person_id)
        except Exception:  # noqa: BLE001 — an unreadable authority is unavailable
            authority = None
        if authority is None:
            return unavailable("authority-unavailable")
        try:
            operation = Operation(
                operation_id=operation_id, kind=_PROFILE_KIND,
                actor=Actor(
                    ActorKind.HOLDER, person_id=binding.person_id,
                    account=authentication.account,
                    proof_issuer=authentication.proof_issuer),
                target=Target(TargetKind.PERSON, person_id=binding.person_id),
                payload=ProfileEdit(display_name=display_name),
                observed_revisions={
                    "identity": binding.person_revision,
                    "authority": authority,
                })
        except OperationError as error:
            return denied(str(error))
        return await self.apply(operation, authentication)

    async def _principal(
        self, authentication: AccountAuthentication
    ) -> AuthenticatedPrincipal | Outcome:
        """Revalidate the retained binding — unverifiable sessions never reach the operation."""
        result = await self._sessions.check(authentication)
        if result.verdict is Verdict.UNAVAILABLE:
            return unavailable("authentication-unavailable")
        if result.principal is None:
            return denied("authentication-refused")
        return result.principal

    # -- the journaled operation --------------------------------------------

    async def apply(self, op: Operation, authentication: AccountAuthentication) -> Outcome:
        try:
            async with self._bindings.mutation():
                # The gate is already held; check() would reacquire it for a fresh sign-in.
                verified = await self._sessions._check(authentication)
                if verified.verdict is Verdict.UNAVAILABLE:
                    return unavailable("authentication-unavailable")
                checked = verified.principal
                if checked is None:
                    return denied("authentication-refused")
                if (op.actor.account != authentication.account
                        or op.actor.person_id != checked.binding.person_id
                        or op.target.person_id != checked.binding.person_id):
                    return denied("authentication-changed")
                return await self._apply(op)
        except OperationConflictError:
            return denied("operation-conflict")
        except ManagedError:
            raise
        except Exception:
            log.exception("profile update failed")
            return unavailable("store-failure")

    async def _apply(self, op: Operation) -> Outcome:
        if op.kind != _PROFILE_KIND or op.actor.kind is not ActorKind.HOLDER:
            return denied(_REFUSED)
        refusal = await _publication_refusal(
            self._bindings,
            people=(op.actor.person_id, op.target.person_id),
            payload=op.payload,
        )
        if refusal is not None:
            return refusal
        stale = await _revisions_fresh(self._bindings, op)
        if stale is not None:
            return stale
        replay = await _recorded_replay(self._bindings, op)
        if replay is not None:
            return replay
        payload = cast(ProfileEdit, op.payload)
        if payload.scope is not None or payload.display_name is None:
            # The operator's scoped nickname is a separate pending
            # capability — refused here, never applied as a global edit.
            return denied(_REFUSED)
        person_id = cast(str, op.target.person_id)
        person = await self._bindings.person(person_id)
        if person is None:
            return denied("unknown-person")
        if person.lifecycle is not PersonLifecycle.ACTIVE:
            return denied("person-suspended")
        record = await _record(self._bindings, op, _PROFILE_STEPS)
        if isinstance(record, Outcome):
            return record
        return await self._run_steps(record)

    async def _step(
        self, record: OperationRecord, step: str, effect: Any
    ) -> Outcome:
        if not _pending(record, step):
            return complete()
        outcome = await effect()
        if outcome.status is OutcomeStatus.COMPLETE:
            await _step_done(self._bindings, record, step)
        return outcome

    async def _run_steps(self, record: OperationRecord) -> Outcome:
        """kratos -> projection -> read-back, skipping journaled-done steps."""
        snapshot = dict(record.snapshot)
        person_id = snapshot["target_person"]
        display_name = snapshot["display_name"]
        newest = await self._bindings.latest_profile_operation(person_id)
        if newest is not None and newest.operation_id != record.operation_id:
            # The journal's order is the intents' order: a later journaled
            # edit owns the name, so this replay writes NOTHING over it —
            # not the trait, not the projection.
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", "superseded")
            return denied("superseded")
        outcome = await self._step(
            record, "kratos",
            lambda: self._registry.set_display_name(
                external_id=person_id, display_name=display_name),
        )
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "projection",
                lambda: self._project(person_id, display_name))
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "readback",
                lambda: self._readback(person_id, display_name))
        if outcome.status is OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "complete")
            return complete()
        if outcome.status is OutcomeStatus.DENIED:
            # An authoritative refusal is terminal — the journaled effect
            # can never apply (absent person, superseded name), so the
            # journal closes denied instead of retrying forever.
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                      outcome.status.value, outcome.detail)
        return outcome

    async def _project(self, person_id: str, display_name: str) -> Outcome:
        """Write the projection only while Kratos still carries THIS op's name."""
        canonical = await self._bindings.canonical_person(person_id)
        if canonical is None:
            return denied("absent-person")
        person = await self._bindings.person(canonical)
        if person is None:
            return denied("absent-person")
        if person.display_name == display_name:
            return complete()
        outcome, traits = await self._registry.identity_traits(
            external_id=canonical)
        if traits is None:
            return outcome
        if traits.get("display_name") != display_name:
            return denied("superseded")
        return await self._bindings.set_display_name(
            person_id=canonical, display_name=display_name)

    async def _readback(self, person_id: str, display_name: str) -> Outcome:
        """Success only when the projected row reads the intended name."""
        canonical = await self._bindings.canonical_person(person_id)
        person = (
            None if canonical is None
            else await self._bindings.person(canonical)
        )
        if person is not None and person.display_name == display_name:
            return complete()
        return unavailable("read-back-mismatch")

    # -- reconciliation --------------------------------------------------------

    async def reconcile(self) -> tuple[OperationRecord, ...]:
        """Restart recovery — resume every journaled `update_profile` the
        journal still owes. Other kinds are other services' journals."""
        async with self._bindings.mutation():
            return await self._reconcile()

    async def _reconcile(self) -> tuple[OperationRecord, ...]:
        finished: list[OperationRecord] = []
        for record in await self._bindings.incomplete_operations():
            if (record.kind != _PROFILE_KIND
                    or "profile_scope" in record.snapshot):
                # Scoped nicknames are `ScopedProfileService`'s journal —
                # a global replay must never pick them up.
                continue
            try:
                outcome = await self._run_steps(record)
            except ManagedError:
                raise
            except Exception:
                log.exception("profile replay failed")
                outcome = unavailable("store-failure")
            if outcome.status is OutcomeStatus.COMPLETE:
                current = await self._bindings.operation(record.operation_id)
                if (current is not None
                        and current.state is OperationJournalState.COMPLETE):
                    finished.append(current)
        return tuple(finished)


_SCOPED_STEPS = ("profile", "readback")


class ScopedProfileService:
    """`update_profile` for scoped operators — the (person, scope)
    nickname (`authorization.md` «Scoped authority»).

    Same kind and journaled retry/restart model as the holder's global
    rename, but the effect is a Nora-owned metadata row under the
    canonical person and the EXACT scope — never the Kratos trait, never
    an identifier, grant or ownership proof. `latest_profile_operation`'s
    scope partition keeps a local nickname independent of the global
    name and of every sibling scope: neither can supersede or replay-
    overwrite the other.
    """

    def __init__(
        self,
        bindings: BindingStore,
        backend: AuthorityBackend,
        topology: TopologySource,
    ) -> None:
        self._bindings = bindings
        self._backend = backend
        self._topology = topology

    async def apply(self, op: Operation) -> Outcome:
        try:
            async with self._bindings.mutation():
                return await self._apply(op)
        except OperationConflictError:
            # Two first-commitments raced one operation_id and the journal
            # recorded the other effect.
            return denied("operation-conflict")
        except ManagedError:
            raise
        except Exception:
            log.exception("scoped profile update failed")
            return unavailable("store-failure")

    async def _apply(self, op: Operation) -> Outcome:
        """Inside the caller's mutation gate — actor, binding and permit revalidated."""
        payload = op.payload
        if (op.kind != _PROFILE_KIND
                or op.actor.kind is not ActorKind.OPERATOR
                or not isinstance(payload, ProfileEdit)
                or payload.scope is None):
            return denied(_REFUSED)
        people = tuple(
            pid for pid in (op.actor.person_id, op.target.person_id)
            if pid is not None
        )
        refusal = await _publication_refusal(
            self._bindings, people=people, payload=payload)
        if refusal is not None:
            return refusal
        stale = await _revisions_fresh(self._bindings, op)
        if stale is not None:
            return stale
        replay = await _recorded_replay(self._bindings, op)
        if replay is not None:
            return replay
        scope = payload.scope
        if not await self._topology.scope_exists(scope):
            return denied("unknown-scope")
        allowed = await self._actor_allowed(scope, op.actor)
        if allowed is not None:
            return allowed
        person_id = cast(str, op.target.person_id)
        person = await self._bindings.person(person_id)
        if person is None:
            return denied("unknown-person")
        if person.lifecycle is not PersonLifecycle.ACTIVE:
            return denied("person-suspended")
        record = await _record(self._bindings, op, _SCOPED_STEPS)
        if isinstance(record, Outcome):
            return record
        return await self._run_steps(record)

    async def _actor_allowed(
        self, scope: Scope, actor: Actor
    ) -> Outcome | None:
        person = await self._bindings.person(cast(str, actor.person_id))
        if person is None or person.lifecycle is not PersonLifecycle.ACTIVE:
            return denied("actor-suspended")
        verdict = await self._backend.check(
            scope=scope, person_id=cast(str, actor.person_id),
            permit="manage_members")
        if verdict.verdict is Verdict.UNAVAILABLE:
            return unavailable("actor-check-unavailable")
        if not verdict.allowed:
            return denied(
                f"actor-lacks-manage_members:{verdict.detail or verdict.verdict.value}")
        return None

    async def _run_steps(self, record: OperationRecord) -> Outcome:
        """store write -> read-back, skipping journaled-done steps."""
        snapshot = dict(record.snapshot)
        person_id = snapshot["target_person"]
        nickname = snapshot["nickname"]
        scope_row = snapshot["profile_scope"]
        scope = Scope(ScopeKind(scope_row["kind"]), scope_row["id"])
        newest = await self._bindings.latest_profile_operation(
            person_id, scope=scope)
        if newest is not None and newest.operation_id != record.operation_id:
            # The journal's order is the intents' order for THIS
            # (person, scope): a later journaled edit owns the nickname,
            # so this replay writes nothing over it.
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", "superseded")
            return denied("superseded")
        outcome = await self._step(
            record, "profile",
            lambda: self._bindings.set_scoped_name(
                person_id=person_id, scope=scope, nickname=nickname,
                operation_id=record.operation_id))
        if outcome.status is OutcomeStatus.COMPLETE:
            outcome = await self._step(
                record, "readback",
                lambda: self._readback(person_id, scope, nickname))
        if outcome.status is OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "complete")
            return complete()
        if outcome.status is OutcomeStatus.DENIED:
            await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                          "denied", outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                      outcome.status.value, outcome.detail)
        return outcome

    async def _step(
        self, record: OperationRecord, step: str, effect: Any
    ) -> Outcome:
        if not _pending(record, step):
            return complete()
        outcome = await effect()
        if outcome.status is OutcomeStatus.COMPLETE:
            await _step_done(self._bindings, record, step)
        return outcome

    async def _readback(
        self, person_id: str, scope: Scope, nickname: str
    ) -> Outcome:
        """Success only when the stored row reads the intended nickname."""
        actual = await self._bindings.scoped_name_for(
            person_id=person_id, scope=scope)
        if actual == nickname:
            return complete()
        return unavailable("read-back-mismatch")

    async def reconcile(self) -> tuple[OperationRecord, ...]:
        """Restart recovery — resume every journaled SCOPED
        `update_profile` still owed. Global journals stay with
        `ProfileService`."""
        async with self._bindings.mutation():
            return await self._reconcile()

    async def _reconcile(self) -> tuple[OperationRecord, ...]:
        finished: list[OperationRecord] = []
        for record in await self._bindings.incomplete_operations():
            if (record.kind != _PROFILE_KIND
                    or "profile_scope" not in record.snapshot):
                continue
            try:
                outcome = await self._run_steps(record)
            except ManagedError:
                raise
            except Exception:
                log.exception("scoped profile replay failed")
                outcome = unavailable("store-failure")
            if outcome.status is OutcomeStatus.COMPLETE:
                current = await self._bindings.operation(record.operation_id)
                if (current is not None
                        and current.state is OperationJournalState.COMPLETE):
                    finished.append(current)
        return tuple(finished)
