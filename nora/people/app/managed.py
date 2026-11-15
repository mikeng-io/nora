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
scope's real containment NOW. That revalidation is this layer's job —
registration here, authority in `managed_authority`, journal plumbing in
`managed_ops`.
"""


from __future__ import annotations

import logging
from typing import cast
from uuid import uuid4

from nora.domain.managed import (
    AccountState,
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
    AccountObservation,
    AccountRegistration,
    Operation,
    Unlink,
)
from nora.people.app.managed_authority import AuthorityService
from nora.people.app.managed_ops import (
    Effect,
    _finish,
    _op_snapshot,
    _pending,
    _publication_refusal,
    _record,
    _recorded_replay,
    _revisions_fresh,
    _step_done,
)

log = logging.getLogger("nora.people.managed")

_REFUSED = "operation not wired in this slice"

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
            async with self._bindings.mutation():
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
        if op.kind != "suspend_identity":
            refusal = await _publication_refusal(self._bindings,
                people=(op.actor.person_id, op.target.person_id), payload=op.payload,
                account=op.target.account, scope=op.target.scope)
            if refusal is not None:
                return refusal
        if op.kind == "register_account":
            replay = await self._completed_registration(op)
            if replay is not None:
                return replay
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

    async def _completed_registration(self, op: Operation) -> Outcome | None:
        """A completed registration's retry re-reads its result; changed bindings still refuse."""
        recorded = await self._bindings.operation(op.operation_id)
        if recorded is None or recorded.state is not OperationJournalState.COMPLETE:
            return None
        if recorded.kind != op.kind or recorded.snapshot != _op_snapshot(op):
            return denied("operation-conflict")
        payload = cast(AccountRegistration, op.payload)
        account = op.target.account
        if account is None:
            return denied("missing-account")
        binding = await self._bindings.account_for(account.platform, account.kind, account.value)
        expected = await self._bindings.canonical_person(payload.person_id)
        if (binding is None or expected is None or binding.person_id != expected
                or binding.identifiers != payload.identifiers):
            return denied("registration-binding-changed")
        if await self._bindings.person_lifecycle(expected) is not PersonLifecycle.ACTIVE:
            return denied("person-suspended")
        return complete("replayed")

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
            # Registration never attaches to an EXISTING person — only the
            # bilateral link can (management.md); the one pass is an exact
            # replay under another id that adds and widens nothing.
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
        """The account this payload exactly re-describes — `None` when an identifier is unbound."""
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
        """Run one journaled store effect, skipping it when the journal marks it done."""
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
        account = op.target.account
        if account is None:
            raise ManagedError("unlink_account carries no account target")
        bound = await self._bindings.account_for(
            account.platform, account.kind, account.value,
        )
        if bound is None or bound.person_id != payload.expected_person:
            return denied("binding-mismatch")
        record = await _record(self._bindings, op, ("binding",))
        if isinstance(record, Outcome):
            return record
        outcome = await self._run_binding(
            record, "binding",
            lambda: self._bindings.revoke_binding(account=account),
        )
        if outcome.status is not OutcomeStatus.COMPLETE:
            await _finish(self._bindings, record, OperationJournalState.INCOMPLETE,
                          outcome.status.value, outcome.detail)
            return outcome
        await _finish(self._bindings, record, OperationJournalState.COMPLETE,
                      "complete")
        return complete()
