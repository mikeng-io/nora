"""One suite, every implementation of `InstagramAccountLookup`.

`SocialRouter` resolves a typed handle, or the username in a pasted link, through this
port, and every router test that drives it through `FakeInstagramAccountLookup` is
evidence about production only if the fake answers the way `InstagramSocialClient` does
over the real bridge contract. This file is what says it does (AGENTS.md: a fake is bound
to its production implementation by a shared contract suite).

The production arm talks to a fake instagram-bridge that behaves like the real
`GET /accounts?username=`: it validates the username the way the Go handler does (400),
filters its accounts to an exact, ASCII case-insensitive match with one leading `@`
ignored, and answers the LIST shape with 0 or 1 items. Its bodies start from the shared
fixtures (`tests/fixtures/instagram_bridge/accounts_by_username_*`) the Go handlers
generate. Every name and id is synthetic.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import InstagramAccountLookup, UsernameLookup
from nora.infra.social import InstagramSocialClient
from nora.testing import FakeInstagramAccountLookup

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "instagram_bridge"
_FOUND = json.loads((_FIXTURES / "accounts_by_username_found.json").read_text())
#: The one account both arms hold, as the fixture names it.
_ACCOUNT = _FOUND["body"]["items"][0]
_USERNAME, _IGID = _ACCOUNT["username"], _ACCOUNT["igid"]


class _Bridge:
    """`GET /accounts?username=` as the Go handler answers it."""

    _VALID = re.compile(r"@?[A-Za-z0-9._]{1,30}")

    def __init__(self, *, status: int = 200) -> None:
        self.status = status

    async def handle(self, request: web.Request) -> web.Response:
        if self.status != 200:
            return web.json_response(
                {"error": {"code": "upstream_rejected", "message": "synthetic"}},
                status=self.status,
            )
        username = request.query.get("username", "")
        if not self._VALID.fullmatch(username):
            fx = json.loads((_FIXTURES / "accounts_by_username_bad_username_400.json").read_text())
            return web.json_response(fx["body"], status=fx["status"])
        body = copy.deepcopy(_FOUND["body"])
        body["items"] = [
            row for row in body["items"]
            if row["username"].lower() == username.removeprefix("@").lower()
        ]
        return web.json_response(body)


@pytest.fixture
async def bridge_url():
    servers = []

    async def start(status: int = 200) -> str:
        app = web.Application()
        app.router.add_get("/accounts", _Bridge(status=status).handle)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("")).rstrip("/")

    yield start
    for server in servers:
        await server.close()


@pytest.fixture(params=["fake", "client"])
async def lookup(request, bridge_url):
    if request.param == "fake":
        return FakeInstagramAccountLookup({_USERNAME: _IGID})
    return InstagramSocialClient(base_url=await bridge_url(), timeout_seconds=5.0)


@pytest.fixture(params=["fake", "client"])
async def broken(request, bridge_url):
    if request.param == "fake":
        return FakeInstagramAccountLookup({_USERNAME: _IGID}, unavailable=True)
    return InstagramSocialClient(base_url=await bridge_url(status=503), timeout_seconds=5.0)


def test_both_satisfy_the_port():
    assert isinstance(FakeInstagramAccountLookup(), InstagramAccountLookup)
    assert isinstance(InstagramSocialClient(base_url="http://127.0.0.1:1"), InstagramAccountLookup)


def test_the_username_is_keyword_only():
    for impl in (InstagramAccountLookup, InstagramSocialClient, FakeInstagramAccountLookup):
        params = inspect.signature(impl.igid_for_username).parameters
        assert params["username"].kind is inspect.Parameter.KEYWORD_ONLY, impl


@pytest.mark.parametrize(
    "asked", [_USERNAME, _USERNAME.upper(), f"@{_USERNAME}", f"  {_USERNAME} "],
)
async def test_the_exact_username_is_found_whatever_its_case(lookup, asked):
    assert await lookup.igid_for_username(username=asked) == UsernameLookup("found", _IGID)


@pytest.mark.parametrize("asked", ["nonexistent_handle", f"{_USERNAME}2", _USERNAME[:-1]])
async def test_a_username_nobody_holds_is_none_never_a_near_match(lookup, asked):
    """«No such account», and never the nearest one: a guessed account is a
    different human."""
    assert await lookup.igid_for_username(username=asked) == UsernameLookup("none")


async def test_a_lookup_that_cannot_be_made_is_unavailable_and_never_raises(broken):
    """«I couldn't check» is never «no such account»."""
    out = await broken.igid_for_username(username=_USERNAME)
    assert out.status == "unavailable"
    assert out.igid == ""
