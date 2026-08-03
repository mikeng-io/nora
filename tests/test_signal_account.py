import pytest
from aiohttp import web

from nora.platform.signal.account import AccountError, derive_account


async def _server(aiohttp_server, payload, status=200, path="/api/v1/accounts"):
    async def handler(request):
        if status != 200:
            return web.Response(status=status)
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get(path, handler)
    return await aiohttp_server(app)


async def test_derives_a_single_account(aiohttp_server):
    s = await _server(aiohttp_server, ["+85290000100"])
    assert await derive_account(str(s.make_url("")).rstrip("/")) == "+85290000100"


async def test_derives_from_an_object_list(aiohttp_server):
    s = await _server(aiohttp_server, [{"number": "+85290000100"}])
    assert await derive_account(str(s.make_url("")).rstrip("/")) == "+85290000100"


async def test_config_override_wins_and_skips_the_request():
    assert await derive_account("http://127.0.0.1:1", override="+1234") == "+1234"


async def test_multiple_accounts_without_an_override_is_a_startup_failure(aiohttp_server):
    s = await _server(aiohttp_server, ["+1", "+2"])
    with pytest.raises(AccountError, match="multiple accounts"):
        await derive_account(str(s.make_url("")).rstrip("/"))


async def test_no_accounts_is_a_startup_failure(aiohttp_server):
    s = await _server(aiohttp_server, [])
    with pytest.raises(AccountError, match="no account"):
        await derive_account(str(s.make_url("")).rstrip("/"))


async def test_an_unreachable_daemon_is_a_startup_failure():
    with pytest.raises(AccountError, match="could not reach"):
        await derive_account("http://127.0.0.1:1", timeout=0.3)
