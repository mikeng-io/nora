"""The durable publication boundaries — an admitted person is held from
its first commit, a proven-complete journal releases all and only its
own holds, and an interrupted or older-sequencing import re-protects
before it publishes more — on the managed fakes."""

from types import MappingProxyType

import pytest

from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.people.app.managed_admission import (
    IMPORT_KIND,
    FileAdmission,
    _account_id,
    _prepare,
)
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
)
from tests.fakes import FakeAdmissionKratos, FakeAdmissionProbe
from tests.unit.test_managed_admission import _NoWrites, _sources


@pytest.fixture
def graph():
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    return store, backend, registry, FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(registry.identities), probe=FakeAdmissionProbe(store),
    )


async def test_a_failed_protected_publication_leaves_no_part_of_the_person(
    tmp_path, graph,
):
    """A hold-write failure inside `publish_held_person` rolls the person,
    its accounts and identifiers back with it — nothing of that person is
    readable, and the journal stays non-complete until a retry commits."""
    store, _, registry, _ = graph
    probe = FakeAdmissionProbe(store)
    probe.fail_hold = True
    admission = FileAdmission(
        bindings=store, backend=FakeAuthorityBackend(),
        kratos=FakeAdmissionKratos(registry.identities), probe=probe,
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert not store.people and not store.accounts and not store.owners
    assert not store.identity_holds
    journal = next(iter(store.operations.values()))
    assert journal.state is not OperationJournalState.COMPLETE
    probe.fail_hold = False
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    assert len(store.accounts) == 4 and not store.identity_holds


async def test_a_resume_reprotects_its_owned_rows_before_publishing_more(
    tmp_path,
):
    """Resumed against people committed WITHOUT their hold — the partial
    shape an interrupted older-sequencing import leaves — the retry's
    holds step re-asserts protection BEFORE the identities, memberships
    or authority steps publish further work."""
    store = FakeBindingStore()
    backend = _NoWrites()
    registry = FakeIdentityRegistry()
    admission = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(registry.identities),
        probe=FakeAdmissionProbe(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    prepared = _prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    # The old shape: people committed unprotected, holds still pending.
    for person_id, plan_people in prepared.people.items():
        person = ManagedPerson(
            person_id=person_id, lifecycle=PersonLifecycle.ACTIVE,
            display_name=plan_people.display_name)
        if not prepared.accounts[person_id]:
            store.people[person_id] = person
        for account in prepared.accounts[person_id]:
            await store.register(
                person=person,
                account=ManagedAccount(
                    account_id=_account_id(person_id, account),
                    person_id=person_id, platform=account.platform,
                    identifiers=account.identifiers,
                    state=AccountState.BOUND, label=account.label))
    store.operations[prepared.import_id] = OperationRecord(
        operation_id=prepared.import_id, kind=IMPORT_KIND,
        state=OperationJournalState.INCOMPLETE,
        steps=MappingProxyType({
            "people": "done", "holds": "pending", "identities": "pending",
            "aliases": "pending", "memberships": "pending",
            "authority": "pending", "readback": "pending",
        }),
        snapshot=prepared.snapshot,
    )
    assert not store.identity_holds
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    # Identities/memberships published under re-asserted protection.
    assert registry.identities
    assert all([
        await store.identity_held(pid) for pid in ("miles", "ada", "listed_only")
    ])
    backend.writes_ok = True
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    assert len(store.accounts) == 4 and not store.identity_holds


async def test_completion_commits_the_journal_and_own_release_together(
    tmp_path, graph,
):
    """A completion-commit failure reports unavailable, and the journal
    stays non-complete with every import hold still in place — the retry
    then commits complete-plus-unheld in one durable outcome."""
    store, _, registry, _ = graph
    probe = FakeAdmissionProbe(store)
    probe.fail_finish = True
    admission = FileAdmission(
        bindings=store, backend=FakeAuthorityBackend(),
        kratos=FakeAdmissionKratos(registry.identities), probe=probe,
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    journal = next(iter(store.operations.values()))
    assert journal.state is not OperationJournalState.COMPLETE
    assert all([
        await store.identity_held(pid) for pid in ("miles", "ada", "listed_only")
    ])
    probe.fail_finish = False
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.COMPLETE
    assert journal.outcome == "complete"
    assert not store.identity_holds


class _DropJournal(FakeAdmissionProbe):
    """A completion write whose journal row is gone — the update cannot
    land, so it must be refused rather than reported complete."""

    async def complete_import(self, record, *, person_ids):
        self._store.operations.pop(record.operation_id, None)
        return await super().complete_import(record, person_ids=person_ids)


async def test_a_journal_the_completion_cannot_land_is_never_complete(
    tmp_path, graph,
):
    """A persistence refusal at the completion commit is the outcome —
    never `complete` — and the import's holds stay in place."""
    store, _, registry, _ = graph
    admission = FileAdmission(
        bindings=store, backend=FakeAuthorityBackend(),
        kratos=FakeAdmissionKratos(registry.identities),
        probe=_DropJournal(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "absent-operation"
    assert all([
        await store.identity_held(pid) for pid in ("miles", "ada", "listed_only")
    ])


class _ForeignHoldAtCompletion(FakeAdmissionProbe):
    """An unrelated hold lands on an imported person before the import's
    completion commit — other operations' protections are not the
    import's to release."""

    async def complete_import(self, record, *, person_ids):
        self._store.identity_holds.add(("miles", "foreign-operation"))
        return await super().complete_import(record, person_ids=person_ids)


async def test_completion_releases_only_this_imports_holds(tmp_path, graph):
    """The import completes with another operation's hold still protecting
    a shared person — that protection is preserved, and dependent access
    stays unavailable because of IT, not cleared to pass."""
    store, backend, registry, _ = graph
    admission = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(registry.identities),
        probe=_ForeignHoldAtCompletion(store),
    )
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.COMPLETE
    assert store.identity_holds == {("miles", "foreign-operation")}
    assert await store.identity_held("miles")
    assert not await store.identity_held("ada")


async def test_retry_reasserts_done_holds_after_a_partial_legacy_release(tmp_path):
    store, backend = FakeBindingStore(), _NoWrites()
    kratos = FakeAdmissionKratos()
    admission = FileAdmission(
        bindings=store, backend=backend, kratos=kratos, probe=FakeAdmissionProbe(store))
    identity, policy = _sources(tmp_path)
    first = await admission.apply(identity, policy)
    assert first.status is OutcomeStatus.UNAVAILABLE
    record = next(iter(store.operations.values()))
    assert record.steps["holds"] == "done"
    await store.release_identity_hold(person_id="miles", operation_id=record.operation_id)
    assert not await store.identity_held("miles")
    second = await admission.apply(identity, policy)
    assert second.status is OutcomeStatus.UNAVAILABLE
    assert await store.identity_held("miles")
    backend.writes_ok = True
    assert (await admission.apply(identity, policy)).status is OutcomeStatus.COMPLETE
    assert not store.identity_holds
