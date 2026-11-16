"""Admission identity ownership — the Kratos-side guards of the one-time
import. `external_id` correlates but never proves ownership: a foreign
record holding it is refused untouched, an owned create whose local
correlation the process lost is recovered by a NEW seam reading the SAME
durable `metadata_admin` marker, and the read-back binds the concrete
record — id, marker, state and the intended initial traits — to the SQL
correlation before any hold releases.
"""

from dataclasses import replace

import pytest

from nora.domain.managed import (
    OperationJournalState,
    Outcome,
    OutcomeStatus,
    unavailable,
)
from nora.people.app.managed_admission import FileAdmission, _prepare
from nora.people.app.managed_admission_identity import (
    AdmissionIdentity,
    admission_marker,
    ensure_owned,
)
from nora.testing.managed import FakeAuthorityBackend, FakeBindingStore
from tests.fakes import FakeAdmissionKratos, FakeAdmissionProbe
from tests.unit.test_managed_admission import _sources


class _ForeignOnCreate(FakeAdmissionKratos):
    """A foreign record lands between the fresh claim's absence check and
    the create itself — the concurrent holder the 409 exists to refuse."""

    def __init__(self) -> None:
        super().__init__()
        self.foreign = {
            "id": "kratos-foreign", "state": "active",
            "traits": {"display_name": "Generated", "social": True},
        }

    async def create(self, *, external_id, traits, owner):
        self.identities.setdefault(external_id, dict(self.foreign))
        return await super().create(
            external_id=external_id, traits=traits, owner=owner)


class _DropFirstCorrelate(FakeBindingStore):
    """The first correlation write reports the process loss a committed
    remote create outlives — the identity exists; the SQL link never landed."""

    def __init__(self) -> None:
        super().__init__()
        self.dropped = False

    async def correlate_kratos(self, *, person_id, kratos_identity_id):
        if not self.dropped:
            self.dropped = True
            return unavailable("process-lost")
        return await super().correlate_kratos(
            person_id=person_id, kratos_identity_id=kratos_identity_id)


class _Tamper(FakeBindingStore):
    """A store whose completed correlation is followed by the remote record
    drifting — holder edits or interference the read-back must catch."""

    def __init__(self, kratos: FakeAdmissionKratos, target: str, mutate) -> None:
        super().__init__()
        self._kratos = kratos
        self._target = target
        self._mutate = mutate

    async def correlate_kratos(self, *, person_id, kratos_identity_id):
        outcome = await super().correlate_kratos(
            person_id=person_id, kratos_identity_id=kratos_identity_id)
        if outcome.status is OutcomeStatus.COMPLETE and person_id == self._target:
            self._mutate(self._kratos.identities[person_id])
        return outcome


async def test_a_collision_landing_mid_apply_refuses_without_adopting(tmp_path):
    """A record appearing between the fresh check and the create is foreign
    unless it carries this import's marker — refused, byte-identical, never
    correlated into the binding store."""
    store, backend = FakeBindingStore(), FakeAuthorityBackend()
    kratos = _ForeignOnCreate()
    admission = FileAdmission(
        bindings=store, backend=backend, kratos=kratos, probe=FakeAdmissionProbe(store))
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "kratos-foreign-identity"
    assert kratos.identities == {"ada": kratos.foreign}
    ada = await store.person("ada")
    assert ada is not None and ada.kratos_identity_id is None
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE


async def test_an_owned_create_recovers_under_a_new_service_and_stays_single(
    tmp_path,
):
    """Create committed, correlate lost: a fresh seam over the same Kratos
    world reads the durable marker, adopts exactly that record and finishes
    — one identity per person, `social=false` preserved for miles."""
    store, backend = _DropFirstCorrelate(), FakeAuthorityBackend()
    identities: dict = {}
    admission = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(identities), probe=FakeAdmissionProbe(store))
    identity_path, policy_path = _sources(tmp_path)
    first = await admission.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.UNAVAILABLE
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE
    assert journal.steps["people"] == "done"
    assert journal.steps["identities"] == "pending"
    ada = await store.person("ada")
    assert ada is not None and ada.kratos_identity_id is None
    created = identities["ada"]
    prepared = _prepare(identity_path, policy_path)
    assert not isinstance(prepared, Outcome)
    assert created["metadata_admin"] == {
        "nora_admission": {
            "import_id": prepared.import_id,
            "effect_sha256": prepared.snapshot["effect_sha256"],
        }
    }
    # New process: a new seam object over the same remote world, same store.
    resumed = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(identities), probe=FakeAdmissionProbe(store))
    outcome = await resumed.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.COMPLETE, outcome.detail
    ada = await store.person("ada")
    assert ada is not None and ada.kratos_identity_id == created["id"]
    assert set(identities) == {"ada", "listed_only", "miles"}
    assert identities["miles"]["traits"] == {
        "display_name": "Miles", "social": False}
    replay = await resumed.apply(identity_path, policy_path)
    assert replay.status is OutcomeStatus.COMPLETE
    assert set(identities) == {"ada", "listed_only", "miles"}


@pytest.mark.parametrize("mutate", [
    lambda record: record.pop("metadata_admin", None),
    lambda record: record.update({"metadata_admin": {}}),
    lambda record: record.update({"metadata_admin": {"nora_admission": "x"}}),
    lambda record: record.update({"metadata_admin": {"nora_admission": {
        "import_id": "file-admission:other", "effect_sha256": "other"}}}),
], ids=["absent-metadata", "empty-metadata", "malformed-marker",
        "other-import"])
async def test_resume_refuses_a_collision_without_this_imports_marker(
    tmp_path, mutate,
):
    """On resume the record holding the external id is foreign whenever the
    durable evidence is absent, malformed or names another import — refused
    untouched, never adopted to satisfy the pending step."""
    store, backend = _DropFirstCorrelate(), FakeAuthorityBackend()
    identities: dict = {}
    admission = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(identities), probe=FakeAdmissionProbe(store))
    identity_path, policy_path = _sources(tmp_path)
    first = await admission.apply(identity_path, policy_path)
    assert first.status is OutcomeStatus.UNAVAILABLE
    foreign = dict(identities["ada"])
    mutate(foreign)
    identities["ada"] = foreign
    resumed = FileAdmission(
        bindings=store, backend=backend,
        kratos=FakeAdmissionKratos(identities), probe=FakeAdmissionProbe(store))
    outcome = await resumed.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "kratos-foreign-identity"
    assert identities["ada"] is foreign
    ada = await store.person("ada")
    assert ada is not None and ada.kratos_identity_id is None


@pytest.mark.parametrize("target,mutate", [
    ("ada", lambda record: record.update({"id": "kratos-foreign"})),
    ("ada", lambda record: record.update({"state": "inactive"})),
    ("ada", lambda record: record.pop("metadata_admin", None)),
    ("ada", lambda record: record["traits"].update(
        {"display_name": "Generated Other"})),
    ("miles", lambda record: record["traits"].update({"social": True})),
    ("miles", lambda record: record["traits"].pop("social", None)),
    ("miles", lambda record: record["traits"].update({"social": "false"})),
], ids=["id-mismatch", "state-inactive", "marker-dropped", "name-drift",
        "social-flipped", "social-missing", "social-nonboolean"])
async def test_readback_fails_closed_when_the_owned_record_drifted(
    tmp_path, target, mutate,
):
    """Between correlate and read-back the record can drift — a different
    id, an inactive state, lost evidence or edited initial traits all fail
    closed instead of releasing the holds."""
    kratos = FakeAdmissionKratos()
    store = _Tamper(kratos, target, mutate)
    admission = FileAdmission(
        bindings=store, backend=FakeAuthorityBackend(),
        kratos=kratos, probe=FakeAdmissionProbe(store))
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "read-back-mismatch"
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE
    assert journal.steps["readback"] == "pending"
    assert await store.identity_held(target)


async def test_a_create_response_without_the_marker_fails_closed(tmp_path):
    """The request carried the marker but the created record's echo lacks
    it — without durable evidence there is nothing to adopt, so the import
    fails closed instead of correlating a record it cannot later prove."""

    class _DropMarkerEcho(FakeAdmissionKratos):
        async def create(self, *, external_id, traits, owner):
            outcome, view = await super().create(
                external_id=external_id, traits=traits, owner=owner)
            if view is not None:
                view = AdmissionIdentity(
                    identity_id=view.identity_id, external_id=view.external_id,
                    state=view.state, traits=view.traits, metadata_admin=None)
            return outcome, view

    store, backend = FakeBindingStore(), FakeAuthorityBackend()
    kratos = _DropMarkerEcho()
    admission = FileAdmission(
        bindings=store, backend=backend, kratos=kratos, probe=FakeAdmissionProbe(store))
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "owner-evidence-mismatch"
    ada = await store.person("ada")
    assert ada is not None and ada.kratos_identity_id is None
    journal = next(iter(store.operations.values()))
    assert journal.state is OperationJournalState.INCOMPLETE


async def test_a_kratos_outage_on_the_fresh_claim_is_unavailable(tmp_path):
    """A fresh claim cannot confirm the target is empty while Kratos is
    down — unavailable before anything is written, never a guessed grant."""
    store, backend = FakeBindingStore(), FakeAuthorityBackend()
    kratos = FakeAdmissionKratos()
    kratos.down = True
    admission = FileAdmission(
        bindings=store, backend=backend, kratos=kratos, probe=FakeAdmissionProbe(store))
    identity_path, policy_path = _sources(tmp_path)
    outcome = await admission.apply(identity_path, policy_path)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert not store.operations and not store.people
    assert not kratos.identities


async def test_owned_recovery_refuses_a_different_external_person():
    class WrongExternal(FakeAdmissionKratos):
        async def read(self, *, external_id):
            outcome, view = await super().read(external_id=external_id)
            return outcome, replace(view, external_id="generated-other")

    marker = admission_marker(import_id="generated-import", effect_sha256="generated-effect")
    kratos = WrongExternal()
    traits = {"display_name": "Generated", "social": False}
    created, original = await kratos.create(
        external_id="generated-person", traits=traits, owner=marker)
    assert created.status is OutcomeStatus.COMPLETE and original is not None
    outcome, recovered = await ensure_owned(
        kratos, external_id="generated-person", traits=traits, marker=marker)
    assert outcome.status is OutcomeStatus.DENIED
    assert recovered is None
    assert len(kratos.identities) == 1
