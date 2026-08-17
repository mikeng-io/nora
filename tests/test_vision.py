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
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from openai import AsyncOpenAI

from nora.infra.vision import (
    DESCRIBE_PROMPTS,
    VISION_LOOP_SHARE,
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
        # Behave like the real server, INCLUDING its wrong-call shapes
        # (CLAUDE.md). A body with no media is a bad request, and so is media
        # carried in the wrong content block: a real OpenAI-compatible gateway
        # rejects `data:audio/aac;…` inside `image_url`, it does not describe it.
        #
        # That second half is why this fake was rewritten. It used to accept ANY
        # `image_url` whose url began `data:`, so an audio payload smuggled into
        # an image block passed its negative control — and the per-modality
        # split shipped with a transport bug no test could see.
        content = body.get("messages", [{}])[-1].get("content", [])
        parts = [p for p in (content if isinstance(content, list) else []) if isinstance(p, dict)]
        for part in parts:
            if part.get("type") == "image_url":
                url = str(part.get("image_url", {}).get("url", ""))
                if not url.startswith("data:"):
                    return web.json_response(
                        {"error": {"message": "no image"}}, status=400)
                if not url.startswith("data:image/"):
                    return web.json_response(
                        {"error": {"message": f"not an image: {url[:24]}"}}, status=400)
            elif part.get("type") == "input_audio":
                audio = part.get("input_audio", {})
                if not audio.get("data") or not audio.get("format"):
                    return web.json_response(
                        {"error": {"message": "input_audio needs data and format"}},
                        status=400)
            elif part.get("type") == "video_url":
                if not str(part.get("video_url", {}).get("url", "")).startswith("data:video/"):
                    return web.json_response(
                        {"error": {"message": "not a video"}}, status=400)
        if not any(p.get("type") in {"image_url", "input_audio", "video_url"} for p in parts):
            return web.json_response({"error": {"message": "no media"}}, status=400)
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
@pytest.mark.parametrize("budget", [1, 2, 3])
def test_the_budget_of_vision_calls_together_stays_under_the_loop(loop, configured, budget):
    """The blocker (§2/§6.2) in its AGGREGATE form, which both reviews 2026-08-07
    demanded: ONE call < loop is not enough — `media_fetch` has budget 2, so two
    hung calls inside the shared `wait_for` must STILL leave headroom, or the
    second is the loop's silent `invocation_timeout` (R-5). Dividing the per-call
    ceiling by the budget guarantees `budget * derived <= loop * VISION_LOOP_SHARE
    < loop` for ANY configured value; reverting to a per-call fraction (not
    dividing by budget) turns the budget>=2 cases red, and a 999 s knob still
    cannot outlive the loop."""
    derived = derive_vision_timeout(
        loop_timeout_seconds=loop, configured=configured, budget=budget,
    )
    assert derived > 0
    assert budget * derived <= loop * VISION_LOOP_SHARE
    assert budget * derived < loop            # headroom for the text turns


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


def test_the_vision_timeout_divides_by_the_resolved_budget_not_the_table():
    """R-33's invariant — `budget * per_call < loop_timeout_seconds` — survives
    a `[llm.budgets]` override.

    `derive_vision_timeout`'s own docstring says the invariant "is one nobody
    can violate if it is never written down twice". `[llm.budgets]` wrote it
    twice: `nora/app/composition.py` read `NATIVE_TOOL_BUDGETS["media_fetch"]`
    straight off the table while R-7's ceiling came from
    `ToolRegistry.budgets()`, which the new knob moves.

    The failure that produces is the one R-33 exists to prevent, not a wrong
    number: `media_fetch = 8` permits eight vision calls while the per-call
    ceiling stays derived from four, so 8 x 18 s = 144 s against a 120 s loop.
    The turn dies on the loop's `wait_for` and surfaces as a silent
    `invocation_timeout` — no reply, and nothing that says why.

    Reads COMPOSITION'S OWN SOURCE, not a registry this test builds. The first
    version constructed its own `ToolRegistry(budget_overrides=...)` and
    computed the invariant from that — which is true by construction and stayed
    green with the production fix reverted (external review, 2026-08-11). A test
    that cannot see the wiring it narrates is decoration.

    So: assert that composition resolves the budget through `budgets()` and does
    NOT read the raw table. That is the actual defect, and it is what changed."""
    from nora.agent.registry import ToolRegistry

    source = (Path(__file__).resolve().parents[1]
              / "nora" / "app" / "composition.py").read_text()
    call = source[source.index("derive_vision_timeout("):]
    call = call[:call.index("),\n    )")]
    # CODE only. The comment above the argument explains the defect by naming
    # `NATIVE_TOOL_BUDGETS`, and a substring check would read that as the bug.
    call = "\n".join(ln for ln in call.splitlines() if not ln.strip().startswith("#"))

    assert "budgets()[\"media_fetch\"]" in call, (
        "composition no longer resolves media_fetch's budget through "
        "ToolRegistry.budgets() — an override would move R-7's ceiling while "
        "the per-call vision timeout kept dividing by the table (R-33)"
    )
    assert "NATIVE_TOOL_BUDGETS" not in call

    # And the property itself holds at every budget the knob can produce.
    loop = 120.0
    for override in ({}, {"media_fetch": 2}, {"media_fetch": 8}, {"media_fetch": 20}):
        budget = ToolRegistry(budget_overrides=override).budgets()["media_fetch"]
        per_call = derive_vision_timeout(
            loop_timeout_seconds=loop, configured=20.0, budget=budget)

        assert budget * per_call < loop, (
            f"override {override} gives budget={budget} x {per_call}s = "
            f"{budget * per_call}s, which exceeds the {loop}s loop — R-33"
        )
        assert per_call > 0


# ---- the per-modality model override (2026-08-17 spec) ----------------------

async def test_a_per_call_model_overrides_the_configured_one(mimo):
    """The seam the split promises: naming a model for a modality must actually
    ROUTE there. Gating access alone would leave one client with one model, so
    an audio model could be configured and never reached.

    Asserted against the request the fake server actually received, not against
    the client's own attribute — the point is which model the GATEWAY was asked
    for.
    """
    result = await _client(mimo).describe(
        data=RED_PNG, media_type="image/png", model="other/audio-model"
    )
    assert result.available
    assert mimo.requests[-1]["model"] == "other/audio-model"


async def test_the_configured_model_is_used_when_no_override_is_given(mimo):
    """The existing call shape keeps its exact behaviour: `media_fetch` before
    this split passed no model, and must still reach the configured one."""
    await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert mimo.requests[-1]["model"] == "opencode-go/mimo-v2.5"


# ---- the payload shape per modality (2026-08-17 spec, review round 1) --------

def _block_types(body: dict) -> list[str]:
    return [p.get("type") for p in body["messages"][-1]["content"] if isinstance(p, dict)]


async def test_audio_is_sent_as_an_input_audio_block(mimo):
    """The finding that made the first cut of this split a false promise: the
    model override chose WHICH model, but every modality was still shipped in an
    `image_url` block, so enabling audio sent `data:audio/aac;…` as an image and
    the gateway 400'd. «A flag and a model name» was untrue at the transport.

    Asserted against what the fake gateway RECEIVED, and the fake now rejects
    media in the wrong block, so this cannot pass by agreeing with itself.
    """
    result = await _client(mimo).describe(data=RED_PNG, media_type="audio/mpeg")
    assert result.available, result.reason
    assert "input_audio" in _block_types(mimo.requests[-1])
    assert "image_url" not in _block_types(mimo.requests[-1])
    audio = mimo.requests[-1]["messages"][-1]["content"][-1]["input_audio"]
    assert audio["format"] == "mp3"          # the API's token, not the MIME subtype


async def test_video_is_sent_as_a_video_block(mimo):
    result = await _client(mimo).describe(data=RED_PNG, media_type="video/mp4")
    assert result.available, result.reason
    assert "video_url" in _block_types(mimo.requests[-1])
    assert "image_url" not in _block_types(mimo.requests[-1])


async def test_an_image_is_still_sent_exactly_as_before(mimo):
    """The shipped path must not move. Same block, same data URI."""
    await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert _block_types(mimo.requests[-1]) == ["text", "image_url"]
    url = mimo.requests[-1]["messages"][-1]["content"][-1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


async def test_an_audio_format_the_api_cannot_name_fails_closed(mimo):
    """`input_audio.format` is a short token the API defines, not a MIME
    subtype, so a subtype we cannot map is a payload we cannot build. That is
    `unavailable` BEFORE any request — never a guessed token, and never a
    doomed round trip whose 400 arrives as an indistinguishable `error`."""
    result = await _client(mimo).describe(data=RED_PNG, media_type="audio/basic")
    assert not result.available
    assert result.reason == "unsupported_payload"
    assert mimo.requests == []


async def test_each_modalitys_prompt_reaches_the_gateway(mimo):
    """`DESCRIBE_PROMPTS` had no test at all: `media_fetch` never passes a
    prompt, so selection happens inside this client — and every test that saw a
    non-image replaced this client with a stub, making the assertion vacuous."""
    await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert mimo.requests[-1]["messages"][-1]["content"][0]["text"] == DESCRIBE_PROMPTS["image"]
    await _client(mimo).describe(data=RED_PNG, media_type="audio/mpeg")
    assert mimo.requests[-1]["messages"][-1]["content"][0]["text"] == DESCRIBE_PROMPTS["audio"]
    await _client(mimo).describe(data=RED_PNG, media_type="video/mp4")
    assert mimo.requests[-1]["messages"][-1]["content"][0]["text"] == DESCRIBE_PROMPTS["video"]
