import pytest
from aiohttp import web

from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.testing import FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.fakes import FakeHoncho

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")


@pytest.fixture
async def client(aiohttp_client):
    app = web.Application()
    register_web_routes(
        app, store=FakeRecordStore(), honcho=FakeHoncho(conclusions={"miles": ["x"]}),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES}, index={}),
        ai_peer="nora", started_at=0.0,
    )
    return await aiohttp_client(app)


async def test_status_route_returns_200_with_the_expected_shape(client):
    resp = await client.get("/app/v1/status")
    assert resp.status == 200
    body = await resp.json()
    assert set(body) == {"uptime_seconds", "store", "honcho", "reminders"}


async def test_memory_route_returns_200_with_every_person(client):
    resp = await client.get("/app/v1/memory")
    assert resp.status == 200
    body = await resp.json()
    assert body == [
        {"person_id": "miles", "display_name": "Miles", "reachable": True, "conclusions": ["x"]},
    ]
