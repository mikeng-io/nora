"""Exception recovery retains exact operation effects."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nora.domain.authentication import RoomAuthentication
from nora.domain.managed import OperationJournalState, OutcomeStatus
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.people.app.managed import ManagedServices, RegistrationService
from nora.people.app.management_invocation import HumanManagement
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from tests.unit.test_managed_registration import ACCT, ACCT_2, PERSON, _register_op


@pytest.mark.parametrize("caller", ["registration", "human"])
@pytest.mark.parametrize("partial", [False, True])
async def test_exception_recovery_attributes_only_the_exact_recorded_effect(caller, partial):
    class FailedGate(FakeBindingStore):
        gate_down = False
        journal_down = False

        def mutation(self):
            if self.gate_down:
                raise ConnectionError("generated gate failure")
            return super().mutation()

        async def operation(self, operation_id):
            if self.journal_down:
                raise ConnectionError("generated journal failure")
            return await super().operation(operation_id)

    store = FailedGate()
    registry = FakeIdentityRegistry()
    services = ManagedServices(store, FakeAuthorityBackend(), FakeTopologySource(),
                               registry, FakeLinkRequests())
    registration = RegistrationService(store, registry)
    original = _register_op("op-recovery")
    assert (await registration.apply(original)).status is OutcomeStatus.COMPLETE
    recorded = await store.operation(original.operation_id)
    assert recorded is not None
    if partial:
        store.operations[original.operation_id] = replace(
            recorded, state=OperationJournalState.INCOMPLETE)
    human = HumanManagement(store, registry, registration, services.authority,
                            services.scoped_profile)
    authentication = RoomAuthentication(
        issuer="signal", message_key=original.operation_id, account=ACCT,
        authenticated_at=datetime(2026, 1, 1, tzinfo=UTC))

    async def invoke(op):
        if caller == "human":
            op = replace(op, actor=Actor(ActorKind.OPERATOR, person_id=PERSON,
                                         scope=op.payload.scope))
            return await human.apply(op, authentication)
        return await registration.apply(op)

    store.gate_down = True
    exact = await invoke(original)
    assert exact.status is (OutcomeStatus.INCOMPLETE if partial else OutcomeStatus.COMPLETE)
    for changed in (
        _register_op(original.operation_id, person_id="generated-other"),
        _register_op(original.operation_id, identifiers=frozenset({ACCT_2})),
        replace(original, payload=replace(original.payload,
                                         scope=Scope(ScopeKind.ROOM, "signal/other"))),
    ):
        outcome = await invoke(changed)
        assert outcome.status is OutcomeStatus.DENIED
        assert outcome.detail == "operation-conflict"
    store.journal_down = True
    assert (await invoke(original)).status is OutcomeStatus.UNAVAILABLE
    assert len(store.people) == len(store.accounts) == 1
    assert store.operations[original.operation_id].snapshot == recorded.snapshot

