"""`RegistrationService` against the in-memory fakes — the behaviors the
managed backend's binding half owns (`management.md`):

- an unclaimed account registers an INDEPENDENT person — service, operator
  and roster paths never attach an account to an existing person (only the
  bilateral link can), and an exact already-owned replay under a different
  `operation_id` completes without widening;
- the journal is the idempotency authority: the same `operation_id`
  replays the recorded outcome, a non-complete journal row resumes its
  owed steps on an authenticated retry, and a reused id carrying a
  different effect is an authoritative conflict, never a replay;
- observed revisions are revalidated at commitment — a stale identity or
  ownership revision denies;
- a store that cannot answer is `unavailable`, not a crash and never a
  denial.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import (
    AccountObservation,
    AccountRegistration,
    Actor,
    ActorKind,
    LifecycleAction,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
    Unlink,
)
from nora.people.app.link_publication import LinkPublicationService
from nora.people.app.managed import RegistrationService
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

ROOM = Scope(ScopeKind.ROOM, "signal/conv-1")
PERSON = "person_alice"
ACCT = Identifier(platform="signal", kind="uuid", value="85290000101")
ACCT_2 = Identifier(platform="signal", kind="uuid", value="85290000102")
ACCT_3 = Identifier(platform="signal", kind="uuid", value="85290000103")
PHONE = Identifier(platform="signal", kind="phone", value="85290000101")


def _service() -> tuple[FakeBindingStore, FakeIdentityRegistry, RegistrationService]:
    store = FakeBindingStore()
    registry = FakeIdentityRegistry()
    return store, registry, RegistrationService(store, registry)


def _register_op(
    operation_id: str,
    *,
    person_id: str = PERSON,
    identifiers: frozenset[Identifier] = frozenset({ACCT}),
    ownership: str = "0",
) -> Operation:
    return Operation(
        operation_id=operation_id,
        kind="register_account",
        actor=Actor(
            kind=ActorKind.SERVICE,
            service_id="bridge",
            sources=frozenset({"signal"}),
        ),
        target=Target(kind=TargetKind.ACCOUNT, account=next(iter(identifiers))),
        payload=AccountRegistration(
            person_id=person_id, scope=ROOM, identifiers=identifiers
        ),
        observed_revisions={"ownership": ownership, "authority": "0"},
    )


async def _register(
    svc: RegistrationService, operation_id: str, **kwargs: object
):
    return await svc.apply(_register_op(operation_id, **kwargs))


async def test_registration_establishes_person_account_membership_and_kratos():
    store, registry, svc = _service()
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.COMPLETE

    person = await store.person(PERSON)
    assert person is not None
    assert person.lifecycle is PersonLifecycle.ACTIVE
    # Registration correlated the person to a real identity record, read
    # back before the operation completed.
    assert person.kratos_identity_id == registry.identities[PERSON]["id"]

    account = await store.record_for(account=ACCT)
    assert account is not None
    assert account.person_id == PERSON
    assert account.state is AccountState.BOUND

    memberships = await store.memberships_for(PERSON)
    assert [m.scope for m in memberships] == [ROOM]


async def test_the_same_operation_id_replays_the_recorded_outcome():
    store, _, svc = _service()
    first = await _register(svc, "op-1")
    second = await _register(svc, "op-1")
    assert first.status is OutcomeStatus.COMPLETE
    assert second.status is OutcomeStatus.COMPLETE
    assert len(store.accounts) == 1
    assert len(store.people) == 1


async def test_a_reused_operation_id_with_a_different_effect_conflicts():
    store, _, svc = _service()
    await _register(svc, "op-1")
    outcome = await _register(svc, "op-1", person_id="person_other")
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operation-conflict"
    # And nothing was widened under the cover of a replay.
    assert len(store.accounts) == 1
    assert len(store.people) == 1


async def test_registration_never_attaches_an_account_to_an_existing_person():
    """management.md: «Registration of an observed account is not
    permission to claim it for an existing person» — only the bilateral
    link can. A second account naming the same person_id is refused, and
    nothing binds."""
    store, _, svc = _service()
    await _register(svc, "op-1")
    outcome = await _register(svc, "op-2", identifiers=frozenset({ACCT_2}))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "person-exists"

    assert await store.record_for(account=ACCT_2) is None
    assert len(await store.accounts_for(PERSON)) == 1


async def test_registration_cannot_widen_an_existing_accounts_identifiers():
    """An exact replay must not widen: one familiar identifier plus one
    new one is a claim on the person, not a replay."""
    store, _, svc = _service()
    await _register(svc, "op-1", identifiers=frozenset({ACCT, PHONE}))
    outcome = await _register(
        svc, "op-2", identifiers=frozenset({ACCT, ACCT_2})
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert await store.record_for(account=ACCT_2) is None


async def test_an_exact_already_owned_replay_completes_without_new_writes():
    """A roster retry under a NEW operation id that names the same person
    and re-presents only identifiers that account already owns is the
    recorded effect restated — complete, and no second account."""
    store, _, svc = _service()
    await _register(svc, "op-1", identifiers=frozenset({ACCT, PHONE}))
    bound = await store.record_for(account=ACCT)
    assert bound is not None
    outcome = await _register(
        svc, "op-2",
        identifiers=frozenset({ACCT}),
        ownership=str(bound.revision),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert outcome.detail == PERSON
    assert len(store.accounts) == 1


async def test_identifiers_split_across_accounts_are_not_a_replay():
    """The replay pass is ONE account: identifiers spanning two of the
    person's accounts is a reshaping claim, denied like any other."""
    store, _, svc = _service()
    await _register(svc, "op-1", identifiers=frozenset({ACCT}))
    # A second account reaches the same person only through paths the
    # store allows; registration is not one of them, so seed it directly.
    await store.register(
        person=ManagedPerson(person_id=PERSON, lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id=str(uuid4()),
            person_id=PERSON,
            platform="signal",
            identifiers=frozenset({ACCT_2}),
            state=AccountState.BOUND,
        ),
        membership=None,
    )
    bound = await store.record_for(account=ACCT)
    assert bound is not None
    outcome = await _register(
        svc, "op-2",
        identifiers=frozenset({ACCT, ACCT_2}),
        ownership=str(bound.revision),
    )
    assert outcome.status is OutcomeStatus.DENIED


async def test_a_suspended_person_denies_registration():
    store, _, svc = _service()
    await _register(svc, "op-1")
    await store.set_lifecycle(person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    outcome = await _register(svc, "op-2", identifiers=frozenset({ACCT_2}))
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "person-suspended"


async def test_an_identifier_owned_by_another_person_conflicts():
    """Discovery never merges: the same matchable identifier under a new
    person id is the store's unique-ownership conflict, denied."""
    _, _, svc = _service()
    await _register(svc, "op-1")
    outcome = await _register(
        svc, "op-2", person_id="person_other", identifiers=frozenset({ACCT})
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "identifier-conflict"


async def test_a_stale_ownership_revision_denies():
    store, _, svc = _service()
    await _register(svc, "op-1")
    bound = await store.record_for(account=ACCT)
    assert bound is not None
    outcome = await _register(
        svc, "op-2", identifiers=frozenset({ACCT}),
        ownership=str(bound.revision + 5),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "stale-ownership-revision"


async def test_a_stale_identity_revision_denies_a_lifecycle_change():
    store, _, svc = _service()
    await _register(svc, "op-1")
    op = Operation(
        operation_id="op-2",
        kind="suspend_identity",
        actor=Actor(kind=ActorKind.INSTALLATION, person_id="installer"),
        target=Target(kind=TargetKind.PERSON, person_id=PERSON),
        payload=LifecycleAction(reason="moderation"),
        observed_revisions={"identity": "99", "authority": "0"},
    )
    outcome = await svc.apply(op)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "stale-identity-revision"
    person = await store.person(PERSON)
    assert person is not None and person.lifecycle is PersonLifecycle.ACTIVE


async def test_suspension_suspends_the_kratos_identity_and_sessions():
    store, registry, svc = _service()
    await _register(svc, "op-1")
    person = await store.person(PERSON)
    assert person is not None
    op = Operation(
        operation_id="op-2",
        kind="suspend_identity",
        actor=Actor(kind=ActorKind.INSTALLATION, person_id="installer"),
        target=Target(kind=TargetKind.PERSON, person_id=PERSON),
        payload=LifecycleAction(reason="moderation"),
        observed_revisions={
            "identity": str(person.revision), "authority": "0"
        },
    )
    outcome = await svc.apply(op)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert registry.identities[PERSON]["state"] == "inactive"
    assert registry.sessions.get(PERSON, 0) == 0


async def test_a_completed_replay_still_revalidates_the_observed_revision():
    """The journal row answers a replay only after the observed revision is
    revalidated — the same op resubmitted against a moved person is stale,
    never a replayed success; re-observing the current revision recovers
    the recorded outcome."""
    store, _, svc = _service()
    await _register(svc, "op-1")
    person = await store.person(PERSON)
    assert person is not None
    op = Operation(
        operation_id="op-2",
        kind="suspend_identity",
        actor=Actor(kind=ActorKind.INSTALLATION, person_id="installer"),
        target=Target(kind=TargetKind.PERSON, person_id=PERSON),
        payload=LifecycleAction(reason="moderation"),
        observed_revisions={
            "identity": str(person.revision), "authority": "0"
        },
    )
    outcome = await svc.apply(op)
    assert outcome.status is OutcomeStatus.COMPLETE
    # The suspension bumped the person's revision — the resubmitted spec
    # observes a world that no longer exists.
    outcome = await svc.apply(op)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "stale-identity-revision"
    moved = await store.person(PERSON)
    assert moved is not None
    re_observed = Operation(
        operation_id="op-2",
        kind="suspend_identity",
        actor=op.actor, target=op.target, payload=op.payload,
        observed_revisions={
            "identity": str(moved.revision), "authority": "0"
        },
    )
    outcome = await svc.apply(re_observed)
    assert outcome.status is OutcomeStatus.COMPLETE


async def _unlink_operation(store):
    bound = await store.record_for(account=ACCT)
    person = await store.person(PERSON)
    assert bound is not None and person is not None
    return Operation(
        operation_id="op-2",
        kind="unlink_account",
        actor=Actor(
            kind=ActorKind.HOLDER,
            person_id=PERSON,
            account=ACCT,
            proof_issuer="signal-cli",
        ),
        target=Target(kind=TargetKind.ACCOUNT, account=ACCT),
        payload=Unlink(expected_person=PERSON),
        observed_revisions={
            "identity": str(person.revision),
            "ownership": str(bound.revision),
            "authority": "0",
        },
    )


async def test_unlink_revokes_only_that_account():
    store, _, svc = _service()
    await _register(svc, "op-1")
    outcome = await svc.apply(await _unlink_operation(store))
    assert outcome.status is OutcomeStatus.COMPLETE
    # The binding is revoked — the identifier resolves no longer, and it
    # can never be re-bound through a fresh registration.
    assert await store.account_for("signal", "uuid", "85290000101") is None
    rebind = await _register(
        svc, "op-3", person_id="person_other", identifiers=frozenset({ACCT})
    )
    assert rebind.status is OutcomeStatus.DENIED


@pytest.mark.parametrize("failure", ["missing", "unavailable"])
async def test_unlink_requires_confirmed_session_revocation_and_reconciles(failure):
    store, registry, registration = _service()
    assert (await _register(registration, "op-1")).status is OutcomeStatus.COMPLETE
    operation = await _unlink_operation(store)
    identity = registry.identities[PERSON]
    registry.sessions[PERSON] = 2
    if failure == "missing":
        del registry.identities[PERSON]
    else:
        registry.down = True

    outcome = await registration.apply(operation)
    assert outcome.status is not OutcomeStatus.COMPLETE
    record = await store.operation(operation.operation_id)
    assert record is not None and record.state is OperationJournalState.INCOMPLETE
    assert record.steps["sessions"] == "pending"
    assert await store.account_for(ACCT.platform, ACCT.kind, ACCT.value) is None
    assert registry.sessions[PERSON] == 2

    registry.down = False
    registry.identities[PERSON] = identity
    recovery = LinkPublicationService(
        store, FakeAuthorityBackend(), FakeTopologySource(), registry, FakeLinkRequests()
    )
    finished = await recovery.reconcile()
    assert [row.operation_id for row in finished] == [operation.operation_id]
    assert finished[0].state is OperationJournalState.COMPLETE
    assert registry.sessions.get(PERSON, 0) == 0


async def test_observation_of_an_unclaimed_account_creates_an_independent_person():
    store, _, svc = _service()
    op = Operation(
        operation_id="op-1",
        kind="observe_account",
        actor=Actor(
            kind=ActorKind.SERVICE,
            service_id="bridge",
            sources=frozenset({"signal"}),
        ),
        target=Target(kind=TargetKind.ACCOUNT, account=ACCT_3),
        payload=AccountObservation(identifiers=frozenset({ACCT_3})),
        observed_revisions={"ownership": "0"},
    )
    outcome = await svc.apply(op)
    assert outcome.status is OutcomeStatus.COMPLETE
    account = await store.record_for(account=ACCT_3)
    assert account is not None
    # Discovery never groups: the observed person is fresh and unrelated.
    assert account.person_id != PERSON
    assert len(store.people) == 1


async def test_an_observed_account_is_not_observed_twice():
    store, _, svc = _service()
    for op_id in ("op-1", "op-2"):
        outcome = await svc.apply(
            Operation(
                operation_id=op_id,
                kind="observe_account",
                actor=Actor(
                    kind=ActorKind.SERVICE,
                    service_id="bridge",
                    sources=frozenset({"signal"}),
                ),
                target=Target(kind=TargetKind.ACCOUNT, account=ACCT_3),
                payload=AccountObservation(identifiers=frozenset({ACCT_3})),
                observed_revisions={
                    "ownership": str(
                        (await store.record_for(account=ACCT_3) or
                         ManagedAccount(
                             account_id="-", person_id="-", platform="signal",
                             identifiers=frozenset({ACCT_3}),
                             state=AccountState.BOUND,
                         )).revision
                    )
                },
            )
        )
        assert outcome.status is OutcomeStatus.COMPLETE
    assert len(store.people) == 1


async def test_a_down_store_is_unavailable_never_denied():
    store, _, svc = _service()
    store.down = True
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    store.down = False
    # And nothing half-applied under the outage: a retry commits cleanly.
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.COMPLETE


async def test_a_down_registry_leaves_the_operation_incomplete_not_complete():
    """A Kratos outage mid-registration is `unavailable` to the caller and
    `incomplete` in the journal — part of the effect committed (the
    binding), the rest is owed, and the journal says so instead of
    claiming a distributed transaction."""
    store, registry, svc = _service()
    registry.down = True
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    assert record.steps["binding"] == "done"
    assert record.steps["kratos"] == "pending"


# -- journal recovery: an authenticated retry finishes what is owed --------


class _CrashAtStep(FakeBindingStore):
    """A crash at exactly one journaled boundary — the named step raises
    once before it applies (or once while the `complete` state is being
    written for `finish`), then the store behaves. `calls` counts each
    store effect so a resume's skipped steps stay observable."""

    def __init__(self, step: str) -> None:
        super().__init__()
        self._step = step
        self._fired = False
        self.calls: dict[str, int] = {}

    def _count(self, step: str) -> None:
        self.calls[step] = self.calls.get(step, 0) + 1

    async def register(self, **kwargs):
        self._count("binding")
        if self._step == "binding" and not self._fired:
            self._fired = True
            raise ConnectionError("generated crash at binding")
        return await super().register(**kwargs)

    async def set_membership(self, **kwargs):
        self._count("membership")
        if self._step == "membership" and not self._fired:
            self._fired = True
            raise ConnectionError("generated crash at membership")
        return await super().set_membership(**kwargs)

    async def correlate_kratos(self, **kwargs):
        self._count("correlation")
        if self._step == "correlation" and not self._fired:
            self._fired = True
            raise ConnectionError("generated crash at correlation")
        return await super().correlate_kratos(**kwargs)

    async def update_operation(self, record):
        if (
            self._step == "finish"
            and not self._fired
            and record.state is OperationJournalState.COMPLETE
        ):
            self._fired = True
            raise ConnectionError("generated crash at finish")
        return await super().update_operation(record)


async def test_an_interrupted_registration_resumes_only_the_owed_steps():
    """A Kratos outage leaves binding+membership done; the same operation
    retried finishes the owed steps, then replays the recorded result."""
    store, registry, svc = _service()
    registry.down = True
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    assert record.steps["binding"] == "done"
    assert record.steps["membership"] == "done"
    assert record.steps["kratos"] == "pending"
    registry.down = False
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert outcome.detail == PERSON
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    person = await store.person(PERSON)
    assert person is not None
    assert person.kratos_identity_id == registry.identities[PERSON]["id"]
    assert len(store.people) == 1 and len(store.accounts) == 1
    again = await _register(svc, "op-1")
    assert again.status is OutcomeStatus.COMPLETE
    assert again.detail == "replayed"


@pytest.mark.parametrize(
    ("step", "state", "done_steps", "pending_steps"),
    [
        ("binding", OperationJournalState.PENDING,
         (), ("binding", "membership", "kratos", "correlation")),
        ("membership", OperationJournalState.INCOMPLETE,
         ("binding",), ("membership", "kratos", "correlation")),
        ("correlation", OperationJournalState.INCOMPLETE,
         ("binding", "membership", "kratos"), ("correlation",)),
        ("finish", OperationJournalState.INCOMPLETE,
         ("binding", "membership", "kratos", "correlation"), ()),
    ],
)
async def test_an_interrupted_registration_resumes_each_boundary(
    step, state, done_steps, pending_steps
):
    """A crash at every journaled boundary resumes the remaining steps
    only — steps already done are never re-run, so the one person, one
    account and one Kratos identity the journal recorded stay singular."""
    store, registry, svc = _service()
    store = _CrashAtStep(step)
    svc = RegistrationService(store, registry)
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is state
    for done in done_steps:
        assert record.steps[done] == "done"
    for pending in pending_steps:
        assert record.steps[pending] == "pending"
    bound_before = await store.record_for(account=ACCT)
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert outcome.detail == PERSON
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    person = await store.person(PERSON)
    assert person is not None and person.kratos_identity_id
    # Only the owed steps ran again: the crashed step was retried once,
    # done steps never re-ran (the finish crash re-runs nothing).
    assert store.calls["binding"] == (2 if step == "binding" else 1)
    assert store.calls["membership"] == (2 if step == "membership" else 1)
    assert store.calls["correlation"] == (2 if step == "correlation" else 1)
    assert len(store.people) == 1 and len(store.accounts) == 1
    assert len(registry.identities) == 1
    if bound_before is not None:
        bound_after = await store.record_for(account=ACCT)
        assert bound_after is not None
        assert bound_after.account_id == bound_before.account_id


async def test_a_conflicting_retry_on_an_unfinished_operation_refuses():
    """A same-id retry carrying a different effect is a conflict while
    the journal is still owed — the recorded effect stays resumable."""
    store, registry, svc = _service()
    registry.down = True
    await _register(svc, "op-1")
    registry.down = False
    for kwargs in (
        {"person_id": "person_other"},
        {"identifiers": frozenset({ACCT_2})},
    ):
        outcome = await _register(svc, "op-1", **kwargs)
        assert outcome.status is OutcomeStatus.DENIED
        assert outcome.detail == "operation-conflict"
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    assert await store.record_for(account=ACCT_2) is None
    outcome = await _register(svc, "op-1")
    assert outcome.status is OutcomeStatus.COMPLETE


async def test_a_retry_while_the_outage_continues_stays_incomplete():
    """The owed remainder is `unavailable` while the outage stands —
    never `complete` before the persisted result is read back."""
    store, registry, svc = _service()
    registry.down = True
    await _register(svc, "op-1")
    again = await _register(svc, "op-1")
    assert again.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("op-1")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    assert record.steps["kratos"] == "pending"


@pytest.mark.parametrize("target_change", ["revoke", "suspend"])
async def test_an_interrupted_registration_refuses_changed_binding_or_lifecycle(target_change):
    store, registry, svc = _service()
    registry.down = True
    interrupted = await _register(svc, "op-1")
    assert interrupted.status is OutcomeStatus.UNAVAILABLE
    assert await store.record_for(account=ACCT) is not None
    if target_change == "revoke":
        await store.revoke_binding(account=ACCT)
    else:
        await store.set_lifecycle(person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    registry.down = False
    retried = await _register(svc, "op-1")
    assert retried.status is OutcomeStatus.DENIED
    if target_change == "revoke":
        assert await store.record_for(account=ACCT) is None
    else:
        assert await store.person_lifecycle(PERSON) is PersonLifecycle.SUSPENDED
    assert (await store.person(PERSON)).kratos_identity_id is None
    assert PERSON not in registry.identities
    assert (await store.operation("op-1")).state is OperationJournalState.INCOMPLETE


async def test_the_journaled_snapshot_lists_identifiers_in_canonical_order():
    """The journaled effect survives process reconstruction — frozenset
    order is process luck, so the snapshot records identifiers sorted."""
    store, _, svc = _service()
    identifiers = frozenset({ACCT, PHONE, ACCT_2})
    outcome = await _register(svc, "op-1", identifiers=identifiers)
    assert outcome.status is OutcomeStatus.COMPLETE
    record = await store.operation("op-1")
    assert record is not None
    assert record.snapshot["identifiers"] == [
        {"platform": i.platform, "kind": i.kind, "value": i.value}
        for i in sorted(
            identifiers, key=lambda i: (i.platform, i.kind, i.value)
        )
    ]
