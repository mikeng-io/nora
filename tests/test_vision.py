"""`VisionClient` — the raw MiMo-V2.5 call under its own timeout and byte ceiling
(DEC-56, the READ half of media custody).

Every scenario is offline, against a local `aiohttp` server that behaves like the
real OmniRoute chat-completions endpoint — INCLUDING the shape it returns for the
wrong call (CLAUDE.md's «never a fake that answers every request the same way»):

* it serves chat completions ONLY over `POST /v1/chat/completions`, 404s any
  other path the way a real gateway does, and 400s a request whose body carries
  no `image_url` block — so a client that stopped sending the image would fail
  loudly here rather than being waved through;
* it can be told to fail (500) a fixed number of times before answering, which is
  what lets a test drive the ONE bounded retry and assert the request count;
* it can be told to stall, which is what turns a hung model into the client's
  `timeout` rather than a hang.

The client is pointed at it with `max_retries=0`, so the retry under test is
`VisionClient`'s own and not the openai SDK's — the two are orthogonal and the
count would be unreadable if both fired.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from openai import AsyncOpenAI

from nora.infra.vision import (
    VISION_TIMEOUT_FRACTION,
    VisionClient,
    derive_vision_timeout,
)

# A 1x1 red PNG — real bytes, so the base64 data URI the client builds is a real
# one and the server can assert it received an image, not a placeholder.
RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeMimo:
    """A stand-in for the OmniRoute chat-completions endpoint."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail_times = 0          # how many leading calls answer 500
        self.stall_seconds = 0.0     # how long each call sleeps before answering
        self.reply = "A red square."

    async def completions(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        # Behave like the real server: a body with no image is a bad request,
        # not a described one. This is the negative control — a client that
        # stopped sending the image gets a 400, never a green pass.
        content = body.get("messages", [{}])[-1].get("content", [])
        has_image = any(
            isinstance(part, dict)
            and part.get("type") == "image_url"
            and str(part.get("image_url", {}).get("url", "")).startswith("data:")
            for part in (content if isinstance(content, list) else [])
        )
        if not has_image:
            return web.json_response({"error": {"message": "no image"}}, status=400)
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        if len(self.requests) <= self.fail_times:
            return web.json_response({"error": {"message": "upstream boom"}}, status=500)
        return web.json_response({
            "id": "cmpl-1", "object": "chat.completion", "created": 0,
            "model": body.get("model", "opencode-go/mimo-v2.5"),
            "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": self.reply},
            }],
            "usage": {"prompt_tokens": 159, "completion_tokens": 4, "total_tokens": 163},
        })

    async def wrong_path(self, request: web.Request) -> web.Response:
        # Any other path is a 404, the way a real gateway answers one — a fake
        # that answered every path alike would assert nothing.
        return web.json_response({"error": {"message": "not found"}}, status=404)


@pytest.fixture
async def mimo():
    fake = FakeMimo()
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


def _client(
    fake: FakeMimo, *, timeout_seconds: float = 5.0, max_bytes: int = 8 * 1024 * 1024,
) -> VisionClient:
    return VisionClient(
        client=AsyncOpenAI(base_url=fake.url, api_key="test-key", max_retries=0),
        model="opencode-go/mimo-v2.5",
        max_tokens=1024,
        reasoning_effort="low",
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )


# ---- the derived timeout is provably shorter than the loop budget -----------

@pytest.mark.parametrize("loop,configured", [(30.0, 20.0), (30.0, 999.0), (10.0, 8.0), (8.0, 20.0)])
def test_the_vision_timeout_is_always_shorter_than_the_loop_budget(loop, configured):
    """The blocker (§2/§6.2): a MiMo call inside the shared loop `wait_for` must
    time out FIRST, or a hung model becomes the loop's silent `invocation_timeout`
    (R-5). `min(configured, loop * fraction)` guarantees it for ANY configured
    value — reverting to `configured` alone lets a 999 s knob outlive a 30 s loop
    and turns this red on the second case."""
    derived = derive_vision_timeout(loop_timeout_seconds=loop, configured=configured)
    assert derived < loop
    assert derived <= loop * VISION_TIMEOUT_FRACTION
    assert derived > 0


# ---- a well-formed call ------------------------------------------------------

async def test_a_well_formed_image_is_described(mimo):
    mimo.reply = "A red one-pixel square."
    result = await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available is True
    assert result.description == "A red one-pixel square."
    # The request carried the model, the effort, and the image as a data URI —
    # the shape the probe verified, asserted rather than assumed.
    sent = mimo.requests[-1]
    assert sent["model"] == "opencode-go/mimo-v2.5"
    assert sent["reasoning_effort"] == "low"
    part = sent["messages"][-1]["content"][-1]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


# ---- the byte ceiling --------------------------------------------------------

async def test_a_blob_over_the_byte_ceiling_is_never_sent(mimo):
    """A byte ceiling below `media.max_bytes` (§2): over it, `too_large` and NO
    request at all — the client must not ship a nine-figure body."""
    client = _client(mimo, max_bytes=len(RED_PNG) - 1)
    result = await client.describe(data=RED_PNG, media_type="image/png")
    assert result.available is False
    assert result.reason == "too_large"
    assert mimo.requests == []


# ---- the timeout -------------------------------------------------------------

async def test_a_hung_model_becomes_a_timeout_not_a_hang(mimo):
    mimo.stall_seconds = 2.0
    result = await _client(mimo, timeout_seconds=0.3).describe(
        data=RED_PNG, media_type="image/png",
    )
    assert result.available is False
    assert result.reason == "timeout"


# ---- the one bounded retry ---------------------------------------------------

async def test_a_transient_failure_is_retried_once_and_then_succeeds(mimo):
    mimo.fail_times = 1
    result = await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available is True
    assert len(mimo.requests) == 2, "the one bounded retry did not fire"


async def test_a_persistent_failure_is_unavailable_and_never_raises(mimo):
    mimo.fail_times = 99
    result = await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available is False
    assert result.reason == "error"
    # Two attempts — the original and the one bounded retry — then it gives up
    # rather than raising into the caller.
    assert len(mimo.requests) == 2


async def test_an_empty_answer_is_a_failure_not_a_blank_description(mimo):
    """A 200 whose message is empty is not «the image was blank» — it is a
    non-answer, and reporting it as a description would invent content."""
    mimo.reply = ""
    result = await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available is False
    assert result.reason == "error"
