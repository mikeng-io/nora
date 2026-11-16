"""Managed membership, social preferences and failure semantics at the
memory consumer, without I/O."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from nora.access import IdentityAccess, SocialPreferenceUnavailableError
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
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeTopologySource,
)
from nora.testing.memory_index import FakeMemoryIndex

PERSON = "generated-person-a"
FOREIGN = "generated-person-b"
FORMER = "generated-person-c"
HERE = RoomFacts(
    "signal", frozenset({"generated-room-a"}), "generated-unit-a", frozenset({FOREIGN})
)
THERE = RoomFacts(
    "signal", frozenset({"generated-room-b"}), "generated-unit-b", frozenset({PERSON})
)
ROOM = Scope(ScopeKind.ROOM, "signal/generated-room-a")
NOW = 1_790_000_000_000
TARGET = RenderRoom(room=HERE, rooms=(HERE, THERE), live=LiveRoom(frozenset({PERSON})))


async def _world(
    *,
    social: dict[str, Any] | None = None,
    members: tuple[str, ...] = (PERSON,),
    bindings: FakeBindingStore | None = None,
):
    """Seed explicit preferences, or omit selected traits to exercise malformed records."""
    if social is None:
        social = {PERSON: True, FOREIGN: True}
    if bindings is None:
        bindings = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM.id: (HERE.unit, False), "signal/generated-room-b": (THERE.unit, False)},
        units={HERE.unit: "generated-workspace", THERE.unit: "generated-workspace"},
        workspaces={"generated-workspace"},
    )
    registry = FakeIdentityRegistry()
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
        if social is not None:
            traits: dict[str, Any] = {"display_name": person}
            if person in social:
                traits["social"] = social[person]
            outcome, _ = await registry.ensure_identity(
                external_id=person, traits=traits
            )
            assert outcome.status is OutcomeStatus.COMPLETE
    for member in members:
        assert (await bindings.set_membership(
            person_id=member, scope=ROOM, member=True, source="generated-source"
        )).status is OutcomeStatus.COMPLETE
    access = IdentityAccess(
        bindings=bindings, authority=AuthorityService(bindings, backend, topology),
        topology=topology, registry=registry,
    )
    return bindings, access, registry


def _social_target() -> RenderRoom:
    """A room whose configured roster matches the managed members."""
    here = RoomFacts(
        "signal", frozenset({"generated-room-a"}), "generated-unit-a",
        frozenset({PERSON, FOREIGN}),
    )
    return RenderRoom(
        room=here, rooms=(here,), live=LiveRoom(frozenset({PERSON, FOREIGN}))
    )


async def test_memory_membership_comes_from_managed_state_and_refreshes_after_departure():
    bindings, access, _ = await _world()
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
    _, access, _ = await _world()
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
    bindings, access, _ = await _world()
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


async def test_managed_alias_refresh_uses_the_callable_person_directory():
    bindings, access, _ = await _world()
    assert (await bindings.add_alias(
        former_id="generated-retired-person", person_id=PERSON
    )).status is OutcomeStatus.COMPLETE
    await access.refresh_aliases(ttl_seconds=0)
    assert access.subject_id_for("generated-retired-person") == PERSON
    assert access.former_ids_for(PERSON) == ("generated-retired-person",)


async def test_a_social_opt_out_excludes_social_items_but_keeps_enabled_and_non_social():
    """The preference read feeds the real scope filter: the opted-out
    person's social node never returns, while the enabled person's social
    node and the opted-out person's non-social node still do."""
    _, access, _ = await _world(
        social={PERSON: False, FOREIGN: True}, members=(PERSON, FOREIGN))
    target = _social_target()
    index = FakeMemoryIndex([
        IndexedNode("social", "generated-opted-out", "generated picnic post", NOW,
                    persons=(PERSON,)),
        IndexedNode("social", "generated-enabled", "generated picnic post", NOW,
                    persons=(FOREIGN,)),
        IndexedNode("message", "generated-opted-out-chat", "generated picnic chat",
                    NOW, persons=(PERSON,), origin_room="signal:generated-room-a"),
    ])
    social_off = await access.social_off_ids(target.room.persons or ())
    assert social_off == {PERSON}
    search = MemorySearch(index, Metrics(), access=access)
    result = await search.search(
        query="generated picnic", person_id=None, target=target,
        now_ms=NOW, social_off=social_off)
    assert result.status in {"ok", "partial"}
    assert {hit.node.key for hit in result.hits} == {
        ("social", "generated-enabled"), ("message", "generated-opted-out-chat")}


def _graph_composition(persons, access, cfg):
    from types import SimpleNamespace

    from nora.memory.wire import graph as wire_graph
    from tests.builders import override_config

    allowlist = SimpleNamespace(
        rooms=lambda: [
            SimpleNamespace(
                platform="signal", primary_id="generated-room-a",
                unit=HERE.unit, persons=frozenset({PERSON}), location=False)
        ],
        home_rooms=lambda: frozenset(),
    )
    cfg = override_config(cfg, {
        "memory.graph.enabled": True, "memory.graph.comprehend.enabled": True,
        "cognition.enabled": True,
    })
    return wire_graph(
        allowlist=allowlist, cfg=cfg, content_checks=False, engine=object(),
        media_join=SimpleNamespace(), metrics=Metrics(), persons=persons,
        presence_circles=(), profile_index=False,
        registry=SimpleNamespace(self_aliases=lambda: frozenset()),
        rosters={}, route_model=lambda occasion, model: "generated-model",
        tier=SimpleNamespace(model="generated-model"),
        unit_scopes=lambda: (HERE.unit,), access=access)


async def test_graph_composition_wires_and_starts_under_the_managed_backend(example_config):
    from nora.access import EMPTY_PERSONS

    _, access, _ = await _world()
    wired = _graph_composition(EMPTY_PERSONS, access, example_config)
    assert wired.cognition_memory is not None


@pytest.mark.parametrize("provider", ["web_search", "instagram_search", "x_search"])
async def test_managed_source_names_are_screened_live_before_external_reads(
    example_config, provider,
):
    from dataclasses import replace

    from nora.access import EMPTY_PERSONS
    from nora.agent.comprehend_tools import ComprehendRead, LookupState
    from nora.agent.fetch_watch import GraphFetched, _result_of
    from nora.agent.ports import ToolSpec, adapt
    from nora.domain.memory_graph import Source
    from nora.testing.comprehend import InMemoryComprehendRecords

    class ManagedRecords(InMemoryComprehendRecords):
        def __init__(self, access):
            super().__init__()
            self.access = access

    bindings, access, _ = await _world()
    bindings.people[PERSON] = replace(bindings.people[PERSON], display_name="Generated Person")
    memory = _graph_composition(EMPTY_PERSONS, access, example_config).cognition_memory
    records = ManagedRecords(memory.records.access)
    state = LookupState(
        GraphFetched(), HERE.unit, 1, records, Metrics(), memory.private_names,
        persons=(PERSON,),
    )
    calls = []

    async def outbound(query: str) -> dict:
        calls.append(query)
        return {"status": "ok", "text": query}

    tool = ComprehendRead(adapt(ToolSpec(
        name=provider, description="", schema={}, call=outbound,
    )), state)

    async def call(query):
        events = [event async for event in tool.stream({
            "toolUseId": "generated-lookup", "name": provider, "input": {"query": query},
        }, {})]
        return _result_of(events[-1])

    private = await call("Generated Person news")
    assert private["status"] == "refused"
    assert private["reason"] == "private_person"
    assert calls == []
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news"]
    bindings.people[FOREIGN] = replace(bindings.people[FOREIGN], display_name="Generated Later")
    state.fetched.show(Source(
        ("message", "generated-later-row"), "record", HERE.unit, (FOREIGN,),
        "A generated later transcript read.",
    ))
    later = await call("Generated Later news")
    assert later["status"] == "refused"
    assert later["reason"] == "private_person"
    assert calls == ["volleyball news"]
    bindings.people[PERSON] = replace(bindings.people[PERSON], display_name="Generated Renamed")
    renamed = await call("Generated Renamed news")
    assert renamed["status"] == "refused"
    assert renamed["reason"] == "private_person"
    bindings.down = True
    assert (await call("volleyball news"))["status"] == "unavailable"
    bindings.down = False
    state.persons = ("generated-unknown-person",)
    assert (await call("volleyball news"))["status"] == "unavailable"
    assert calls == ["volleyball news"]
    state.persons = (PERSON,)
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news", "volleyball news"]


async def _screened_lookup(example_config, access, *, provider="web_search",
                           persons=(PERSON,)):
    """A `ComprehendRead` over `provider` as the managed session wires it."""
    from nora.access import EMPTY_PERSONS
    from nora.agent.comprehend_tools import ComprehendRead, LookupState
    from nora.agent.fetch_watch import GraphFetched, _result_of
    from nora.agent.ports import ToolSpec, adapt
    from nora.testing.comprehend import InMemoryComprehendRecords

    class ManagedRecords(InMemoryComprehendRecords):
        def __init__(self, access):
            super().__init__()
            self.access = access

    memory = _graph_composition(EMPTY_PERSONS, access, example_config).cognition_memory
    state = LookupState(
        GraphFetched(), HERE.unit, 1, ManagedRecords(memory.records.access),
        Metrics(), memory.private_names, persons=persons,
    )
    calls: list[str] = []

    async def outbound(query: str) -> dict:
        calls.append(query)
        return {"status": "ok", "text": query}

    tool = ComprehendRead(adapt(ToolSpec(
        name=provider, description="", schema={}, call=outbound,
    )), state)

    async def call(query):
        events = [event async for event in tool.stream({
            "toolUseId": "generated-lookup", "name": provider,
            "input": {"query": query},
        }, {})]
        return _result_of(events[-1])

    return call, calls, state


@pytest.mark.parametrize("blank", ["", "   "])
async def test_a_blank_managed_source_name_makes_screened_reads_unavailable(
    example_config, blank,
):
    """An empty authoritative display name is no name at all: the screen
    cannot prove its privacy boundary, so the read is `unavailable` before
    the provider runs — never silently reduced to the person id the
    presentation fallback would render."""
    from dataclasses import replace

    bindings, access, _ = await _world()
    bindings.people[PERSON] = replace(bindings.people[PERSON], display_name=blank)
    call, calls, _ = await _screened_lookup(example_config, access)

    assert (await call("volleyball news"))["status"] == "unavailable"
    assert calls == []
    bindings.people[PERSON] = replace(
        bindings.people[PERSON], display_name="Generated Person")
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news"]


@pytest.mark.parametrize("retired", ["generated-retired-a", "generated-retired-b"])
async def test_a_retired_id_of_a_source_person_is_screened_before_external_reads(
    example_config, retired,
):
    """Every retired id the canonical person now answers for is private the
    way its current id is: a query carrying any of them refuses before the
    provider runs."""
    bindings, access, _ = await _world()
    for former in ("generated-retired-a", "generated-retired-b"):
        assert (await bindings.add_alias(
            former_id=former, person_id=PERSON
        )).status is OutcomeStatus.COMPLETE
    call, calls, _ = await _screened_lookup(example_config, access)

    result = await call(f"lookup {retired}")
    assert result["status"] == "refused"
    assert result["reason"] == "private_person"
    assert calls == []
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news"]


async def test_a_source_recorded_under_a_retired_id_is_screened_by_its_survivor(
    example_config,
):
    """A source person whose rows carry a retired id is still that person:
    its current name and its OTHER retired ids screen just the same."""
    from dataclasses import replace

    bindings, access, _ = await _world()
    for former in ("generated-retired-a", "generated-retired-b"):
        assert (await bindings.add_alias(
            former_id=former, person_id=PERSON
        )).status is OutcomeStatus.COMPLETE
    bindings.people[PERSON] = replace(
        bindings.people[PERSON], display_name="Generated Person")
    call, calls, _ = await _screened_lookup(
        example_config, access, persons=("generated-retired-a",))

    named = await call("Generated Person news")
    assert named["status"] == "refused" and named["reason"] == "private_person"
    other = await call("lookup generated-retired-b")
    assert other["status"] == "refused" and other["reason"] == "private_person"
    assert calls == []
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news"]


async def test_an_alias_read_outage_makes_the_screen_unavailable_not_partial(
    example_config,
):
    """A former-id read that fails is an authority failure: the screen
    cannot claim it covered retired ids, so the read is `unavailable` —
    never a screen with aliases silently omitted."""

    class AliasDownBindings(FakeBindingStore):
        def __init__(self) -> None:
            super().__init__()
            self.former_down = False

        async def former_persons(self, person_id):
            if self.former_down:
                raise RuntimeError("alias read unavailable")
            return await super().former_persons(person_id)

    bindings = AliasDownBindings()
    _, access, _ = await _world(bindings=bindings)
    call, calls, _ = await _screened_lookup(example_config, access)

    bindings.former_down = True
    assert (await call("volleyball news"))["status"] == "unavailable"
    assert calls == []
    bindings.former_down = False
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news"]


async def test_a_source_person_exposed_mid_lookup_is_screened_before_the_provider_runs(
    example_config,
):
    """A concurrent read's `on_rows` hands `fetched` a new source person
    while the identity reads await; the re-scan must find and screen it, so
    its private name never reaches the provider."""
    from dataclasses import replace

    from nora.domain.memory_graph import Source

    started, release = asyncio.Event(), asyncio.Event()

    class GatedBindings(FakeBindingStore):
        async def person(self, person_id):
            if not started.is_set():
                started.set()
                await release.wait()
            return await super().person(person_id)

    bindings, access, _ = await _world(bindings=GatedBindings())
    bindings.people[FOREIGN] = replace(
        bindings.people[FOREIGN], display_name="Generated Later")
    call, calls, state = await _screened_lookup(example_config, access)

    task = asyncio.create_task(call("Generated Later news"))
    try:
        await asyncio.wait_for(started.wait(), 10)
        # The sibling read tool's `on_rows` callback lands its source here,
        # inside the suspended identity read — the concurrent-executor path.
        state.fetched.show(Source(
            ("message", "generated-mid-row"), "record", HERE.unit, (FOREIGN,),
            "A generated mid-read transcript row.",
        ))
    finally:
        release.set()
    result = await task
    assert result["status"] == "refused"
    assert result["reason"] == "private_person"
    assert calls == []
    assert (await call("volleyball news"))["status"] == "ok"
    assert calls == ["volleyball news"]


async def test_file_graph_keeps_opt_outs_when_no_access_backend_is_supplied(example_config):
    from nora.domain.models import PersonRef
    from nora.persons import Persons

    persons = Persons(
        by_person={PERSON: PersonRef(
            person_id=PERSON, name="Generated Person", honcho_peer_id=PERSON)},
        index={}, _social_off=frozenset({PERSON}),
    )
    wired = _graph_composition(persons, None, example_config)
    assert wired.cognition_memory is not None
    assert await wired.cognition_memory.records._social_off((PERSON,)) == {PERSON}
    assert FOREIGN not in await wired.cognition_memory.records._social_off((FOREIGN,))


async def test_an_opted_out_retired_identity_still_excludes_the_survivors_social_items():
    """Linking retires the former's Kratos identity; its recorded opt-out
    stays effective on every id the folded person may be recorded under —
    the read never reactivates the retired record to ask."""
    bindings, access, registry = await _world(
        social={PERSON: True, FOREIGN: True}, members=(PERSON, FOREIGN))
    account = ManagedAccount(
        account_id=str(uuid4()), person_id=FORMER, platform="signal",
        identifiers=frozenset(
            {Identifier("signal", "uuid", "generated-account-c")}),
        state=AccountState.BOUND,
    )
    assert (await bindings.register(
        person=ManagedPerson(person_id=FORMER, lifecycle=PersonLifecycle.ACTIVE),
        account=account, membership=None,
    )).status is OutcomeStatus.COMPLETE
    outcome, _ = await registry.ensure_identity(
        external_id=FORMER, traits={"display_name": FORMER, "social": False}
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert (await bindings.consolidate(survivor=PERSON, former=FORMER)
            ).status is OutcomeStatus.COMPLETE
    assert (await registry.set_state(external_id=FORMER, active=False)
            ).status is OutcomeStatus.COMPLETE
    assert registry.identities[FORMER]["state"] == "inactive"

    assert await access.social_off_ids({PERSON, FOREIGN}) == {PERSON, FORMER}
    assert await access.social_off(PERSON) is True
    assert await access.social_off(FORMER) is True

    target = _social_target()
    index = FakeMemoryIndex([
        IndexedNode("social", "generated-survivor", "generated picnic post", NOW,
                    persons=(PERSON,)),
        IndexedNode("social", "generated-enabled", "generated picnic post", NOW,
                    persons=(FOREIGN,)),
    ])
    search = MemorySearch(index, Metrics(), access=access)
    result = await search.search(
        query="generated picnic", person_id=None, target=target,
        now_ms=NOW, social_off=await access.social_off_ids(target.room.persons or ()))
    assert {hit.node.key for hit in result.hits} == {("social", "generated-enabled")}


@pytest.mark.parametrize("broken", ["missing", "malformed"])
async def test_a_missing_or_malformed_social_preference_is_unavailable_never_inferred(broken):
    """An identity without a `social` trait — or with a truthy non-boolean
    one — is `unavailable` to the read, never silently enabled."""
    social: dict[str, Any] = {PERSON: True}
    if broken == "malformed":
        social[FOREIGN] = "yes"
    _, access, _ = await _world(social=social, members=(PERSON, FOREIGN))
    with pytest.raises(SocialPreferenceUnavailableError):
        await access.social_off_ids({PERSON, FOREIGN})
    # The intact person's read still answers — the broken one refuses.
    assert await access.social_off_ids({PERSON}) == frozenset()


async def test_an_unknown_listed_person_is_unavailable_never_treated_as_enabled():
    _, access, _ = await _world(social={PERSON: True}, members=(PERSON,))
    with pytest.raises(SocialPreferenceUnavailableError):
        await access.social_off_ids({PERSON, "generated-stranger"})
    assert await access.social_off_ids({PERSON}) == frozenset()


async def test_a_social_preference_outage_is_unavailable_and_recovery_restores_the_read():
    bindings, access, registry = await _world(
        social={PERSON: False}, members=(PERSON,))
    registry.down = True
    with pytest.raises(SocialPreferenceUnavailableError):
        await access.social_off_ids({PERSON})
    registry.down = False
    assert await access.social_off_ids({PERSON}) == {PERSON}
    bindings.down = True
    with pytest.raises(RuntimeError):
        await access.social_off_ids({PERSON})
    bindings.down = False
    assert await access.social_off_ids({PERSON}) == {PERSON}


async def test_a_whole_roster_opt_out_read_is_unavailable_not_partial():
    """`social_off_ids(None)` asks for every canonical AND retired person;
    the roster contract enumerates ACTIVE people only, so the read refuses
    rather than answering a partial set that would look safe."""
    _, access, _ = await _world(social={PERSON: False}, members=(PERSON,))
    with pytest.raises(SocialPreferenceUnavailableError):
        await access.social_off_ids()
    # The named-persons path is unaffected — it never had the gap.
    assert await access.social_off_ids({PERSON}) == {PERSON}


async def test_memory_walk_checks_managed_opt_out_after_replacing_stale_file_roster():
    _, access, _ = await _world(
        social={PERSON: False, FOREIGN: True}, members=(PERSON, FOREIGN))
    # The room config predates managed registration and names only FOREIGN.
    assert TARGET.room.persons == {FOREIGN}
    caller_opt_out = await access.social_off_ids(TARGET.room.persons)
    assert caller_opt_out == frozenset()
    index = FakeMemoryIndex([
        IndexedNode("social", "generated-new-opt-out", "generated picnic post", NOW,
                    persons=(PERSON,)),
        IndexedNode("social", "generated-enabled", "generated picnic post", NOW,
                    persons=(FOREIGN,)),
        IndexedNode("message", "generated-opt-out-chat", "generated picnic chat", NOW,
                    persons=(PERSON,), origin_room="signal:generated-room-a"),
    ])
    result = await MemorySearch(index, Metrics(), access=access).search(
        query="generated picnic", person_id=None, target=TARGET,
        now_ms=NOW, social_off=caller_opt_out)
    assert result.status in {"ok", "partial"}
    assert {hit.node.key for hit in result.hits} == {
        ("social", "generated-enabled"), ("message", "generated-opt-out-chat")}
