"""`LinkShortener` — never load-bearing, and a fake that behaves like the
Worker: bearer-checked mint, the real minted shape, and the failure shapes
(403, non-JSON, timeout) that must all land on None (deploy/shortener/)."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.shortener import LinkShortener

TOKEN = "test-mint-token"  # noqa: S105


class _Worker:
    def __init__(self) -> None:
        self.mode = "ok"          # ok | 403 | html | hang
        self.minted: list[str] = []

    async def mint(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "forbidden"}, status=403)
        if self.mode == "403":
            return web.json_response({"error": "forbidden"}, status=403)
        if self.mode == "html":
            return web.Response(text="<html>challenge</html>", status=200,
                                content_type="text/html")
        if self.mode == "hang":
            await asyncio.sleep(30)
        body = await request.json()
        self.minted.append(body["url"])
        return web.json_response(
            {"slug": "abc1234", "short_url": "https://n.example.net/abc1234"}
        )


@pytest.fixture
async def worker():
    fake = _Worker()
    app = web.Application()
    app.router.add_post("/api/links", fake.mint)
    server = TestServer(app)
    await server.start_server()
    yield fake, str(server.make_url("/")).rstrip("/")
    await server.close()


def _client(base, *, token=TOKEN, timeout=5.0):
    return LinkShortener(base_url=base, token=token, timeout_seconds=timeout)


async def test_a_mint_returns_the_short_url(worker):
    fake, base = worker
    short = await _client(base).shorten("https://www.google.com/maps/dir/?api=1&origin=A")
    assert short == "https://n.example.net/abc1234"
    assert fake.minted == ["https://www.google.com/maps/dir/?api=1&origin=A"]


async def test_a_wrong_token_is_none_never_a_raise(worker):
    _fake, base = worker
    assert await _client(base, token="wrong").shorten("https://x.example/") is None  # noqa: S106


async def test_a_non_json_answer_is_none(worker):
    fake, base = worker
    fake.mode = "html"
    assert await _client(base).shorten("https://x.example/") is None


async def test_a_hung_worker_times_out_to_none(worker):
    fake, base = worker
    fake.mode = "hang"
    assert await _client(base, timeout=0.3).shorten("https://x.example/") is None


async def test_an_unreachable_worker_is_none(worker):
    assert await _client("http://127.0.0.1:1").shorten("https://x.example/") is None
