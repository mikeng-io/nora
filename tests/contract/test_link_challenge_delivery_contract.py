"""The private account-delivery contract, shared by the fake and real Signal HTTP sender."""

from types import SimpleNamespace

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.platform.signal.consent import SignalLinkDelivery
from nora.testing.link_delivery import FakeLinkChallengeDelivery

ACCOUNTS = (
    Identifier("signal", "uuid", "generated-holder-account"),
    Identifier("signal", "phone", "+85290000101"),
)


@pytest.fixture(params=["memory", "signal"])
async def transport(request, aiohttp_server):
    if request.param == "memory":
        delivery = FakeLinkChallengeDelivery()
        return delivery, delivery
    state = SimpleNamespace(fail=False, sent=[])

    async def rpc(request):
        body = await request.json()
        assert body["method"] == "send"
        assert "groupId" not in body["params"]
        if state.fail:
            return web.Response(status=503)
        params = body["params"]
        assert len(params["recipient"]) == 1
        state.sent.append((params["recipient"][0], params["message"]))
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": {}})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    server = await aiohttp_server(app)
    return SignalLinkDelivery(str(server.make_url(""))), state


def _sent(state):
    return [(account.value if isinstance(account, Identifier) else account, text)
            for account, text in state.sent]


@pytest.mark.parametrize("account", ACCOUNTS)
async def test_challenge_is_delivered_privately_to_the_exact_account_with_unchanged_text(
    transport, account,
):
    delivery, state = transport
    text = "Generated request pair, survivor and private nonce"
    assert await delivery.deliver(account=account, text=text) is True
    assert _sent(state) == [(account.value, text)]


async def test_foreign_platform_is_refused_without_a_transport_send(transport):
    delivery, state = transport
    assert await delivery.deliver(account=ACCOUNTS[0], text="Generated positive control")
    before = _sent(state)
    assert await delivery.deliver(
        account=Identifier("discord", "user_id", "generated-other-platform"), text="Private nonce"
    ) is False
    assert _sent(state) == before


async def test_unconfirmed_transport_is_false_and_recovers_after_the_failure_clears(transport):
    delivery, state = transport
    state.fail = True
    assert await delivery.deliver(account=ACCOUNTS[0], text="Generated request") is False
    state.fail = False
    assert await delivery.deliver(account=ACCOUNTS[0], text="Generated recovered request") is True
    assert _sent(state)[-1] == (ACCOUNTS[0].value, "Generated recovered request")
