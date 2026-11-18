"""Unavailable discovery cannot publish residual rows, and retries can recover."""

from dataclasses import replace

from nora.domain.identity import Identifier
from nora.domain.managed import CollectionSnapshot, ObservationFreshness, ObservedMember
from nora.people import PlatformOnboarding
from nora.testing.discovery import ScriptedDiscovery
from tests.unit.test_platform_onboarding import GUILD, UNIT, _graph, _rule


async def test_an_unavailable_roster_cannot_publish_even_with_fresh_partial_data():
    now = 1_750_000_000.0
    clock = [0.0]
    freshness = ObservationFreshness(wall_clock=lambda: now)
    services, provisioning = _graph(freshness=freshness)
    account = Identifier("discord", "user_id", "10001")
    snapshot = CollectionSnapshot(
        available=False, complete=True, members=(ObservedMember(frozenset({account})),),
        observed_ms=int(now * 1000), detail="generated interrupted read")
    discovery = ScriptedDiscovery(snapshot, collection=GUILD)
    onboarding = PlatformOnboarding(
        rules=(_rule(discovery),), provisioning=provisioning,
        registration=services.registration, bindings=services.bindings,
        authority=services.authority, interval_seconds=10**9, retry_seconds=60,
        freshness=freshness, clock=lambda: clock[0], wall_clock=lambda: now)
    await onboarding._drain()
    assert discovery.reads == 1
    assert not await services.bindings.persons()
    assert not await services.bindings.members_of(UNIT)
    assert await services.bindings.record_for(account=account) is None

    discovery.snapshot = replace(snapshot, available=True, detail="")
    clock[0] = 60
    await onboarding._drain()
    assert discovery.reads == 2
    record = await services.bindings.record_for(account=account)
    assert record is not None
    assert {m.person_id for m in await services.bindings.members_of(UNIT)} == {record.person_id}
