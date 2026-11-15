"""Roster-observation freshness — onboarding round-one BLOCKER 1 (issue #528
Task 8; `identity.md` «Platform discovery and membership»):

- A complete roster carries departure evidence only while its own
  upstream-observation stamp is fresh: a stale, undated, malformed or
  future-stamped complete snapshot is unavailable — never a departure —
  and an old roster delivered now does not become fresh at delivery.
- The normalizers propagate the producer's own stamp: direct seams stamp
  the read's own completion, bridge seams take the envelope's
  `observed_ms`, and a transport that cannot establish the instant leaves
  it `0` — never a re-stamp of delivery time.
- The fresh complete roster stays the positive control: it still removes
  a genuinely departed member, and a partial roster still merges without
  freshness, since partial was never departure-capable.

All freshness comparisons run against an explicit `wall_clock` — the
tests never sleep.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.app.composition_onboarding import (
    _signal_bridge_fetch,
)
from nora.app.composition_onboarding import (
    onboarding as build,
)
from nora.config_identity import (
    OnboardingCollectionConfig,
    ProvisioningScopeConfig,
)
from nora.domain.identity import Identifier
from nora.domain.managed import (
    CollectionSnapshot,
    ObservedMember,
    ObservedRows,
)
from nora.people import PlatformOnboarding
from nora.platform.bridge.client import BridgeEndpoint
from nora.platform.discord.discovery import DiscordDiscovery
from nora.platform.discord.roster import DiscordRoster
from nora.platform.signal import directory
from nora.platform.signal.discovery import SignalDiscovery
from nora.testing.discovery import ScriptedDiscovery
from tests.unit.test_platform_onboarding import (
    GROUP,
    GUILD,
    UNIT,
    _graph,
    _rule,
    guild_member,
    signal_member,
)
from tests.unit.test_platform_onboarding import (
    TestComposition as _Comp,
)

pytestmark = pytest.mark.asyncio

NOW_MS = 1_750_000_000_000   # a fixed epoch ms — the explicit test clock
NOW = NOW_MS / 1000
INTERVAL = 300.0


def _member(n: int) -> ObservedMember:
    return ObservedMember(
        frozenset({Identifier("discord", "user_id", f"1000{n}")}))


def _onboarding(services, provisioning, *rules, wall_clock=lambda: NOW):
    return PlatformOnboarding(
        rules=tuple(rules), provisioning=provisioning,
        registration=services.registration, bindings=services.bindings,
        authority=services.authority,
        interval_seconds=INTERVAL, retry_seconds=60.0,
        wall_clock=wall_clock)


class TestFreshnessGuard:
    """`observed_ms` is the only clock a departure trusts."""

    async def test_stale_complete_roster_cannot_retract_membership(self):
        services, provisioning = _graph()
        member, other = _member(1), _member(2)
        discovery = ScriptedDiscovery(CollectionSnapshot(
            available=True, complete=True, members=(member, other),
            observed_ms=NOW_MS), collection=GUILD)
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

        # A complete roster missing `other`, stamped a full interval ago
        # and delivered now, is stale evidence — not a departure.
        discovery.snapshot = CollectionSnapshot(
            available=True, complete=True, members=(member,),
            observed_ms=NOW_MS - int((INTERVAL + 60) * 1000))
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

        # Positive control: the same read, honestly stamped now, departs.
        discovery.snapshot = CollectionSnapshot(
            available=True, complete=True, members=(member,),
            observed_ms=NOW_MS)
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 1

    @pytest.mark.parametrize("observed_ms", [
        0,                        # the transport could not time the read
        -1,                       # malformed
        NOW_MS + 3_600_000,       # an hour into the future
    ])
    async def test_undated_invalid_or_future_stamps_cannot_depart(
            self, observed_ms):
        services, provisioning = _graph()
        member, other = _member(1), _member(2)
        discovery = ScriptedDiscovery(CollectionSnapshot(
            available=True, complete=True, members=(member, other),
            observed_ms=NOW_MS), collection=GUILD)
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

        discovery.snapshot = CollectionSnapshot(
            available=True, complete=True, members=(member,),
            observed_ms=observed_ms)
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

    async def test_a_delayed_delivery_through_the_normalizer_is_stale(self):
        """The reviewer's path: a cached read re-served later keeps the
        instant the upstream read actually happened — delivered now, it
        does not read as observed now."""
        services, provisioning = _graph()
        reads = {"rows": [guild_member(1), guild_member(2)],
                 "observed_ms": NOW_MS}

        async def fetch(collection):
            return ObservedRows(
                rows=tuple(reads["rows"]),
                observed_ms=reads["observed_ms"])

        onboarding = _onboarding(
            services, provisioning,
            _rule(DiscordDiscovery(fetch=fetch)))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

        reads["rows"] = [guild_member(1)]
        reads["observed_ms"] = NOW_MS - 3_600_000
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2

    async def test_partial_roster_merges_without_freshness(self):
        """Partial reads were never departure-capable — their merge path
        keeps its existing source-support semantics."""
        services, provisioning = _graph()
        member, other = _member(1), _member(2)
        discovery = ScriptedDiscovery(CollectionSnapshot(
            available=True, complete=True, members=(member,),
            observed_ms=NOW_MS), collection=GUILD)
        onboarding = _onboarding(services, provisioning, _rule(discovery))
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 1

        discovery.snapshot = CollectionSnapshot(
            available=True, complete=False,
            members=(member, other), observed_ms=0)
        onboarding.notify_resumed(platform="discord")
        await onboarding._drain()
        assert len(await services.bindings.members_of(UNIT)) == 2


class TestNormalizersPropagateTheProducerStamp:
    async def test_normalizers_carry_the_upstream_instant_verbatim(self):
        stale = NOW_MS - 3_600_000

        async def discord_fetch(collection):
            return ObservedRows(
                rows=(guild_member(1), guild_member(2)),
                observed_ms=stale)

        snapshot = await DiscordDiscovery(
            fetch=discord_fetch).members(collection=GUILD)
        assert snapshot.available and snapshot.observed_ms == stale

        async def signal_fetch(collection):
            return ObservedRows(
                rows=(signal_member(1),), observed_ms=stale)

        snapshot = await SignalDiscovery(
            fetch=signal_fetch, own_account="").members(collection=GROUP)
        assert snapshot.available and snapshot.observed_ms == stale

    async def test_a_fetch_without_a_stamp_stays_undated(self):
        async def fetch(collection):
            return ObservedRows(rows=(guild_member(1),))

        snapshot = await DiscordDiscovery(
            fetch=fetch).members(collection=GUILD)
        assert snapshot.available and snapshot.observed_ms == 0


class TestBridgeEnvelopesCarryTheStamp:
    async def test_discord_bridge_members_read_takes_the_envelope_time(self):
        services, _ = _graph()
        stamp = NOW_MS - 1_000

        class Control:
            async def read(self, op, params):
                return {"available": True, "observed_ms": stamp,
                        "value": {"members": [{"user": {"id": "10001"}}]}}

        platforms = SimpleNamespace(
            discord=SimpleNamespace(mode="bridge"),
            signal=SimpleNamespace(
                mode="direct", base_url="", account="+852111"))
        cfg = _Comp._cfg(
            collections=(OnboardingCollectionConfig(
                name="c", service="svc", platform="discord",
                collection=GUILD,
                scope=ProvisioningScopeConfig(kind="unit", id="u")),),
            platforms=platforms)
        wiring = build(
            cfg, managed_services=services,
            discord_control=Control(), discord_token="")
        rule = next(iter(wiring.onboarding._rules.values()))
        snapshot = await rule.discovery.members(collection=GUILD)
        assert snapshot.available and snapshot.observed_ms == stamp

    async def test_an_unstamped_bridge_envelope_stays_undated(self):
        services, _ = _graph()

        class Control:
            async def read(self, op, params):
                return {"available": True,
                        "value": {"members": [{"user": {"id": "10001"}}]}}

        platforms = SimpleNamespace(
            discord=SimpleNamespace(mode="bridge"),
            signal=SimpleNamespace(
                mode="direct", base_url="", account="+852111"))
        cfg = _Comp._cfg(
            collections=(OnboardingCollectionConfig(
                name="c", service="svc", platform="discord",
                collection=GUILD,
                scope=ProvisioningScopeConfig(kind="unit", id="u")),),
            platforms=platforms)
        wiring = build(
            cfg, managed_services=services,
            discord_control=Control(), discord_token="")
        rule = next(iter(wiring.onboarding._rules.values()))
        snapshot = await rule.discovery.members(collection=GUILD)
        # An older or incapable transport: undated, never guessed.
        assert snapshot.available and snapshot.observed_ms == 0

    async def test_signal_bridge_fetch_takes_the_envelope_stamp(self):
        stamp = NOW_MS - 5_000
        seen = []

        async def reads(req):
            seen.append(await req.json())
            return web.json_response({
                "available": True, "observed_ms": stamp,
                "members": [{"number": "+852001",
                             "uuid": "00000000-0000-4abc-8def-000000000001"}]})

        app = web.Application()
        app.router.add_post("/v1/reads", reads)
        server = TestServer(app)
        await server.start_server()
        try:
            fetch = _signal_bridge_fetch(BridgeEndpoint(
                base_url=str(server.make_url("")).rstrip("/"),
                token="generated-token"))  # noqa: S106 — generated
            result = await fetch(GROUP)
        finally:
            await server.close()
        assert result is not None and result.observed_ms == stamp
        assert len(result.rows) == 1
        assert seen and seen[0]["op"] == "roster"

    async def test_signal_bridge_fetch_without_a_stamp_stays_undated(self):
        async def reads(req):
            return web.json_response({
                "available": True,
                "members": [{"number": "+852001"}]})

        app = web.Application()
        app.router.add_post("/v1/reads", reads)
        server = TestServer(app)
        await server.start_server()
        try:
            fetch = _signal_bridge_fetch(BridgeEndpoint(
                base_url=str(server.make_url("")).rstrip("/"),
                token="generated-token"))  # noqa: S106 — generated
            result = await fetch(GROUP)
        finally:
            await server.close()
        assert result is not None and result.observed_ms is None


class TestDirectSeamsStampTheReadItself:
    """A direct read IS the upstream observation — the seam stamps its own
    completion, the instant the upstream answered."""

    async def test_discord_direct_stamps_the_rest_answer(self, monkeypatch):
        services, _ = _graph()

        async def guild_members(self, guild_id):
            return [{"user": {"id": "10001"}}]

        monkeypatch.setattr(
            DiscordRoster, "guild_members", guild_members)
        platforms = SimpleNamespace(
            discord=SimpleNamespace(mode="direct"),
            signal=SimpleNamespace(
                mode="direct", base_url="", account="+852111"))
        cfg = _Comp._cfg(
            collections=(OnboardingCollectionConfig(
                name="c", service="svc", platform="discord",
                collection=GUILD,
                scope=ProvisioningScopeConfig(kind="unit", id="u")),),
            platforms=platforms)
        wiring = build(
            cfg, managed_services=services,
            discord_control=None,
            discord_token="generated-token")  # noqa: S106 — generated
        rule = next(iter(wiring.onboarding._rules.values()))
        snapshot = await rule.discovery.members(collection=GUILD)
        assert snapshot.available
        assert 0 < snapshot.observed_ms <= int(time.time() * 1000)

    async def test_signal_direct_stamps_the_daemons_answer(self, monkeypatch):
        services, _ = _graph()

        async def group_members(**kw):
            return {GROUP: (signal_member(1),)}

        monkeypatch.setattr(
            directory, "group_members", group_members)
        platforms = SimpleNamespace(
            discord=SimpleNamespace(mode="direct"),
            signal=SimpleNamespace(
                mode="direct", base_url="http://signal-cli.local",
                account="+852111"))
        cfg = _Comp._cfg(
            collections=(OnboardingCollectionConfig(
                name="c", service="svc", platform="signal",
                collection=GROUP,
                scope=ProvisioningScopeConfig(kind="unit", id="u")),),
            platforms=platforms)
        wiring = build(
            cfg, managed_services=services,
            discord_control=None, discord_token="")
        rule = next(iter(wiring.onboarding._rules.values()))
        snapshot = await rule.discovery.members(collection=GROUP)
        assert snapshot.available
        assert 0 < snapshot.observed_ms <= int(time.time() * 1000)
