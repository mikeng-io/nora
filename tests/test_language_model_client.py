"""`_LanguageModelClient` (`nora.app.composition`) — the Dreamer's one model
call, the same shape `tests/test_vision.py::FakeMimo` already tests
`VisionClient` against, reused here for the same reason: an offline
`aiohttp` server standing in for the OmniRoute chat-completions endpoint,
including its WRONG-call shape (CLAUDE.md's «never a fake that answers every
request the same way») — it can be told to stall, which is what turns a hung
provider into this client's `TimeoutError` rather than a hang.

Covers two findings from the final whole-branch review that were previously
unverified by anything:

* Finding 2a — "schema-forced lexeme extraction" was a prompt asking for
  JSON, nothing more: `response_format` was never sent on the wire.
* Finding 4 — this was the only unbounded raw model call in the codebase; a
  hung provider left `dream_loop` skipping the scope forever
  (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising, so
  the exponential backoff never engaged.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from openai import AsyncOpenAI

from nora.app.composition import _LanguageModelClient


class FakeOmniRoute:
    """A stand-in for the OmniRoute chat-completions endpoint — the same
    role `tests/test_vision.py::FakeMimo` plays for `VisionClient`."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.stall_seconds = 0.0
        self.reply = '{"observations": []}'

    async def completions(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        return web.json_response({
            "id": "cmpl-1", "object": "chat.completion", "created": 0,
            "model": body.get("model", "ollamacloud/deepseek-v4-flash"),
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": self.reply},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        })

    async def wrong_path(self, request: web.Request) -> web.Response:
        return web.json_response({"error": {"message": "not found"}}, status=404)


@pytest.fixture
async def omniroute():
    fake = FakeOmniRoute()
    app = web.Application()
    app.router.add_post("/v1/chat/completions", fake.completions)
    app.router.add_route("*", "/{tail:.*}", fake.wrong_path)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("/v1"))  # type: ignore[attr-defined]
    try:
        yield fake
    finally:
        await server.close()


def _client(omniroute: FakeOmniRoute, *, timeout_seconds: float = 5.0) -> _LanguageModelClient:
    return _LanguageModelClient(
        client=AsyncOpenAI(base_url=omniroute.url, api_key="test-key", max_retries=0),
        model="ollamacloud/deepseek-v4-flash",
        timeout_seconds=timeout_seconds,
    )


async def test_the_request_asks_the_provider_to_hold_up_its_side(omniroute):
    """Finding 2a: the prompt already asks for JSON-only output; nothing on
    the wire used to force the provider to honour it. Reverting
    `response_format={"type": "json_object"}` in `_LanguageModelClient.complete`
    must turn this red."""
    await _client(omniroute).complete(prompt="hello")
    sent = omniroute.requests[-1]
    assert sent["response_format"] == {"type": "json_object"}


async def test_a_hung_provider_becomes_a_timeout_not_a_silent_hang(omniroute):
    """Finding 4: every other raw model call in the codebase is explicitly
    bounded; this one ran on the bare `AsyncOpenAI` default (600s x retries)
    until now. Reverting the `asyncio.wait_for` wrap in
    `_LanguageModelClient.complete` must turn this red — the call would
    still be running when this test's own timeout gives up on IT."""
    omniroute.stall_seconds = 2.0
    with pytest.raises(TimeoutError):
        await _client(omniroute, timeout_seconds=0.3).complete(prompt="hello")


async def test_a_well_formed_response_is_returned_stripped(omniroute):
    omniroute.reply = '  {"observations": []}  '
    result = await _client(omniroute).complete(prompt="hello")
    assert result == '{"observations": []}'
