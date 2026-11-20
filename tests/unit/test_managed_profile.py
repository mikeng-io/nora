"""`ProfileService` — the holder's global display-name update
(`management.md` Task 5). Kratos owns the authoritative trait; the SQL
`managed_person.display_name` row is the projection every consumer reads.
The behaviours this service owns:

- actor and target derive ONLY from the revalidated session binding — a
  forged or expired claim never reaches the operation;
- the journaled order is kratos -> projection -> read-back: a partial
  effect stays `incomplete` and `reconcile` finishes the
  recorded steps, never a fresh effect;
- a replayed step cannot overwrite a NEWER name — the journaled intent
  refuses (`denied`) rather than reverting someone else's update;
- the projection is descriptive, so preferences, bindings, grants and the
  session's observed revisions all survive it.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from nora.domain.authentication import AccountAuthentication, SessionBinding
from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OperationJournalState,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    unavailable,
)
from nora.domain.management import Scope, ScopeKind
from nora.people.app.link_publication import LinkPublicationService
from nora.people.app.managed import ManagedServices
from nora.people.app.managed_profile import ProfileService
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

PERSON = "generated-person-a"
OTHER = "generated-person-b"
ACCT = Identifier(platform="discord", kind="user_id", value="generated-account-a")
ACCT_B = Identifier(platform="discord", kind="user_id", value="generated-account-b")
ACCOUNT_ID = "generated-binding-a"
ROOM = Scope(ScopeKind.ROOM, "generated-room")
NEW_NAME = "Generated New Name"
OLDER_NAME = "Generated Original Name"


class _FailOnceStore(FakeBindingStore):
    """A projection write that fails once — the partial state a crash
    between the kratos patch and the SQL write leaves."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_projection = True

    async def set_display_name(
        self, *, person_id: str, display_name: str
    ) -> Any:
        if self.fail_projection:
            self.fail_projection = False
            raise RuntimeError("generated projection outage")
        return await super().set_display_name(
            person_id=person_id, display_name=display_name)


class _FailOnceRegistry(FakeIdentityRegistry):
    """`set_display_name` unavailable once — the patch fails AFTER the
    journal lands; `identity_state` stays up so the session check passes."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_patch = True

    async def set_display_name(
        self, *, external_id: str, display_name: str
    ) -> Outcome:
        if self.fail_patch:
            self.fail_patch = False
            return unavailable("generated kratos outage")
        return await super().set_display_name(
            external_id=external_id, display_name=display_name)


def _service(
    store: FakeBindingStore | None = None,
    registry: FakeIdentityRegistry | None = None,
) -> tuple[FakeBindingStore, FakeIdentityRegistry, ManagedServices]:
    store = store or FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: ("generated-unit", False)},
        units={"generated-unit": "generated-workspace"},
        workspaces={"generated-workspace"})
    registry = registry or FakeIdentityRegistry()
    services = ManagedServices(
        store, backend, topology, registry, FakeLinkRequests())
    return store, registry, services


async def _enrolled(
    store: FakeBindingStore, registry: FakeIdentityRegistry, *,
    person_id: str = PERSON, account: Identifier = ACCT,
    account_id: str = ACCOUNT_ID, display_name: str = OLDER_NAME,
) -> None:
    """A bound account plus its correlated identity, preference and labels."""
    await store.register(
        person=ManagedPerson(person_id, PersonLifecycle.ACTIVE,
                             display_name=display_name),
        account=ManagedAccount(account_id, person_id, account.platform,
                               frozenset({account}), AccountState.BOUND),
        membership=ManagedMembership(person_id, ROOM, MembershipState.MEMBER,
                                     "registration"))
    outcome, identity_id = await registry.ensure_identity(
        external_id=person_id,
        traits={"display_name": display_name, "social": False,
                "labels": {"generated": "label"}})
    assert outcome.status is OutcomeStatus.COMPLETE
    await store.correlate_kratos(person_id=person_id,
                               kratos_identity_id=identity_id)


async def _authentication(
    store: FakeBindingStore, *, account: Identifier = ACCT,
    binding: SessionBinding | None = None, purpose: str = "session",
) -> AccountAuthentication:
    """A session assertion whose retained binding is revalidated live."""
    if binding is None:
        record = await store.record_for(account=account)
        assert record is not None
        person = await store.person(record.person_id)
        assert person is not None
        binding = SessionBinding(person.person_id, str(person.revision),
                                 record.account_id, str(record.revision))
    return AccountAuthentication(
        issuer="generated-portal", session_id="generated-session",
        account=account, method="discord_oauth",
        proof_issuer="generated-client", authenticated_at=datetime.now(UTC),
        purpose=purpose, binding=binding)


@pytest.mark.parametrize("purpose", ["session", "sign_in"])
async def test_a_holder_renames_their_own_person_and_consumers_read_it(purpose):
    store, registry, services = _service()
    await _enrolled(store, registry)
    outcome = await asyncio.wait_for(services.profile.update_display_name(
        await _authentication(store, purpose=purpose),
        operation_id="generated-op-1", display_name=NEW_NAME), timeout=5)
    assert outcome.status is OutcomeStatus.COMPLETE

    person = await store.person(PERSON)
    assert person is not None and person.display_name == NEW_NAME
    traits = registry.identities[PERSON]["traits"]
    # The narrow patch: the name moved, every other trait survived.
    assert traits == {"display_name": NEW_NAME, "social": False,
                      "labels": {"generated": "label"}}

    # The consumer read — `nora.access` projects the same row.
    ref = await services.access.person_ref(PERSON)
    assert ref is not None and ref.name == NEW_NAME


async def test_the_update_never_bumps_the_session_binding_revisions():
    """The projection is descriptive: the session that ran the update still
    verifies afterwards, so a retained session is not logged out by a
    rename."""
    store, _, services = _service()
    await _enrolled(store, services.registry)
    authentication = await _authentication(store)
    first = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert first.status is OutcomeStatus.COMPLETE
    # Same retained binding — the display name changed nothing it proves.
    second = await services.profile.update_display_name(
        authentication, operation_id="generated-op-2",
        display_name="Generated Third Name")
    assert second.status is OutcomeStatus.COMPLETE


async def test_the_same_operation_id_replays_without_a_second_effect():
    store, _, services = _service()
    await _enrolled(store, services.registry)
    authentication = await _authentication(store)
    first = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    second = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert first.status is second.status is OutcomeStatus.COMPLETE
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    assert set(record.steps) == {"kratos", "projection", "readback"}
    assert (await store.person(PERSON)).display_name == NEW_NAME


async def test_a_reused_operation_id_with_a_different_name_conflicts():
    store, _, services = _service()
    await _enrolled(store, services.registry)
    authentication = await _authentication(store)
    first = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    conflict = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1",
        display_name="Generated Different Name")
    assert first.status is OutcomeStatus.COMPLETE
    assert conflict.status is OutcomeStatus.DENIED
    assert conflict.detail == "operation-conflict"
    assert (await store.person(PERSON)).display_name == NEW_NAME


@pytest.mark.parametrize("purpose", ["sign_in", "session"])
async def test_a_forged_binding_never_reaches_the_operation(purpose):
    """Wrong-holder: the session's own binding is revalidated live — a
    claim naming someone else's person or revisions refuses before any
    operation is journaled."""
    store, _, services = _service()
    await _enrolled(store, services.registry)
    await _enrolled(store, services.registry, person_id=OTHER,
                    account=ACCT_B, account_id="generated-binding-b")
    forged = await _authentication(
        store, account=ACCT_B,
        binding=SessionBinding(PERSON, "0", ACCOUNT_ID, "0"), purpose=purpose)
    outcome = await services.profile.update_display_name(
        forged, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "authentication-refused"
    assert await store.operation("generated-op-1") is None
    assert (await store.person(PERSON)).display_name == OLDER_NAME
    assert (await store.person(OTHER)).display_name == OLDER_NAME


async def test_a_revoked_or_unbound_account_cannot_rename():
    store, _, services = _service()
    await _enrolled(store, services.registry)
    authentication = await _authentication(store)
    await store.revoke_binding(account=ACCT)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.DENIED
    assert (await store.person(PERSON)).display_name == OLDER_NAME


class _RevokingPublication(LinkPublicationService):
    async def authority_revision(self, person_id):
        revision = await super().authority_revision(person_id)
        async with self._bindings.mutation():
            await self._bindings.revoke_binding(account=ACCT)
        return revision


async def test_a_profile_write_refuses_revocation_after_initial_authentication():
    store, registry, services = _service()
    await _enrolled(store, registry)
    authentication = await _authentication(store)
    publication = _RevokingPublication(
        store, services.backend, services.topology, registry, services.links
    )
    profile = ProfileService(store, registry, publication)
    outcome = await profile.update_display_name(
        authentication, operation_id="generated-revocation-race", display_name=NEW_NAME
    )
    assert await store.record_for(account=ACCT) is None
    assert (await store.person(PERSON)).display_name == OLDER_NAME
    assert registry.identities[PERSON]["traits"]["display_name"] == OLDER_NAME
    assert await store.operation("generated-revocation-race") is None
    assert outcome.status is OutcomeStatus.DENIED


async def test_a_suspended_person_cannot_rename():
    store, _, services = _service()
    await _enrolled(store, services.registry)
    await store.set_lifecycle(person_id=PERSON, lifecycle=PersonLifecycle.SUSPENDED)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.DENIED
    assert (await store.person(PERSON)).display_name == OLDER_NAME


async def test_an_identity_hold_is_unavailable_not_denied():
    store, _, services = _service()
    await _enrolled(store, services.registry)
    store.identity_holds.add((PERSON, "generated-hold"))
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert (await store.person(PERSON)).display_name == OLDER_NAME


async def test_a_kratos_outage_leaves_an_incomplete_journal_reconcile_finishes():
    registry = _FailOnceRegistry()
    store, _, services = _service(registry=registry)
    await _enrolled(store, registry)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    assert record.steps["kratos"] == "pending"
    assert (await store.person(PERSON)).display_name == OLDER_NAME

    finished = await services.reconcile()
    assert [row.operation_id for row in finished] == ["generated-op-1"]
    assert (await store.person(PERSON)).display_name == NEW_NAME
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE


async def test_the_runtime_loop_recovers_a_profile_without_a_collection():
    class NotifyingRegistry(_FailOnceRegistry):
        def __init__(self):
            super().__init__()
            self.recovered = asyncio.Event()

        async def set_display_name(self, **kwargs):
            result = await super().set_display_name(**kwargs)
            if result.status is OutcomeStatus.COMPLETE:
                self.recovered.set()
            return result

    registry = NotifyingRegistry()
    store, _, services = _service(registry=registry)
    await _enrolled(store, registry)
    outcome = await services.profile.update_display_name(
        await _authentication(store), operation_id="generated-runtime-recovery",
        display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    task = asyncio.create_task(services.run_reconciliation(interval_seconds=3600))
    try:
        await asyncio.wait_for(registry.recovered.wait(), timeout=2)
        assert (await store.person(PERSON)).display_name == NEW_NAME
        record = await store.operation("generated-runtime-recovery")
        assert record.state is OperationJournalState.COMPLETE
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_an_unreadable_recovery_owner_does_not_starve_the_other_owners():
    class JournalOutage(FakeBindingStore):
        fail_read = False

        async def incomplete_operations(self):
            if self.fail_read:
                self.fail_read = False
                raise RuntimeError("generated journal read outage")
            return await super().incomplete_operations()

    store = JournalOutage()
    store, registry, services = _service(store, _FailOnceRegistry())
    await _enrolled(store, registry)
    outcome = await services.profile.update_display_name(
        await _authentication(store), operation_id="generated-owner-outage",
        display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    store.fail_read = True
    finished = await services.reconcile()
    assert [row.operation_id for row in finished] == ["generated-owner-outage"]
    assert (await store.person(PERSON)).display_name == NEW_NAME


async def test_a_projection_outage_reconciles_the_recorded_effect():
    """kratos patched but SQL failed: the journal owns the retry, and a
    FRESH service over the same store finishes exactly what was recorded —
    the restart shape."""
    store = _FailOnceStore()
    store, registry, services = _service(store)
    await _enrolled(store, registry)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.INCOMPLETE
    # The upstream effect landed; the projection did not — the journal
    # records exactly that, never "nothing changed".
    assert registry.identities[PERSON]["traits"]["display_name"] == NEW_NAME
    assert (await store.person(PERSON)).display_name == OLDER_NAME

    recovered = ProfileService(store, registry, services.publication)
    finished = await recovered.reconcile()
    assert [row.operation_id for row in finished] == ["generated-op-1"]
    assert (await store.person(PERSON)).display_name == NEW_NAME


async def test_a_committed_global_name_with_failed_readback_reports_incomplete():
    class ReadbackMismatch(FakeBindingStore):
        mismatch = False

        async def set_display_name(self, **kwargs):
            result = await super().set_display_name(**kwargs)
            self.mismatch = True
            return result

        async def person(self, person_id):
            person = await super().person(person_id)
            if self.mismatch and person_id == PERSON:
                self.mismatch = False
                return replace(person, display_name=OLDER_NAME)
            return person

    store, registry, services = _service(ReadbackMismatch())
    await _enrolled(store, registry)
    result = await services.profile.update_display_name(
        await _authentication(store), operation_id="generated-readback-failure",
        display_name=NEW_NAME)
    assert result.status is OutcomeStatus.INCOMPLETE
    assert registry.identities[PERSON]["traits"]["display_name"] == NEW_NAME
    assert (await store.person(PERSON)).display_name == NEW_NAME
    record = await store.operation("generated-readback-failure")
    assert record.state is OperationJournalState.INCOMPLETE
    assert record.steps["projection"] == "done" and record.steps["readback"] == "pending"
    assert len(await services.reconcile()) == 1


async def test_a_replayed_journal_never_overwrites_a_newer_name():
    """The operation journaled "New" and patched Kratos, but a later intent
    renamed the person to "Newer" before reconciliation ran. The replay
    must refuse rather than revert the newer intent."""
    store = _FailOnceStore()
    store, registry, services = _service(store)
    await _enrolled(store, registry)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.INCOMPLETE

    newer = "Generated Even Newer Name"
    registry.identities[PERSON]["traits"]["display_name"] = newer
    await store.set_display_name(person_id=PERSON, display_name=newer)

    finished = await services.profile.reconcile()
    assert finished == ()  # resolved denied, not completed
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    assert record.outcome == "denied"
    assert (await store.person(PERSON)).display_name == newer
    assert registry.identities[PERSON]["traits"]["display_name"] == newer


async def test_an_older_replay_never_writes_over_a_later_journaled_intent():
    """The journaled order is the intents' order: op-1 crashed BEFORE its
    kratos step ran, then op-2 renamed the person and completed. The
    reconcile must close op-1 superseded WITHOUT re-applying its older
    name — over neither the trait nor the projection."""
    registry = _FailOnceRegistry()
    store, _, services = _service(registry=registry)
    await _enrolled(store, registry)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("generated-op-1")
    assert record is not None and record.steps["kratos"] == "pending"

    latest_name = "Generated Latest Name"
    newer = await services.profile.update_display_name(
        authentication, operation_id="generated-op-2",
        display_name=latest_name)
    assert newer.status is OutcomeStatus.COMPLETE

    finished = await services.profile.reconcile()
    assert finished == ()
    record = await store.operation("generated-op-1")
    assert record is not None
    assert record.state is OperationJournalState.COMPLETE
    assert record.outcome == "denied" and record.detail == "superseded"
    person = await store.person(PERSON)
    assert person is not None and person.display_name == latest_name
    assert registry.identities[PERSON]["traits"]["display_name"] == latest_name


async def test_preferences_bindings_memberships_and_grants_survive_a_rename():
    store, registry, services = _service()
    backend = services.backend
    await _enrolled(store, registry)
    await backend.put_relation(scope=ROOM, relation="grant_manage", person_id=PERSON)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.COMPLETE

    record = await store.record_for(account=ACCT)
    assert record is not None and record.state is AccountState.BOUND
    assert record.account_id == ACCOUNT_ID
    memberships = await store.memberships_for(PERSON)
    assert [m.scope for m in memberships] == [ROOM]
    assert memberships[0].state is MembershipState.MEMBER
    rows = await backend.relations_on(scope=ROOM)
    assert ("grant_manage", PERSON) in rows
    person = await store.person(PERSON)
    assert person is not None and person.kratos_identity_id is not None
    assert person.lifecycle is PersonLifecycle.ACTIVE


async def test_a_blank_name_refuses_before_any_effect():
    store, _, services = _service()
    await _enrolled(store, services.registry)
    authentication = await _authentication(store)
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name="   ")
    assert outcome.status is OutcomeStatus.DENIED
    assert await store.operation("generated-op-1") is None


async def test_the_file_backend_refuses_authoritatively():
    """identity.toml is operator-maintained: there is no session binding to
    revalidate, so the request is denied before any managed state exists —
    the route must never pretend a file deployment can take the write."""
    from nora.domain.models import PersonRef
    from nora.people.adapters.file_backend import (
        FileBindingStore,
        FileIdentityRegistry,
    )
    from nora.persons import Persons

    persons = Persons(
        by_person={PERSON: PersonRef(
            person_id=PERSON, name=OLDER_NAME, honcho_peer_id="generated-peer")},
        index={})
    services = ManagedServices(
        FileBindingStore(persons), FakeAuthorityBackend(),
        FakeTopologySource(), FileIdentityRegistry(), FakeLinkRequests())
    authentication = await _authentication(
        FakeBindingStore(), account=ACCT,
        binding=SessionBinding(PERSON, "0", ACCOUNT_ID, "0"))
    outcome = await services.profile.update_display_name(
        authentication, operation_id="generated-op-1", display_name=NEW_NAME)
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "authentication-refused"
