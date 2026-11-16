"""The admission create/read seam on its fake and production HTTP adapter."""

from copy import deepcopy
from urllib.parse import unquote

import aiohttp
import pytest
from aiohttp import web

from nora.domain.managed import OutcomeStatus
from nora.infra.ory.kratos import KratosClient
from nora.people.adapters.managed_handoff import KratosAdmission
from tests.fakes import FakeAdmissionKratos

PERSON = "generated-person"
TRAITS = {"display_name": "Generated", "social": False}
OWNER = {"nora_admission": {"import_id": "generated-import", "effect_sha256": "generated-effect"}}


@pytest.fixture(params=["fake", "http"])
async def seam(request, aiohttp_server):
    records = {}
    state = {"down": False}
    if request.param == "fake":
        backend = FakeAdmissionKratos(records)
        yield backend, lambda: setattr(backend, "down", True)
        return

    async def handler(request):
        if state["down"]:
            return web.Response(status=503)
        if request.method == "POST" and request.path == "/admin/identities":
            body = await request.json()
            pid = body["external_id"]
            if pid in records:
                return web.Response(status=409)
            record = {"id": "generated-kratos-id", "external_id": pid,
                      "state": "active", "traits": body["traits"],
                      "metadata_admin": body.get("metadata_admin")}
            records[pid] = record
            return web.json_response(record, status=201)
        prefix = "/admin/identities/by/external/"
        if request.method == "GET" and request.raw_path.startswith(prefix):
            pid = unquote(request.raw_path[len(prefix):])
            if pid in records:
                return web.json_response(records[pid])
            return web.Response(status=404)
        return web.Response(status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        backend = KratosAdmission(KratosClient(session, str(server.make_url("")).rstrip("/")))
        yield backend, lambda: state.update(down=True)


async def test_create_read_and_collision_preserve_exact_owned_record(seam):
    backend, _ = seam
    absent, view = await backend.read(external_id=PERSON)
    assert absent.status is OutcomeStatus.DENIED and absent.detail == "absent" and view is None
    created, view = await backend.create(external_id=PERSON, traits=TRAITS, owner=OWNER)
    assert created.status is OutcomeStatus.COMPLETE and view is not None
    assert view.external_id == PERSON and view.state == "active"
    assert view.traits == TRAITS and view.metadata_admin == OWNER
    collision, other = await backend.create(
        external_id=PERSON, traits={"social": True}, owner={"foreign": True})
    assert collision.status is OutcomeStatus.DENIED
    assert collision.detail == "external-id-conflict" and other is None
    found, reread = await backend.read(external_id=PERSON)
    assert found.status is OutcomeStatus.COMPLETE and reread == view
    absent, other = await backend.read(external_id="generated-sibling")
    assert absent.status is OutcomeStatus.DENIED and other is None


async def test_transport_failure_is_unavailable_on_read_and_create(seam):
    backend, fail = seam
    created, view = await backend.create(external_id=PERSON, traits=TRAITS, owner=OWNER)
    assert created.status is OutcomeStatus.COMPLETE and view is not None
    fail()
    for outcome, view in (
        await backend.read(external_id=PERSON),
        await backend.create(external_id="generated-sibling", traits=TRAITS, owner=OWNER),
    ):
        assert outcome.status is OutcomeStatus.UNAVAILABLE and view is None


async def test_remote_write_and_read_are_snapshots_of_the_supplied_metadata(seam):
    backend, _ = seam
    owner = deepcopy(OWNER)
    created, view = await backend.create(external_id=PERSON, traits=TRAITS, owner=owner)
    assert created.status is OutcomeStatus.COMPLETE and view is not None
    owner["nora_admission"]["import_id"] = "changed-after-create"
    found, first = await backend.read(external_id=PERSON)
    assert found.status is OutcomeStatus.COMPLETE and first.metadata_admin == OWNER
    first.metadata_admin["nora_admission"]["import_id"] = "changed-after-read"
    found, second = await backend.read(external_id=PERSON)
    assert found.status is OutcomeStatus.COMPLETE and second.metadata_admin == OWNER
