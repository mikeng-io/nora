"""Real HTTP preserves opaque correlation keys and refuses foreign identities."""

from urllib.parse import unquote
from uuid import uuid4

import aiohttp
import pytest
from aiohttp import web

from nora.domain.managed import OutcomeStatus
from nora.infra.ory.kratos import KratosClient
from nora.people.adapters.kratos_registry import KratosIdentityRegistry
from nora.people.adapters.managed_handoff import KratosAdmission


@pytest.fixture
async def api(aiohttp_server):
    state = {"external": "generated-person", "identity": str(uuid4()),
             "body": None, "paths": [], "session_delete": 204, "sessions": [],
             "identity_get": 200, "supplied": None}

    async def handler(request):
        state["paths"].append(request.raw_path)
        if request.headers.get("Authorization") != "Bearer generated-key":
            return web.json_response({"error": "unauthorized"}, status=401)
        body = {"id": state["identity"], "external_id": state["external"],
                "state": "active", "traits": {"display_name": "Generated"}}
        if request.method == "POST" and request.path == "/admin/identities":
            supplied = await request.json()
            assert supplied["external_id"] == state["external"]
            state["supplied"] = supplied
            if "metadata_admin" in supplied:
                body["metadata_admin"] = supplied["metadata_admin"]
            return web.json_response(body if state["body"] is None else state["body"], status=201)
        prefix = "/admin/identities/by/external/"
        if request.method == "GET" and request.raw_path.startswith(prefix):
            segment = request.raw_path[len(prefix):]
            if "/" not in segment and "?" not in segment and unquote(segment) == state["external"]:
                return web.json_response(body if state["body"] is None else state["body"])
        if request.path == "/admin/identities/" + state["identity"] + "/sessions":
            if request.method == "DELETE":
                return web.Response(status=state["session_delete"])
            if request.method == "GET":
                return web.json_response(state["sessions"])
        if request.path == "/admin/identities/" + state["identity"]:
            if request.method == "GET":
                if state["identity_get"] != 200:
                    return web.Response(status=state["identity_get"])
                return web.json_response(body if state["body"] is None else state["body"])
            if request.method == "PATCH":
                patch = await request.json()
                body["state"] = patch[0]["value"]
                return web.json_response(body if state["body"] is None else state["body"])
        return web.json_response({"error": "absent"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        yield KratosClient(session, str(server.make_url("")).rstrip("/"),
                           api_key="generated-key"), state


@pytest.mark.parametrize("external", [
    "generated-person", "namespace/person", "../other", ".", "..",
    "name.with.dots", "name?state=active", "name#fragment", "literal%2Fvalue", "生成身份",
])
async def test_opaque_external_id_reaches_one_exact_admin_resource(api, external):
    client, state = api
    state["external"] = external
    outcome, identity = await client.get_by_external_id(external)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert identity is not None and identity.external_id == external
    assert len(state["paths"]) == 1


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("state", "unknown"), ("state", []), ("state", {}),
    ("traits", []), ("external_id", "another-person"),
])
async def test_malformed_or_foreign_identity_is_unavailable_not_an_active_person(api, field, value):
    client, state = api
    healthy, identity = await client.get_by_external_id(state["external"])
    assert healthy.status is OutcomeStatus.COMPLETE and identity is not None
    state["body"] = {"id": state["identity"], "external_id": state["external"],
                     "state": "active", "traits": {}}
    state["body"][field] = value
    failed, identity = await client.get_by_external_id(state["external"])
    assert failed.status is OutcomeStatus.UNAVAILABLE and identity is None


async def test_create_cannot_correlate_the_person_to_a_foreign_response(api):
    client, state = api
    state["body"] = {"id": state["identity"], "external_id": "another-person",
                     "state": "active", "traits": {}}
    outcome, identity = await client.create_identity(external_id=state["external"], traits={})
    assert outcome.status is OutcomeStatus.UNAVAILABLE and identity is None


async def test_create_writes_admin_metadata_and_the_read_surfaces_it(api):
    """The durable admission marker rides the remote write itself and comes
    back on both the create response and the external-id read."""
    client, state = api
    marker = {"nora_admission": {
        "import_id": "file-admission:generated", "effect_sha256": "generated-effect"}}
    outcome, identity = await client.create_identity(
        external_id=state["external"], traits={}, metadata_admin=marker)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert state["supplied"]["metadata_admin"] == marker
    assert identity is not None and identity.metadata_admin == marker
    state["body"] = {"id": state["identity"], "external_id": state["external"],
                     "state": "active", "traits": {}, "metadata_admin": marker}
    outcome, identity = await client.get_by_external_id(state["external"])
    assert outcome.status is OutcomeStatus.COMPLETE
    assert identity is not None and identity.metadata_admin == marker


async def test_admission_seam_moves_the_marker_as_typed_values(api):
    """`KratosAdmission` carries the owner marker into the remote create and
    returns the concrete record — typed values only, the judgement stays
    with the caller."""
    client, state = api
    admission = KratosAdmission(client)
    marker = {"nora_admission": {
        "import_id": "file-admission:generated", "effect_sha256": "generated-effect"}}
    outcome, view = await admission.create(
        external_id=state["external"], traits={}, owner=marker)
    assert outcome.status is OutcomeStatus.COMPLETE
    assert state["supplied"]["metadata_admin"] == marker
    assert view is not None and view.metadata_admin == marker
    state["body"] = {"id": state["identity"], "external_id": state["external"],
                     "state": "active", "traits": {}, "metadata_admin": marker}
    outcome, view = await admission.read(external_id=state["external"])
    assert outcome.status is OutcomeStatus.COMPLETE
    assert (view is not None and view.identity_id == state["identity"]
            and view.metadata_admin == marker)


async def test_a_non_object_admin_metadata_is_malformed(api):
    client, state = api
    state["body"] = {"id": state["identity"], "external_id": state["external"],
                     "state": "active", "traits": {}, "metadata_admin": "not-an-object"}
    outcome, identity = await client.get_by_external_id(state["external"])
    assert outcome.status is OutcomeStatus.UNAVAILABLE and identity is None


async def test_get_by_id_cannot_return_another_identity(api):
    client, state = api
    state["body"] = {"id": str(uuid4()), "external_id": state["external"],
                     "state": "active", "traits": {}}
    outcome, identity = await client.get_identity(state["identity"])
    assert outcome.status is OutcomeStatus.UNAVAILABLE and identity is None


async def test_state_update_requires_the_returned_identity_and_state_to_agree(api):
    client, state = api
    good = await client.set_state(identity_id=state["identity"], active=False)
    assert good.status is OutcomeStatus.COMPLETE
    state["body"] = {"id": state["identity"], "external_id": state["external"],
                     "state": "active", "traits": {}}
    refused = await client.set_state(identity_id=state["identity"], active=False)
    assert refused.status is OutcomeStatus.UNAVAILABLE


async def test_session_deletion_accepts_zero_rows_only_after_empty_readback(api):
    client, state = api
    assert (await client.drop_sessions(identity_id=state["identity"])
            ).status is OutcomeStatus.COMPLETE
    state["session_delete"] = 404
    assert (await client.drop_sessions(identity_id=state["identity"])
            ).status is OutcomeStatus.COMPLETE
    state["sessions"] = [{"id": "generated-session"}]
    assert (await client.drop_sessions(identity_id=state["identity"])
            ).status is OutcomeStatus.UNAVAILABLE


async def test_empty_sessions_cannot_hide_a_mismatched_identity_readback(api):
    client, state = api
    state["session_delete"] = 404
    state["body"] = {"id": str(uuid4()), "external_id": state["external"],
                     "state": "active", "traits": {}}
    assert (await client.drop_sessions(identity_id=state["identity"])
            ).status is OutcomeStatus.UNAVAILABLE


async def test_registry_never_reports_a_missing_identity_readback_as_revocation_success(api):
    client, state = api
    registry = KratosIdentityRegistry(client)
    assert (await registry.drop_sessions(external_id=state["external"])
            ).status is OutcomeStatus.COMPLETE
    state["session_delete"] = 404
    state["identity_get"] = 404
    assert (await registry.drop_sessions(external_id=state["external"])
            ).status is OutcomeStatus.DENIED
