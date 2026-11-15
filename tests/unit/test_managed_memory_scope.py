"""Managed membership and failure semantics at the memory consumer, without I/O."""

from uuid import uuid4

from nora.access import IdentityAccess
from nora.agent.memory_search import MemorySearch, scope_for_async
from nora.domain.identity import Identifier
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    OutcomeStatus,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.memory_index import IndexedNode
from nora.obs.metrics import Metrics
from nora.people.app.managed_authority import AuthorityService
from nora.testing.managed import FakeAuthorityBackend, FakeBindingStore, FakeTopologySource
from nora.testing.memory_index import FakeMemoryIndex

PERSON = "generated-person-a"
FOREIGN = "generated-person-b"
HERE = RoomFacts(
    "signal", frozenset({"generated-room-a"}), "generated-unit-a", frozenset({FOREIGN})
)
THERE = RoomFacts(
    "signal", frozenset({"generated-room-b"}), "generated-unit-b", frozenset({PERSON})
)
ROOM = Scope(ScopeKind.ROOM, "signal/generated-room-a")
NOW = 1_790_000_000_000
TARGET = RenderRoom(room=HERE, rooms=(HERE, THERE), live=LiveRoom(frozenset({PERSON})))


async def _world():
    bindings = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (HERE.unit, False), "signal/generated-room-b": (THERE.unit, False)},
        units={HERE.unit: "generated-workspace", THERE.unit: "generated-workspace"},
        workspaces={"generated-workspace"},
    )
    for person, value in ((PERSON, "generated-account-a"), (FOREIGN, "generated-account-b")):
        account = ManagedAccount(
            account_id=str(uuid4()), person_id=person, platform="signal",
            identifiers=frozenset({Identifier("signal", "uuid", value)}), state=AccountState.BOUND,
        )
        assert (await bindings.register(
            person=ManagedPerson(person_id=person, display_name=person,
                                 lifecycle=PersonLifecycle.ACTIVE), account=account,
            membership=None,
        )).status is OutcomeStatus.COMPLETE
    assert (await bindings.set_membership(
        person_id=PERSON, scope=ROOM, member=True, source="generated-source"
    )).status is OutcomeStatus.COMPLETE
    access = IdentityAccess(
        bindings=bindings, authority=AuthorityService(bindings, backend, topology),
        topology=topology,
    )
    return bindings, access


async def test_memory_membership_comes_from_managed_state_and_refreshes_after_departure():
    bindings, access = await _world()
    scope = await scope_for_async(TARGET, social_off=(), access=access)
    assert scope is not None
    assert scope.listed_here == {PERSON}
    assert scope.room_persons == {"signal:generated-room-a": frozenset({PERSON})}
    assert scope.units == (HERE.unit,)

    assert (await bindings.set_membership(
        person_id=PERSON, scope=ROOM, member=False, source="generated-source"
    )).status is OutcomeStatus.COMPLETE
    refreshed = await scope_for_async(TARGET, social_off=(), access=access)
    assert refreshed is not None and refreshed.listed_here == frozenset()
    assert FOREIGN not in refreshed.people_here


async def test_memory_search_reads_its_unit_and_refuses_matching_sibling_unit_content():
    _, access = await _world()
    index = FakeMemoryIndex([
        IndexedNode("message", "generated-visible", "generated apples", NOW,
                    origin_room="signal:generated-room-a"),
        IndexedNode("message", "generated-foreign", "generated apples", NOW,
                    origin_room="signal:generated-room-b"),
    ])
    search = MemorySearch(index, Metrics(), access=access)
    result = await search.search(query="apples", person_id=None, target=TARGET,
                                 now_ms=NOW, social_off=())
    assert result.status in {"ok", "partial"}
    assert {hit.node.key for hit in result.hits} == {("message", "generated-visible")}


async def test_memory_store_outage_is_unavailable_and_recovery_restores_the_positive_read():
    bindings, access = await _world()
    index = FakeMemoryIndex([
        IndexedNode("message", "generated-visible", "generated apples", NOW,
                    origin_room="signal:generated-room-a"),
    ])
    search = MemorySearch(index, Metrics(), access=access)

    async def read():
        return await search.search(query="apples", person_id=None, target=TARGET,
                                   now_ms=NOW, social_off=())

    positive = await read()
    assert {hit.node.key for hit in positive.hits} == {("message", "generated-visible")}
    bindings.down = True
    unavailable = await read()
    assert unavailable.status == "unavailable"
    bindings.down = False
    recovered = await read()
    assert recovered.status in {"ok", "partial"}
    assert {hit.node.key for hit in recovered.hits} == {("message", "generated-visible")}
