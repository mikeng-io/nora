"""Producer-time evidence over the real daemon -> bridge -> discovery route."""

import time

import pytest

from nora.app.composition_onboarding import _signal_bridge_fetch
from nora.domain.identity import Identifier
from nora.platform.bridge.client import BridgeEndpoint, BridgeRoster
from nora.platform.signal.discovery import SignalDiscovery
from nora.testing.signal_daemon import FakeSignalDaemon
from tests.integration.test_signal_bridge_routes import (
    AUTH,
    OWN,
    ROOM,
    _cfg,
    _projection,
    _redis_url,
    _spawn,
    _teardown,
)

pytestmark = pytest.mark.integration
PHONE = "+85290000101"
ACI = "a1000001-0000-4000-8000-000000000001"


@pytest.fixture
async def roster_bridge(clean_pg_url, redis_client, tmp_path, aiohttp_server):
    daemon = FakeSignalDaemon()
    daemon.groups = [{"id": ROOM, "members": [{"number": PHONE, "uuid": ACI}]}]
    upstream = await aiohttp_server(daemon.app())
    _projection(tmp_path)
    cfg = _cfg(tmp_path, signal_http=str(upstream.make_url("")),
               redis_url=_redis_url(redis_client), database_url=clean_pg_url)
    bridge, publisher, client = await _spawn(cfg)
    try:
        yield client, daemon
    finally:
        await _teardown(bridge, publisher, client)


async def test_roster_route_produces_and_discovery_preserves_source_time(roster_bridge):
    client, daemon = roster_bridge
    endpoint = BridgeEndpoint(base_url=str(client.make_url("")).rstrip("/"),
                              token=AUTH["Authorization"].removeprefix("Bearer "))
    read = _signal_bridge_fetch(endpoint)
    observed = []

    async def fetch(collection):
        result = await read(collection)
        observed.append(result)
        return result

    before = int(time.time() * 1000)
    snapshot = await SignalDiscovery(fetch=fetch, own_account=OWN).members(collection=ROOM)
    after = int(time.time() * 1000)
    assert snapshot.available and snapshot.complete
    assert snapshot.observed_ms == observed[0].observed_ms
    assert before <= snapshot.observed_ms <= after
    assert snapshot.members[0].identifiers == frozenset({
        Identifier("signal", "phone", PHONE), Identifier("signal", "uuid", ACI)})
    assert daemon.rpc_calls == ["listGroups"]

    missing = await SignalDiscovery(fetch=fetch, own_account=OWN).members(collection="absent")
    assert not missing.available and not missing.members
    denied = await client.post("/v1/reads", json={"op": "roster",
        "params": {"conversation_id": ROOM}}, headers={"Authorization": "Bearer wrong"})
    assert denied.status == 401
    assert daemon.rpc_calls == ["listGroups", "listGroups"]

    daemon.rpc_errors.add("listGroups")
    failed = await SignalDiscovery(fetch=fetch, own_account=OWN).members(collection=ROOM)
    assert not failed.available and not failed.members
    assert daemon.rpc_calls == ["listGroups", "listGroups", "listGroups"]


async def test_bridge_roster_reads_the_real_bridge_route(roster_bridge):
    client, daemon = roster_bridge
    endpoint = BridgeEndpoint(base_url=str(client.make_url("")).rstrip("/"),
                              token=AUTH["Authorization"].removeprefix("Bearer "))
    roster = BridgeRoster("signal", endpoint)
    known = await roster.roster(conversation_id=ROOM)
    assert known.available
    assert known.members == (frozenset({PHONE, ACI}),)
    assert not (await roster.roster(conversation_id="absent")).available
    daemon.groups = [{"id": ROOM, "members": [{"number": True}]}]
    assert not (await roster.roster(conversation_id=ROOM)).available
    daemon.rpc_errors.add("listGroups")
    assert not (await roster.roster(conversation_id=ROOM)).available
