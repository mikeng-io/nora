"""A complete collection snapshot retracts only its own authenticated support."""

from dataclasses import replace

import pytest

from nora.domain.managed import MembershipState, OutcomeStatus, Verdict
from nora.domain.management import ActorKind, OperationError, RosterSync, Target, TargetKind
from tests.unit.test_managed_authority import (
    MEMBER_ID,
    ROOM,
    _DropsFail,
    _op,
    _person,
    _service,
)


def snapshot(key, *, service="bridge", collection="guild-a", members=(), complete=True):
    operation = _op(key, "sync_membership", actor_kind=ActorKind.SERVICE,
        target=Target(TargetKind.SCOPE, scope=ROOM),
        payload=RosterSync(frozenset(members), complete, collection))
    return replace(operation, actor=replace(operation.actor, service_id=service))


@pytest.mark.parametrize("service,collection", [
    ("bridge", "guild-b"), ("another-bridge", "guild-a"),
])
async def test_collection_departure_preserves_other_collection_or_services_membership(
    service, collection,
):
    store, _, authority = _service()
    await _person(store, "generated-member", MEMBER_ID)
    for op in (snapshot("join-a", members={MEMBER_ID}),
               snapshot("join-b", service=service, collection=collection, members={MEMBER_ID})):
        assert (await authority.apply(op)).status is OutcomeStatus.COMPLETE
    assert len((await store.members_of(ROOM))[0].sources) == 2
    assert (await authority.apply(snapshot("leave-a"))).status is OutcomeStatus.COMPLETE
    member = (await store.members_of(ROOM))[0]
    assert member.state is MembershipState.MEMBER
    assert len(member.sources) == 1
    assert (await authority.check(scope=ROOM, person_id="generated-member", permit="view")).allowed
    assert (await authority.apply(snapshot("leave-b", service=service,
                                         collection=collection))).status is OutcomeStatus.COMPLETE
    assert (await store.memberships_for("generated-member"))[0].state is MembershipState.LEFT
    assert (await authority.check(scope=ROOM, person_id="generated-member",
                                 permit="view")).verdict is Verdict.DENIED


async def test_source_survives_journal_replay_and_partial_roster_cannot_remove_it():
    backend = _DropsFail()
    store, _, authority = _service(backend)
    await _person(store, "generated-member", MEMBER_ID)
    joined = await authority.apply(snapshot("join", members={MEMBER_ID}))
    assert joined.status is OutcomeStatus.COMPLETE
    partial = await authority.apply(snapshot("partial", complete=False))
    assert partial.status is OutcomeStatus.COMPLETE
    assert (await store.members_of(ROOM))[0].state is MembershipState.MEMBER
    backend.fail_drops = True
    assert (await authority.apply(snapshot("leave"))).status is OutcomeStatus.UNAVAILABLE
    journal = await store.operation("leave")
    assert journal.snapshot["collection"] == "guild-a"
    assert journal.snapshot["sync_source"] == 'sync:["bridge","guild-a"]'
    backend.fail_drops = False
    assert await authority.reconcile()
    assert (await store.memberships_for("generated-member"))[0].state is MembershipState.LEFT
    assert (await authority.check(scope=ROOM, person_id="generated-member",
                                 permit="speak")).verdict is Verdict.DENIED


def test_collection_is_an_immutable_named_value():
    with pytest.raises(OperationError):
        RosterSync(frozenset(), True, " \t")


def test_roster_snapshot_has_stable_account_order_for_restart_retries():
    from nora.domain.identity import Identifier
    from nora.people.app.managed_ops import _op_snapshot

    operation = snapshot("generated-stable", members={
        Identifier("signal", "uuid", "generated-z"),
        Identifier("signal", "uuid", "generated-a"),
    })
    assert [row["value"] for row in _op_snapshot(operation)["members"]] == [
        "generated-a", "generated-z",
    ]
