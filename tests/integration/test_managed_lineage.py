"""Current SQL identity and authenticated circle facts at disclosure callers."""

from dataclasses import replace
from uuid import uuid4

import pytest

from nora.agent.lineage import LineagePolicy, entry_permits, excluded
from nora.app.cognition_grounding import place_release
from nora.domain.identity import Identifier
from nora.domain.lineage import LineageEntry, LiveRoom, message
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    PersonLifecycle,
)
from nora.domain.management import Scope, ScopeKind
from nora.location.adapters.tools import build
from nora.policy import load_policy
from nora.testing.eval_backends import CannedRouteSource
from tests.integration.test_managed_presence import PERSON
from tests.integration.test_managed_presence import case as case
from tests.integration.test_managed_presence import location as location


@pytest.fixture
async def boundary(case, location, tmp_path):
    source = Scope(ScopeKind.ROOM, "signal/generated-source-room")
    backend = location.services.backend
    backend.tuples.add(("Room", location.room_scope.id, "member", PERSON))
    backend.tuples.add(("Room", source.id, "member", PERSON))
    location.services.topology.rooms[source.id] = ("generated-location-unit", False)
    await case.bindings.set_membership(person_id=PERSON, scope=source,
        member=True, source="generated-lineage")
    path = tmp_path / "generated-policy.toml"
    path.write_text('''[[conversations]]
platform="signal"
id="generated-source-room"
label="Generated Source"
unit="generated-location-unit"
persons=["generated-file-only"]
location=true
[[conversations]]
platform="signal"
id="generated-location-room"
label="Generated Target"
unit="generated-location-unit"
persons=["generated-file-only"]
location=true
[[conversations]]
platform="signal"
id="generated-sibling-room"
label="Generated Sibling"
unit="generated-sibling-unit"
persons="*"
location=true
''')
    return LineagePolicy.from_policy(load_policy(path), mode="on", access=location.access)


async def _target(boundary, person=PERSON):
    return await boundary.read_room(platform="signal", conversation_id="generated-location-room",
                                    live=LiveRoom(frozenset({person})))


async def test_cross_room_lineage_uses_current_sql_members_and_permission(case, location, boundary):
    entry = message("signal:generated-source-room")
    target = await _target(boundary)
    assert entry_permits(entry, target, origin=None)
    assert target.room.persons == frozenset({PERSON})
    assert len(target.rooms) == 2
    assert not entry_permits(message("signal:generated-sibling-room"), target, origin=None)
    location.services.backend.tuples.remove(
        ("Room", "signal/generated-source-room", "member", PERSON))
    assert not entry_permits(entry, await _target(boundary), origin=None)
    location.services.backend.tuples.add(("Room", "signal/generated-source-room", "member", PERSON))
    assert entry_permits(entry, await _target(boundary), origin=None)


async def test_directory_outage_withholds_presence_without_disabling_message_lineage(
    case, location, boundary,
):
    presence = LineageEntry(kind="presence", subject=PERSON)
    assert entry_permits(presence, await _target(boundary), origin=None)
    await location.server.close()
    target = await _target(boundary)
    assert not entry_permits(presence, target, origin=None)
    assert entry_permits(message("signal:generated-source-room"), target, origin=None)


async def test_authority_outage_never_substitutes_file_membership(case, location, boundary):
    entry = message("signal:generated-source-room")
    assert entry_permits(entry, await _target(boundary), origin=None)
    location.services.backend.down = True
    target = await _target(boundary)
    assert target.room.persons is None
    assert not entry_permits(entry, target, origin=None)
    location.services.backend.down = False
    assert entry_permits(entry, await _target(boundary), origin=None)


async def test_lineage_folds_retired_subject_and_exclusion_without_rewriting_provenance(
    case, location, boundary,
):
    survivor = "generated-lineage-survivor"
    await case.bindings.register(person=ManagedPerson(survivor, PersonLifecycle.ACTIVE,
        display_name="Generated Survivor"), account=ManagedAccount(str(uuid4()), survivor,
        "signal", frozenset({Identifier("signal", "uuid", "generated-survivor-account")}),
        AccountState.BOUND))
    await case.registry.ensure_identity(external_id=survivor, traits={})
    entry = LineageEntry(kind="presence", subject=PERSON)
    original = entry.to_json()
    await case.bindings.consolidate(survivor=survivor, former=PERSON)
    target = await _target(boundary)
    assert target.live.person_ids == frozenset({survivor})
    assert entry_permits(entry, target, origin=None)
    assert excluded((PERSON,), target)
    assert entry.to_json() == original


async def test_cognition_release_reads_selected_circles_and_rechecks_after_live_roster(
    case, location,
):
    async def roster(_platform, _conversation):
        return await location.room.members()

    release = place_release(rooms_of=lambda _unit: [("signal", "generated-location-room", True)],
        room_view=roster, circles=(), access=location.access)
    assert await release("generated-location-unit", PERSON)

    async def late_hold(platform, conversation):
        view = await roster(platform, conversation)
        await case.bindings.hold_identity(person_id=PERSON, operation_id="generated-late-hold")
        return view

    late = place_release(rooms_of=lambda _unit: [("signal", "generated-location-room", True)],
        room_view=late_hold, circles=(), access=location.access)
    assert not await late("generated-location-unit", PERSON)
    await case.bindings.release_identity_hold(person_id=PERSON, operation_id="generated-late-hold")
    assert await release("generated-location-unit", PERSON)


async def test_implicit_parking_reads_current_circle_but_explicit_public_place_is_independent(
    case, location,
):
    deps = replace(location.deps, route_source=CannedRouteSource())
    tools = {tool.name: tool.call for tool in build(deps)}
    assert (await tools["find_parking"]())["status"] == "ok"
    await case.bindings.revoke_binding(account=case.ident)
    assert (await tools["find_parking"]())["status"] == "unavailable"
    await location.server.close()
    assert (await tools["find_parking"]("Generated Public Plaza"))["status"] == "ok"


async def test_tool_lineage_target_reads_current_selected_members(case, location, boundary):
    deps = replace(location.deps, lineage=boundary)
    result = await deps._lineage_target("generated-read")
    assert result is not None
    target, _, view = result
    assert view.available and target.live.person_ids == frozenset({PERSON})
    assert entry_permits(message("signal:generated-source-room"), target, origin=None)
    location.services.backend.down = True
    result = await deps._lineage_target("generated-read")
    assert result is not None
    assert not entry_permits(message("signal:generated-source-room"), result[0], origin=None)


async def test_implicit_parking_rechecks_after_provider_await(case, location):
    class HoldAfterSearch(CannedRouteSource):
        async def nearby_parking(self, **kwargs):
            rows = await super().nearby_parking(**kwargs)
            await case.bindings.hold_identity(
                person_id=PERSON, operation_id="generated-search-hold")
            return rows

    tools = {tool.name: tool.call for tool in build(
        replace(location.deps, route_source=HoldAfterSearch()))}
    result = await tools["find_parking"]()
    assert result["status"] == "unavailable" and "parking" not in result
    await case.bindings.release_identity_hold(person_id=PERSON,
                                             operation_id="generated-search-hold")
    positive = {tool.name: tool.call for tool in build(
        replace(location.deps, route_source=CannedRouteSource()))}
    assert (await positive["find_parking"]())["status"] == "ok"
