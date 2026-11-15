"""The `IdentityRegistry` trait-read contract, shared by the Kratos
adapter and the fake — the read that carries managed social preferences.

`identity_traits` is the port's descriptive-trait read: `complete` with
the traits dict, `denied` when the identity is absent, `unavailable` when
the service cannot answer. A retired (inactive) identity's traits read
the same — linking retires the record, never its retained preference —
and an `ensure_identity` retry answers by read-back, so it can never
overwrite a preference the record already holds.
"""

from urllib.parse import unquote

import aiohttp
import pytest
from aiohttp import web

from nora.domain.managed import OutcomeStatus
from nora.infra.ory.kratos import KratosClient
from nora.people.adapters.kratos_registry import KratosIdentityRegistry
from nora.testing.managed import FakeIdentityRegistry


@pytest.fixture(params=["memory", "kratos"])
async def registry(request, aiohttp_server):
    """`(registry, down)` — `down(True)` is each implementation's outage
    shape: the fake's flag, or the generated admin API answering 503."""
    if request.param == "memory":
        fake = FakeIdentityRegistry()
        yield fake, lambda down: setattr(fake, "down", down)
        return
    identities: dict[str, dict] = {}
    state = {"fail": False, "counter": 0}

    async def handler(request):
        if state["fail"]:
            return web.json_response({"error": "down"}, status=503)
        if request.method == "POST" and request.path == "/admin/identities":
            supplied = await request.json()
            external = supplied["external_id"]
            if external in identities:
                return web.json_response({"error": "conflict"}, status=409)
            state["counter"] += 1
            identities[external] = {
                "id": f"kratos-{state['counter']}",
                "external_id": external,
                "state": "active",
                "traits": dict(supplied["traits"]),
            }
            return web.json_response(identities[external], status=201)
        prefix = "/admin/identities/by/external/"
        if request.method == "GET" and request.path.startswith(prefix):
            ident = identities.get(unquote(request.path[len(prefix):]))
            if ident is not None:
                return web.json_response(ident)
            return web.json_response({"error": "absent"}, status=404)
        if request.method == "PATCH":
            identity_id = request.path.rsplit("/", 1)[-1]
            for ident in identities.values():
                if ident["id"] == identity_id:
                    patch = await request.json()
                    ident["state"] = patch[0]["value"]
                    return web.json_response(ident)
            return web.json_response({"error": "absent"}, status=404)
        return web.json_response({"error": "absent"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    session = aiohttp.ClientSession()
    reg = KratosIdentityRegistry(
        KratosClient(session, str(server.make_url("")).rstrip("/"))
    )
    yield reg, lambda down: state.update(fail=down)
    await session.close()


async def test_identity_traits_reads_back_what_registration_recorded(registry):
    reg, _ = registry
    outcome, identity_id = await reg.ensure_identity(
        external_id="generated-person",
        traits={"display_name": "Generated", "social": True},
    )
    assert outcome.status is OutcomeStatus.COMPLETE and identity_id
    outcome, traits = await reg.identity_traits(external_id="generated-person")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert traits == {"display_name": "Generated", "social": True}


async def test_identity_traits_for_an_absent_identity_is_denied(registry):
    reg, _ = registry
    outcome, traits = await reg.identity_traits(external_id="generated-absent")
    assert outcome.status is OutcomeStatus.DENIED and traits is None


async def test_a_retired_identity_still_answers_its_recorded_traits(registry):
    """Linking retires the former's Kratos identity — its retained opt-out
    must still read, with no reactivation."""
    reg, _ = registry
    outcome, _ = await reg.ensure_identity(
        external_id="generated-former", traits={"social": False}
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert (await reg.set_state(
        external_id="generated-former", active=False
    )).status is OutcomeStatus.COMPLETE
    outcome, traits = await reg.identity_traits(external_id="generated-former")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert traits is not None and traits["social"] is False


async def test_identity_traits_is_unavailable_when_the_service_cannot_answer(registry):
    reg, down = registry
    outcome, _ = await reg.ensure_identity(
        external_id="generated-person", traits={"social": True}
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    down(True)
    outcome, traits = await reg.identity_traits(external_id="generated-person")
    assert outcome.status is OutcomeStatus.UNAVAILABLE and traits is None
    down(False)
    outcome, traits = await reg.identity_traits(external_id="generated-person")
    assert outcome.status is OutcomeStatus.COMPLETE and traits is not None


async def test_an_ensure_identity_retry_keeps_the_recorded_traits(registry):
    """A registration retry resolves the external-id conflict by
    read-back — never by overwriting the stored preference."""
    reg, _ = registry
    outcome, _ = await reg.ensure_identity(
        external_id="generated-person",
        traits={"display_name": "Generated", "social": True},
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    outcome, identity_id = await reg.ensure_identity(
        external_id="generated-person",
        traits={"display_name": "Changed", "social": False},
    )
    assert outcome.status is OutcomeStatus.COMPLETE and identity_id
    outcome, traits = await reg.identity_traits(external_id="generated-person")
    assert outcome.status is OutcomeStatus.COMPLETE
    assert traits == {"display_name": "Generated", "social": True}
