"""Direct membership withdrawal preserves independently supported access."""

import pytest

from nora.domain.managed import (
    MembershipState,
    OperationJournalState,
    OutcomeStatus,
    Verdict,
    unavailable,
)
from nora.domain.management import MembershipStanding, Target, TargetKind
from nora.people.app.managed_authority import AuthorityService
from nora.testing.managed import FakeAuthorityBackend, FakeBindingStore, FakeTopologySource
from tests.unit.test_managed_authority import (
    PERSON,
    ROOM,
    UNIT,
    WS,
    _grant_operator,
    _op,
    _person,
    _service,
)


@pytest.mark.parametrize("other_sources", [
    frozenset(),
    frozenset({"registration"}),
    frozenset({"sync:bridge:collection-a", "sync:bridge:collection-b"}),
    frozenset({"registration", "sync:bridge:collection-a", "sync:bridge:collection-b"}),
])
async def test_direct_withdrawal_preserves_every_other_membership_support(other_sources):
    store, backend, service = _service()
    await _person(store, PERSON)
    await _grant_operator(store, backend, "manage_members")
    for source in {"delegation"} | other_sources:
        await store.set_membership(person_id=PERSON, scope=ROOM, member=True, source=source)
    await store.set_membership(person_id=PERSON, scope=UNIT, member=True, source="registration")
    unit_before = next(m for m in await store.memberships_for(PERSON) if m.scope == UNIT)
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON)
    assert (await service.check(scope=ROOM, person_id=PERSON, permit="view")).allowed
    operation = _op("generated-direct-withdrawal", "set_membership",
        target=Target(TargetKind.MEMBERSHIP, person_id=PERSON, scope=ROOM),
        payload=MembershipStanding(member=False))
    assert (await service.apply(operation)).status is OutcomeStatus.COMPLETE
    memberships = {m.scope: m for m in await store.memberships_for(PERSON)}
    standing = memberships[ROOM]
    assert standing.sources == other_sources
    expected = MembershipState.MEMBER if other_sources else MembershipState.LEFT
    assert standing.state is expected
    assert memberships[UNIT] == unit_before
    relations = await backend.relations_on(scope=ROOM)
    assert (("member", PERSON) in relations) is bool(other_sources)
    permission = await service.check(scope=ROOM, person_id=PERSON, permit="view")
    assert permission.verdict is (Verdict.ALLOWED if other_sources else Verdict.DENIED)
    record = await store.operation(operation.operation_id)
    assert record is not None and record.state is OperationJournalState.COMPLETE
    replay = await service.apply(operation)
    assert replay.status is OutcomeStatus.COMPLETE and replay.detail == "replayed"
    assert {m.scope: m for m in await store.memberships_for(PERSON)} == memberships


class _WithdrawalFails(FakeBindingStore):
    def __init__(self):
        super().__init__()
        self.fail_withdrawal = True

    async def drop_membership_support(self, *, person_id, scope, source):
        if self.fail_withdrawal:
            return unavailable("injected-withdrawal-outage")
        return await super().drop_membership_support(
            person_id=person_id, scope=scope, source=source
        )


async def test_reconciliation_withdraws_only_direct_support_after_an_interrupted_effect():
    store = _WithdrawalFails()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False)}, units={UNIT.id: WS.id}, workspaces={WS.id}
    )
    service = AuthorityService(store, backend, topology)
    await _person(store, PERSON)
    await _grant_operator(store, backend, "manage_members")
    for source in ("delegation", "registration", "sync:bridge:collection-a"):
        await store.set_membership(person_id=PERSON, scope=ROOM, member=True, source=source)
    await backend.put_relation(scope=ROOM, relation="member", person_id=PERSON)
    operation = _op("generated-interrupted-withdrawal", "set_membership",
        target=Target(TargetKind.MEMBERSHIP, person_id=PERSON, scope=ROOM),
        payload=MembershipStanding(member=False))
    result = await service.apply(operation)
    assert result.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation(operation.operation_id)
    assert record is not None and record.state is OperationJournalState.INCOMPLETE
    assert (await service.check(scope=ROOM, person_id=PERSON, permit="view")
            ).verdict is Verdict.UNAVAILABLE
    store.fail_withdrawal = False
    resumed = AuthorityService(store, backend, topology)
    finished = await resumed.reconcile()
    assert [r.operation_id for r in finished] == [operation.operation_id]
    standing = next(m for m in await store.memberships_for(PERSON) if m.scope == ROOM)
    assert standing.sources == frozenset({"registration", "sync:bridge:collection-a"})
    assert standing.state is MembershipState.MEMBER
    assert (await resumed.check(scope=ROOM, person_id=PERSON, permit="view")).allowed
    before = standing
    backend.down = True
    refused = await resumed.apply(_op("generated-authority-outage", "set_membership",
        target=Target(TargetKind.MEMBERSHIP, person_id=PERSON, scope=ROOM),
        payload=MembershipStanding(member=False)))
    assert refused.status is OutcomeStatus.UNAVAILABLE
    assert next(m for m in await store.memberships_for(PERSON) if m.scope == ROOM) == before
