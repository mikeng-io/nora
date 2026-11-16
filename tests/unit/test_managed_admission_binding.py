"""The admission's invocation binding — one immutable captured effect and
completed replay as a checked historical receipt, on the managed fakes."""

import copy
from dataclasses import replace
from types import MappingProxyType

import pytest

from nora.domain.managed import (
    OperationJournalState,
    OperationRecord,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.app.managed_admission import (
    IMPORT_KIND,
    FileAdmission,
    prepare,
)
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
)
from tests.fakes import FakeAdmissionKratos, FakeAdmissionProbe
from tests.unit.test_managed_admission import _sources


@pytest.fixture
def graph():
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    return store, backend, registry, FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(registry.identities), probe=FakeAdmissionProbe(store),
    )


async def test_a_completed_replay_is_a_historical_receipt_with_no_effects(
    tmp_path, graph,
):
    """Kind + full snapshot + COMPLETE is a historical receipt: the retry
    returns the recorded outcome AS-IS after later legitimate management
    changes and unrelated journals — revalidating nothing, reasserting no
    hold, calling no remote write, restoring no old traits or grants."""
    store, backend, registry, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    prepared = prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    outcome = await admission.apply_prepared(prepared)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    recorded = store.operations[prepared.import_id]

    # Legitimate later changes an authorized operation may leave.
    await store.set_lifecycle(
        person_id="ada", lifecycle=PersonLifecycle.SUSPENDED)
    await store.hold_identity(person_id="miles", operation_id="op-9")
    await store.record_operation(OperationRecord(
        "grant-9", "grant_access", OperationJournalState.COMPLETE,
        MappingProxyType({}), snapshot=MappingProxyType({"kind": "grant_access"})))
    registry.identities["miles"]["traits"]["display_name"] = "Changed Later"
    room_a = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
    await backend.put_relation(
        scope=room_a, relation="member", person_id="late-joiner")
    before = (
        dict(store.people), dict(store.accounts), set(store.owners),
        dict(store.aliases), dict(store.memberships), dict(store.supports),
        set(store.identity_holds), dict(store.operations),
        set(backend.tuples), copy.deepcopy(registry.identities),
    )
    receipt = await admission.apply_prepared(prepared)
    assert receipt.status is OutcomeStatus.COMPLETE
    assert receipt.detail == recorded.detail
    # Nothing was written: the later changes stand — no holds re-asserted,
    # no grants re-applied, no traits restored, the journal untouched.
    assert (dict(store.people), dict(store.accounts), set(store.owners),
            dict(store.aliases), dict(store.memberships), dict(store.supports),
            set(store.identity_holds), dict(store.operations),
            set(backend.tuples), registry.identities) == before
    # Read-only assess attributes the same way.
    assessed = await admission.assess_prepared(prepared)
    assert assessed.status is OutcomeStatus.COMPLETE
    assert assessed.detail == recorded.detail


@pytest.mark.parametrize("journal_state", [
    OperationJournalState.COMPLETE, OperationJournalState.INCOMPLETE,
])
async def test_the_same_import_id_with_a_different_snapshot_refuses(
    tmp_path, graph, journal_state,
):
    """Same operation id, a different journaled effect — a CONFLICT on
    apply and assess alike: never the old complete, never new work, and
    the row and every hold left untouched."""
    store, backend, registry, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    prepared = prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    tampered = MappingProxyType(dict(prepared.snapshot, effect_sha256="0" * 64))
    store.operations[prepared.import_id] = OperationRecord(
        operation_id=prepared.import_id, kind=IMPORT_KIND, state=journal_state,
        steps=MappingProxyType({}), outcome="complete", detail="older-run",
        snapshot=tampered)
    for attempt in (admission.apply_prepared, admission.assess_prepared):
        outcome = await attempt(prepared)
        assert outcome.status is OutcomeStatus.DENIED
        assert outcome.detail == "effect-conflict"
    assert not store.people and not store.accounts and not store.identity_holds
    assert not registry.identities and not backend.tuples
    assert store.operations[prepared.import_id].snapshot == tampered
    # Positive control: the exact durable record attributes honestly —
    # even with nothing on the target, its own receipt answers.
    store.operations[prepared.import_id] = OperationRecord(
        operation_id=prepared.import_id, kind=IMPORT_KIND,
        state=OperationJournalState.COMPLETE, steps=MappingProxyType({}),
        outcome="complete", detail="recorded-result",
        snapshot=prepared.snapshot)
    receipt = await admission.apply_prepared(prepared)
    assert receipt.status is OutcomeStatus.COMPLETE
    assert receipt.detail == "recorded-result"
    assert not store.people and not store.identity_holds and not registry.identities


async def test_the_same_import_id_with_a_foreign_kind_refuses(tmp_path, graph):
    """A durable journal under this import's id but another operation kind
    is foreign — refused before any write, never adopted or replayed."""
    store, _, registry, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    prepared = prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    store.operations[prepared.import_id] = OperationRecord(
        operation_id=prepared.import_id, kind="grant_access",
        state=OperationJournalState.COMPLETE, steps=MappingProxyType({}),
        snapshot=prepared.snapshot)
    outcome = await admission.apply_prepared(prepared)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "effect-conflict"
    assert not store.people and not store.identity_holds and not registry.identities


class _PhantomJournal(FakeAdmissionProbe):
    """The claim read sees a journal row the bindings read cannot return —
    an inconsistent journal is unavailable, never a guessed replay."""

    def __init__(self, store, operation_id):
        super().__init__(store)
        self._operation_id = operation_id

    async def facts(self):
        facts = await super().facts()
        return replace(facts, operations=facts.operations | {
            (self._operation_id, IMPORT_KIND,
             OperationJournalState.COMPLETE.value)})


async def test_a_journal_row_the_store_cannot_read_back_is_unavailable(tmp_path):
    """`facts` names the journal, `operation()` returns nothing — the import
    reports unavailable, never a fabricated replay or a fresh claim."""
    store = FakeBindingStore()
    backend = FakeAuthorityBackend()
    registry = FakeIdentityRegistry()
    identity_path, policy_path = _sources(tmp_path)
    prepared = prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    admission = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(registry.identities),
        probe=_PhantomJournal(store, prepared.import_id))
    outcome = await admission.apply_prepared(prepared)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "journal-missing"
    assessed = await admission.assess_prepared(prepared)
    assert assessed.status is OutcomeStatus.UNAVAILABLE
    assert not store.people and not store.identity_holds and not registry.identities


async def test_a_journal_read_failure_is_unavailable(tmp_path, graph):
    """A store that cannot answer the journal read is unavailable — the
    import never guesses completed or fresh."""
    store, _, _, admission = graph
    identity_path, policy_path = _sources(tmp_path)
    prepared = prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    store.down = True
    outcome = await admission.apply_prepared(prepared)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "store-failure"
