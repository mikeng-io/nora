"""Both local and authenticated HTTP circle readers preserve availability and freshness."""

import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nora.domain.presence_circles import CircleUnavailableError
from nora.infra.life360 import CircleRead, PlacesRead, ReadStatus
from nora.infra.presence_circles import HttpCircleObservations
from nora.obs.metrics import Metrics
from nora.people import ManagedPresenceAccounts
from nora.presence.__main__ import _metrics_app
from nora.presence.collector import CircleSpec, PresenceCollector
from nora.presence.directory import CollectorCircleObservations
from nora.testing.managed import FakeBindingStore, FakeIdentityRegistry
from tests.unit.test_presence_collector_synthetic import FakeLife360, FakeStore, member

pytestmark = pytest.mark.asyncio
KEY = "generated-private-directory-key"
CIRCLE = "generated-contract-circle"


@pytest.fixture(params=["collector", "http"])
async def directory(request):
    now = int(time.time() * 1000)
    provider = FakeLife360([CircleRead(ReadStatus.OK, (member("generated-member", now),))],
                           [PlacesRead(ReadStatus.OK)])
    collector = PresenceCollector(client=provider, store=FakeStore(),
        circles=(CircleSpec(CIRCLE, {}),), cadence_seconds=90,
        managed_accounts=ManagedPresenceAccounts(FakeBindingStore(), FakeIdentityRegistry()))
    async with TestServer(_metrics_app(Metrics(), collector, directory_key=KEY)) as server:
        http = HttpCircleObservations(base_url=str(server.make_url("/")), key=KEY,
                                      circles=(CIRCLE,), ttl_ms=180_000)
        reader = http if request.param == "http" else CollectorCircleObservations(collector)
        yield SimpleNamespace(reader=reader, collector=collector, provider=provider,
                              server=server, now=now)


async def test_missing_then_complete_circle_observation_is_distinct(directory):
    with pytest.raises(CircleUnavailableError):
        await directory.reader.read()
    await directory.collector.collect_once(CircleSpec(CIRCLE, {}), now_ms=directory.now)
    rows = await directory.reader.read()
    assert len(rows) == 1 and rows[0].circle_id == CIRCLE
    assert rows[0].member_ids == ("generated-member",)
    assert rows[0].observed_ms == directory.now


async def test_failed_poll_invalidates_prior_membership(directory):
    await directory.collector.collect_once(CircleSpec(CIRCLE, {}), now_ms=directory.now)
    assert len(await directory.reader.read()) == 1
    directory.provider._circle = [CircleRead(ReadStatus.UNAVAILABLE)]
    await directory.collector.collect_once(CircleSpec(CIRCLE, {}), now_ms=directory.now)
    with pytest.raises(CircleUnavailableError):
        await directory.reader.read()


async def test_expired_observation_does_not_become_an_empty_circle(directory):
    await directory.collector.collect_once(CircleSpec(CIRCLE, {}),
                                           now_ms=directory.now - 180_001)
    with pytest.raises(CircleUnavailableError):
        await directory.reader.read()


async def test_http_authentication_is_required_before_any_member_identifier_is_returned(directory):
    await directory.collector.collect_once(CircleSpec(CIRCLE, {}), now_ms=directory.now)
    async with TestClient(TestServer(_metrics_app(Metrics(), directory.collector,
                                                 directory_key=KEY))) as client:
        for headers in ({}, {"Authorization": "Bearer generated-wrong"}):
            response = await client.get("/memberships", headers=headers)
            assert response.status == 403
            assert "generated-member" not in await response.text()
        response = await client.get("/memberships", headers={"Authorization": "Bearer " + KEY})
        assert response.status == 200
        assert "generated-member" in await response.text()
        response = await client.post("/refresh", params={"person_id": "generated-identity"})
        assert response.status == 403


@pytest.mark.parametrize("body", [
    {"circles": [{"circle_id": CIRCLE, "observed_ms": 1, "member_ids": "abc"}]},
    {"circles": [{"circle_id": "generated-foreign", "observed_ms": 1, "member_ids": ["x"]}]},
    {"circles": [None]}, {"circles": {}}, {"circles": []},
])
async def test_http_reader_refuses_malformed_or_foreign_inventory(body, aiohttp_server):
    from aiohttp import web

    app = web.Application()

    async def respond(_request):
        return web.json_response(body)

    app.router.add_get("/memberships", respond)
    server = await aiohttp_server(app)
    reader = HttpCircleObservations(base_url=str(server.make_url("/")), key=KEY,
                                    circles=(CIRCLE,), ttl_ms=1000, clock=lambda: 1)
    with pytest.raises(CircleUnavailableError):
        await reader.read()
