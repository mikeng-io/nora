from aiohttp import web

from nora.domain.models import ConversationTarget
from nora.platform.base import PlatformAdapter
from nora.platform.signal.adapter import SignalAdapter


def _accounts_app(payload) -> web.Application:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get("/api/v1/accounts", handler)
    return app


def test_adapter_satisfies_the_protocol():
    assert isinstance(SignalAdapter("http://127.0.0.1:18080", "+852111"), PlatformAdapter)


def test_adapter_declares_its_platform():
    assert SignalAdapter("http://x", "+1").platform == "signal"


async def test_parse_filters_our_own_account():
    adapter = SignalAdapter("http://x", "+852999")
    event = {"method": "receive", "params": {"envelope": {
        "source": "+852999", "dataMessage": {"message": "hi",
                                             "groupInfo": {"groupId": "g"}}}}}
    assert await adapter.parse(event) is None


async def test_health_reports_connection_state():
    adapter = SignalAdapter("http://x", "+1")
    health = await adapter.health()
    assert set(health) >= {"connected", "last_event_age_seconds", "account"}
    assert health["account"].endswith("1")


async def test_send_delegates_to_the_sender():
    adapter = SignalAdapter("http://127.0.0.1:1", "+1")
    # unreachable daemon → not ok, never an exception
    result = await adapter.send(target=ConversationTarget("signal", "g", "group"), text="x")
    assert result.ok is False


async def test_resolve_identity_always_includes_the_primary(aiohttp_server):
    """Even when the daemon reports nothing beyond the bare number, `primary` is
    always a member of `aliases` — `PlatformIdentity.__post_init__` guarantees this,
    but the adapter must actually reach that constructor."""
    server = await aiohttp_server(_accounts_app([{"number": "+852999"}]))
    adapter = SignalAdapter(str(server.make_url("")).rstrip("/"), "+852999")
    identity = await adapter.resolve_identity()
    assert identity.platform == "signal"
    assert identity.primary == "+852999"
    assert identity.primary in identity.aliases


async def test_resolve_identity_includes_a_uuid_alias_when_the_daemon_reports_one(aiohttp_server):
    """signal-cli can pair a phone number with a UUID for the same account — R-12
    must recognize either form as Nora's own (nora/domain/ports.py's module
    docstring), so the UUID has to make it into `aliases`."""
    server = await aiohttp_server(
        _accounts_app([{"number": "+852999", "uuid": "uuid-abc"}])
    )
    adapter = SignalAdapter(str(server.make_url("")).rstrip("/"), "+852999")
    identity = await adapter.resolve_identity()
    assert "uuid-abc" in identity.aliases
    assert "+852999" in identity.aliases


async def test_resolve_identity_is_idempotent(aiohttp_server):
    server = await aiohttp_server(
        _accounts_app([{"number": "+852999", "uuid": "uuid-abc"}])
    )
    adapter = SignalAdapter(str(server.make_url("")).rstrip("/"), "+852999")
    first = await adapter.resolve_identity()
    second = await adapter.resolve_identity()
    assert first == second
