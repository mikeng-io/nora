"""`sync_membership` admission: the ONE producer-time freshness policy and
the durable per-source ordering mark, both inside the shared mutation gate
(`management.md` «Platform discovery and membership»):

- every roster acts only on the instant the producer stamped itself —
  absent, stale or future-dated evidence is `unavailable` and preserves
  membership, never a successful empty synchronisation;
- the newest accepted (service, collection, scope) mark refuses an older
  instant outright and a same-instant DIFFERENT effect as conflicting,
  while an exact same-instant replay answers its recorded outcome;
- a newer accepted observation — a partial one included — orphans an
  older journaled roster's pending steps, which close as superseded
  rather than retracting membership nobody still believes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    MembershipState,
    ObservationFreshness,
    OperationJournalState,
    Outcome,
    OutcomeStatus,
    PersonLifecycle,
    Verdict,
)
from nora.domain.management import (
    Actor,
    ActorKind,
    Operation,
    RosterSync,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.people.app.managed import AuthorityService
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeTopologySource,
)

WS = Scope(ScopeKind.WORKSPACE, "generated-ws")
UNIT = Scope(ScopeKind.UNIT, "generated-unit")
ROOM = Scope(ScopeKind.ROOM, "signal/generated-conv")
MEMBER = Identifier(platform="signal", kind="uuid", value="generated-member")
NOW = 1_760_000_000_000


class _DropsFail(FakeAuthorityBackend):
    """A backend that refuses tuple REMOVES — the mid-sync outage that
    leaves a journaled roster incomplete."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_drops = False

    async def drop_relation(
        self, *, scope: Scope, relation: str, person_id: str
    ) -> Outcome:
        if self.fail_drops:
            return Outcome(OutcomeStatus.UNAVAILABLE, "down")
        return await super().drop_relation(
            scope=scope, relation=relation, person_id=person_id
        )


def _service(
    backend: FakeAuthorityBackend | None = None,
) -> tuple[FakeBindingStore, FakeAuthorityBackend, AuthorityService]:
    store = FakeBindingStore()
    backend = backend or FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (UNIT.id, False)},
        units={UNIT.id: WS.id},
        workspaces={WS.id},
    )
    # The constructor clock seam: admission policy pinned to NOW so a
    # "stale" or "future" instant is exact arithmetic, not a race.
    freshness = ObservationFreshness(
        max_age_ms=60_000, wall_clock=lambda: NOW / 1000)
    return store, backend, AuthorityService(store, backend, topology, freshness)


async def _person(
    store: FakeBindingStore, person_id: str,
    ident: Identifier = MEMBER,
) -> None:
    await store.register(
        person=ManagedPerson(person_id=person_id, lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id=str(uuid4()), person_id=person_id,
            platform="signal", identifiers=frozenset({ident}),
            state=AccountState.BOUND,
        ),
        membership=None,
    )


async def _member(
    store: FakeBindingStore, backend: FakeAuthorityBackend,
    person_id: str = "person_member",
    source: str = 'sync:["generated-bridge","generated-roster"]',
    scope: Scope = ROOM,
) -> None:
    await _person(store, person_id)
    await store.set_membership(
        person_id=person_id, scope=scope, member=True, source=source)
    await backend.put_relation(scope=scope, relation="member", person_id=person_id)


def _roster(
    operation_id: str, observed_ms: int, *, members=(),
    complete: bool = True, collection: str = "generated-roster",
    scope: Scope = ROOM, service: str = "generated-bridge",
) -> Operation:
    return Operation(
        operation_id=operation_id, kind="sync_membership",
        actor=Actor(ActorKind.SERVICE, service_id=service,
                    sources=frozenset({"signal"})),
        target=Target(kind=TargetKind.SCOPE, scope=scope),
        payload=RosterSync(
            members=frozenset(members), complete=complete,
            collection=collection, observed_ms=observed_ms),
        observed_revisions={"authority": "0"},
    )


async def test_an_undated_roster_is_unavailable_and_never_departs():
    """`0` is not the epoch — it is evidence with no instant, and the gate
    refuses it before anything journals."""
    store, backend, svc = _service()
    await _member(store, backend)
    for complete in (True, False):
        outcome = await svc.apply(_roster("op-undated", 0, complete=complete))
        assert outcome.status is OutcomeStatus.UNAVAILABLE
        assert outcome.detail == "observation-undated"
    assert await store.operation("op-undated") is None
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER


async def test_a_stale_complete_roster_is_unavailable_and_never_departs():
    """Outside one interval a complete roster's absence claim is expired
    evidence — it preserves everyone it would have removed."""
    store, backend, svc = _service()
    await _member(store, backend)
    outcome = await svc.apply(
        _roster("op-stale", NOW - 61_000))
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert outcome.detail == "observation-stale"
    assert await store.operation("op-stale") is None
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER
    assert (await svc.check(
        scope=ROOM, person_id="person_member", permit="speak"
    )).verdict is Verdict.ALLOWED


async def test_a_future_roster_is_unavailable_on_every_shape():
    """A stamp beyond real clock disagreement never happened — refused
    whether the roster claims completeness or not."""
    store, backend, svc = _service()
    await _member(store, backend)
    for complete in (True, False):
        outcome = await svc.apply(_roster(
            f"op-future-{complete}", NOW + 61_000, members={MEMBER},
            complete=complete))
        assert outcome.status is OutcomeStatus.UNAVAILABLE
        assert outcome.detail == "observation-future"
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER


async def test_an_old_but_real_partial_still_adds():
    """A partial roster proves presence, not absence: any real past
    instant admits its add half — the ordering mark, not the age window,
    sequences it against later observations."""
    store, _, svc = _service()
    await _person(store, "person_member")
    outcome = await svc.apply(_roster(
        "op-partial", NOW - 600_000, members={MEMBER}, complete=False))
    assert outcome.status is OutcomeStatus.COMPLETE
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER


async def test_an_older_observation_never_supersedes_a_newer_one():
    """The durable mark wins over arrival order: a complete roster older
    than what the source already accepted is refused whole."""
    store, backend, svc = _service()
    await _member(store, backend)
    newer = await svc.apply(_roster(
        "op-newer", NOW - 10_000, members={MEMBER}))
    assert newer.status is OutcomeStatus.COMPLETE
    older = await svc.apply(_roster("op-older", NOW - 20_000, members=()))
    assert older.status is OutcomeStatus.UNAVAILABLE
    assert older.detail == "observation-superseded"
    assert await store.operation("op-older") is None
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER


async def test_a_newer_partial_supersedes_an_older_complete():
    """Completeness does not buy ordering: an add-only observation still
    moves the mark an older complete roster can no longer pass."""
    store, backend, svc = _service()
    await _member(store, backend)
    partial = await svc.apply(_roster(
        "op-partial", NOW - 10_000, members={MEMBER}, complete=False))
    assert partial.status is OutcomeStatus.COMPLETE
    older_complete = await svc.apply(
        _roster("op-older-complete", NOW - 20_000, members=()))
    assert older_complete.status is OutcomeStatus.UNAVAILABLE
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER


async def test_the_same_instant_with_different_content_is_a_conflict():
    """Equal instants are only a replay when the effect is identical —
    different content at the same instant is conflicting evidence."""
    store, _, svc = _service()
    await _person(store, "person_member")
    await _person(store, "person_other", Identifier(
        platform="signal", kind="uuid", value="generated-other"))
    accepted = await svc.apply(_roster(
        "op-accepted", NOW - 10_000, members={MEMBER}))
    assert accepted.status is OutcomeStatus.COMPLETE
    other = Identifier(platform="signal", kind="uuid", value="generated-other")
    conflict = await svc.apply(_roster(
        "op-conflict", NOW - 10_000, members={other}))
    assert conflict.status is OutcomeStatus.UNAVAILABLE
    assert conflict.detail == "observation-conflict"
    assert await store.operation("op-conflict") is None


async def test_an_exact_retry_of_the_same_instant_replays():
    """Same instant AND same effect is the definition of a retry — the
    recorded outcome answers, nothing double-applies."""
    store, _, svc = _service()
    await _person(store, "person_member")
    first = await svc.apply(_roster(
        "op-snapshot", NOW - 10_000, members={MEMBER}))
    assert first.status is OutcomeStatus.COMPLETE
    again = await svc.apply(_roster(
        "op-snapshot", NOW - 10_000, members={MEMBER}))
    assert again.status is OutcomeStatus.COMPLETE
    members = await store.members_of(ROOM)
    assert len(members) == 1


async def test_observation_marks_stay_inside_their_source():
    """(service, collection, scope) is the mark's whole key — a sibling
    service's, collection's or scope's history cannot refuse this one."""
    store, backend, svc = _service()
    await _member(store, backend)
    await svc.apply(_roster("op-anchor", NOW - 10_000, members={MEMBER}))
    # A different service, a different collection and a different scope
    # each see no mark at all — their rosters are judged on their own.
    foreign = await svc.apply(_roster(
        "op-foreign", NOW - 20_000, members={MEMBER},
        service="generated-other"))
    assert foreign.status is OutcomeStatus.COMPLETE
    other_collection = await svc.apply(_roster(
        "op-collection", NOW - 20_000, members={MEMBER},
        collection="generated-roster-two"))
    assert other_collection.status is OutcomeStatus.COMPLETE
    room_two = Scope(ScopeKind.ROOM, "signal/generated-conv-2")
    other_scope = await svc.apply(_roster(
        "op-scope", NOW - 20_000, members={MEMBER}, scope=room_two))
    # The unmapped room is refused for scope reasons, never supersession.
    assert other_scope.status is OutcomeStatus.DENIED


async def test_an_interrupted_roster_replays_as_superseded_after_a_newer_one():
    """A journaled older roster loses its warrant once newer evidence is
    accepted — reconcile closes it superseded instead of finishing the
    stale departure."""
    store, backend, svc = _service(backend=_DropsFail())
    await _member(store, backend)
    assert isinstance(backend, _DropsFail)
    backend.fail_drops = True
    interrupted = await svc.apply(_roster("op-interrupted", NOW - 20_000))
    assert interrupted.status is OutcomeStatus.UNAVAILABLE
    record = await store.operation("op-interrupted")
    assert record is not None
    assert record.state is OperationJournalState.INCOMPLETE
    backend.fail_drops = False
    # A newer observation of the same source is accepted meanwhile.
    newer = await svc.apply(_roster(
        "op-newer", NOW - 10_000, members={MEMBER}))
    assert newer.status is OutcomeStatus.COMPLETE
    replayed = await svc.apply_replay(record)
    assert replayed.status is OutcomeStatus.COMPLETE
    assert replayed.detail == "superseded"
    closed = await store.operation("op-interrupted")
    assert closed is not None
    assert closed.state is OperationJournalState.COMPLETE
    members = await store.members_of(ROOM)
    assert members[0].state is MembershipState.MEMBER
    # The abandoned departure's block dies with it — the member who never
    # left keeps answering for themselves.
    assert await store.authority_blocks() == ()
    assert (await svc.check(
        scope=ROOM, person_id="person_member", permit="speak"
    )).verdict is Verdict.ALLOWED


@pytest.mark.parametrize("elapsed_ms", [0, 120_000])
async def test_reconstructed_roster_rechecks_freshness_before_departure(elapsed_ms):
    store, backend, initial = _service(backend=_DropsFail())
    await _member(store, backend)
    backend.fail_drops = True
    interrupted = await initial.apply(_roster("op-aging", NOW - 20_000))
    assert interrupted.status is OutcomeStatus.UNAVAILABLE
    journal = await store.operation("op-aging")
    assert journal is not None
    assert await store.members_of(ROOM)
    backend.fail_drops = False
    current = AuthorityService(
        store, backend,
        FakeTopologySource(rooms={ROOM.id: (UNIT.id, False)},
                           units={UNIT.id: WS.id}, workspaces={WS.id}),
        ObservationFreshness(max_age_ms=60_000,
                             wall_clock=lambda: (NOW + elapsed_ms) / 1000),
    )
    result = await current.apply_replay(journal)
    if elapsed_ms:
        assert result.status is OutcomeStatus.UNAVAILABLE
        assert result.detail == "observation-stale"
        assert await store.members_of(ROOM)
        assert (await store.operation("op-aging")).state is OperationJournalState.INCOMPLETE
    else:
        assert result.status is OutcomeStatus.COMPLETE
        assert not await store.members_of(ROOM)
