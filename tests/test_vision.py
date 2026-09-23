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
import inspect
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from openai import AsyncOpenAI

from nora.infra.vision import (
    DESCRIBE_PROMPTS,
    VISION_LOOP_SHARE,
    VISION_WINDOW_CONCURRENCY,
    VisionClient,
    derive_vision_timeout,
    read_video_windows,
    vision_waves,
)

# The operator's settled settings (design/media-read-lane/07-timeout-derivation
# .md §1). Asserted with the REAL numbers rather than round test ones, because
# the defect being fixed was invisible at any other triple: at loop 360,
# configured 120 and budget 4 the old formula derived 54 s, and nothing in the
# suite looked at the configured value to notice it never took effect.
OPERATOR_LOOP = 360.0
OPERATOR_CONFIGURED = 120.0

# A 1x1 red PNG — real bytes, so the base64 data URI the client builds is a real
# one and the server can assert it received an image, not a placeholder.
RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeMimo:
    """A stand-in for the OmniRoute chat-completions endpoint."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.auths: list[str] = []       # Authorization header per request — the
                                         # only way a test can tell WHICH client
                                         # (omniroute vs kimi direct) sent a call
                                         # when both point at the same fake.
        self.fail_times = 0          # how many leading calls answer 500
        self.stall_seconds = 0.0     # how long each call sleeps before answering
        self.reply = "A red square."

    async def completions(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        self.auths.append(request.headers.get("Authorization", ""))
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
    loop_timeout_seconds: float = 300.0,
) -> VisionClient:
    """A client whose LOOP ceiling is wide enough not to bind.

    The client derives its per-call ceiling now (`_call_timeout`) instead of
    being handed one, so every scenario below must say what loop it is inside.
    300 s x 0.6 = 180 s over one wave, so `timeout_seconds` is what binds and
    each test's explicit value — including the 0.3 s the hang test needs — is
    the one that reaches `wait_for`. The derivation's own arithmetic is asserted
    directly, not through a live server.
    """
    return VisionClient(
        client=AsyncOpenAI(base_url=fake.url, api_key="test-key", max_retries=0),
        model="opencode-go/mimo-v2.5",
        max_tokens=1024,
        reasoning_effort="low",
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        loop_timeout_seconds=loop_timeout_seconds,
    )


def _client_kimi(
    fake: FakeMimo, *, timeout_seconds: float = 5.0, max_bytes: int = 8 * 1024 * 1024,
) -> VisionClient:
    """The provider-aware shape: omniroute client + a DIRECT kimi client.

    Both point at the same local fake so the test asserts WHICH one got the
    call (the fake records requests), not what the fake answered.
    """
    return VisionClient(
        client=AsyncOpenAI(base_url=fake.url, api_key="omniroute-key", max_retries=0),  # type: ignore[attr-defined]
        direct_client=AsyncOpenAI(base_url=fake.url, api_key="kimi-key", max_retries=0),  # type: ignore[attr-defined]
        model="kimi-code/k3-256k",
        max_tokens=1024,
        reasoning_effort="low",
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        loop_timeout_seconds=300.0,
    )


# ---- the derived timeout is provably shorter than the loop budget -----------

@pytest.mark.parametrize("loop,configured", [(30.0, 20.0), (30.0, 999.0), (10.0, 8.0), (8.0, 20.0)])
@pytest.mark.parametrize("windows", [1, 2, 3, 10])
@pytest.mark.parametrize("concurrency", [1, 2, 4])
def test_the_waves_of_one_read_together_stay_under_the_loop(
    loop, configured, windows, concurrency,
):
    """The blocker (§2/§6.2) in its WALL-CLOCK form: one call < loop is not
    enough, because a video is cut into up to ten windows and one `media_fetch`
    awaits them in sequence. If the waves together outlast the loop's shared
    `wait_for`, the last of them is the loop's silent `invocation_timeout`
    (R-5).

    Dividing the per-call ceiling by the SERIAL factor — `ceil(windows /
    concurrency)` — guarantees `waves * derived <= loop * VISION_LOOP_SHARE <
    loop` for ANY configured value, so a 999 s knob still cannot outlive the
    loop. Reverting to a per-call fraction (not dividing at all) turns every
    `waves >= 2` case red.
    """
    waves = vision_waves(windows=windows, concurrency=concurrency)
    derived = derive_vision_timeout(
        loop_timeout_seconds=loop, configured=configured, waves=waves,
    )
    assert derived > 0
    assert derived <= configured, "the configured value is a CEILING, never a floor"
    # `+ 1e-9` is an ULP allowance, not slack in the invariant. The left side
    # re-associates `loop * SHARE / waves` by multiplying back, and IEEE-754
    # does not promise that round trip is exact — `8.0 * 0.6` is already
    # 4.799999999999999822. The bound that a turn actually dies on is the
    # strict one below, against the LOOP, and it has the whole 0.4 share of
    # headroom.
    assert waves * derived <= loop * VISION_LOOP_SHARE + 1e-9
    assert waves * derived < loop            # headroom for the text turns


def test_the_derived_timeout_honours_the_configured_value_at_the_instagram_window_count():
    """THE REGRESSION THAT WOULD HAVE CAUGHT THE INERT 120.

    An Instagram Reel, Story or Post is 90 seconds or less, so `cut_video`
    produces ONE window and one invocation runs ONE wave. At the operator's
    settled settings the configured 120 must therefore be what binds — the
    whole point of setting it.

    The old formula divided by the `media_fetch` budget (4) and derived
    `min(120, 360 * 0.6 / 4) = 54 s`, against a measured 90-second-window median
    of ~41.6 s with 13 of 19 runs over 36 s. Roughly a third of reads degraded
    on a ceiling that existed to remove exactly that failure, and the suite had
    nothing that looked at whether `configured` ever took effect
    (design/media-read-lane/07-timeout-derivation.md §5.2).
    """
    derived = derive_vision_timeout(
        loop_timeout_seconds=OPERATOR_LOOP,
        configured=OPERATOR_CONFIGURED,
        waves=vision_waves(windows=1),
    )
    assert derived == OPERATOR_CONFIGURED, (
        f"one window must honour the configured {OPERATOR_CONFIGURED}s in full, "
        f"got {derived}s — a divisor that is not the serial factor is back"
    )


def test_a_long_clip_tightens_the_per_call_timeout():
    """The protection the old formula was reaching for, on the right quantity.

    Ten windows is `VIDEO_MAX_CHUNKS` — fifteen minutes of clip — read one after
    another, so each window gets a tenth of the share and the invocation still
    fits inside the loop. Honouring the configured 120 for the short case must
    not mean handing a fifteen-minute video ten x 120 s.
    """
    derived = derive_vision_timeout(
        loop_timeout_seconds=OPERATOR_LOOP,
        configured=OPERATOR_CONFIGURED,
        waves=vision_waves(windows=10),
    )
    assert derived == pytest.approx(21.6)
    assert derived < OPERATOR_CONFIGURED
    assert 10 * derived < OPERATOR_LOOP


def test_the_media_fetch_budget_is_not_an_input_to_the_derivation():
    """THE INVERTED TEST. Its ancestor —
    `test_the_budget_of_vision_calls_together_stays_under_the_loop` — asserted
    `budget * derived < loop` and so PINNED the defect: it proved a property of
    a quantity that never elapses.

    Four `media_fetch` calls in one turn run CONCURRENTLY. They occupy
    `max(their durations)` of wall clock, not the sum, so dividing a wall-clock
    ceiling by their count shrank the result by a factor corresponding to no
    elapsed time at all. `budget` bounds REFUSALS (DEC-10's count), not seconds.

    So the assertion is now the negative one: the budget is not an input, and no
    value of it may move the number. There is no `budget=` parameter left to
    pass — this test states the property the signature enforces, in the terms an
    operator raising `[llm.budgets] media_fetch` would ask it in.
    """
    # The property is enforced by the SIGNATURE, so assert the signature. The
    # first version of this test was parametrised over `budget` and asserted
    # `derived == OPERATOR_CONFIGURED` — but its body never read the parameter,
    # so it ran five IDENTICAL computations and proved nothing five times. A
    # test that cannot see what it claims to measure is decoration; this reads
    # the thing that actually changed.
    params = inspect.signature(derive_vision_timeout).parameters
    assert "budget" not in params, (
        "`budget` is back in the derivation's signature — budgeted calls run "
        "concurrently and occupy max(their durations) of wall clock, never "
        "their sum, so it must not divide a wall-clock ceiling"
    )
    assert set(params) == {"loop_timeout_seconds", "configured", "waves"}, (
        f"the derivation's inputs changed: {sorted(params)}"
    )

    derived = derive_vision_timeout(
        loop_timeout_seconds=OPERATOR_LOOP,
        configured=OPERATOR_CONFIGURED,
        waves=vision_waves(windows=1),
    )
    assert derived == OPERATOR_CONFIGURED, (
        f"one window must honour the configured {OPERATOR_CONFIGURED}s, "
        f"got {derived}s"
    )


def test_the_window_fan_out_and_the_wave_divisor_cannot_drift_apart():
    """`VISION_WINDOW_CONCURRENCY` is the divisor's companion, and this is what
    stops the two from separating.

    `waves = ceil(windows / concurrency)`. If `read_video_windows` grows a
    bounded `asyncio.gather` (design/media-read-lane/01-chunk-parallelism.md)
    while the constant stays 1, every window gets a ceiling derived for a serial
    read — ten times tighter than the wall clock actually permits, and every
    long clip starts timing out. If the constant rises while the loop stays
    sequential, the invariant this file proves is simply false.

    Read from the SOURCE rather than by timing the calls: the property is «these
    two agree», which no run of the current sequential code can falsify.
    """
    source = inspect.getsource(read_video_windows)
    # CODE only, comments stripped. `read_video_windows`'s own comment NAMES
    # `asyncio.gather` — it explains that this guard exists and what moving the
    # two together would mean — so a substring check reads that explanation as
    # the bug it warns about. This file's sibling test
    # (`test_composition_hands_the_client_the_derivations_inputs_not_a_budget`)
    # hit the identical trap and solved it the identical way; stripping is the
    # house answer, not a local workaround.
    code = "\n".join(
        ln for ln in source.splitlines() if not ln.strip().startswith("#")
    )
    # EVERY way a window read can fan out, not just the one this file happens to
    # name. The first version checked only `"gather" not in code`, and a
    # `TaskGroup` fan-out walks straight through it: verified by injecting
    # `async with asyncio.TaskGroup() ... create_task(ask(...))` into
    # `read_video_windows`, which left this assertion GREEN while genuinely
    # reading every window at once — the exact defect the test exists to catch.
    # A guard that only knows one spelling of the thing it guards is decoration.
    fan_out = ("gather", "TaskGroup", "asyncio.wait", "ensure_future", "create_task")
    if VISION_WINDOW_CONCURRENCY == 1:
        found = [k for k in fan_out if k in code]
        assert not found, (
            f"read_video_windows fans out ({', '.join(found)}) while "
            f"VISION_WINDOW_CONCURRENCY is 1 — the per-call ceiling is being "
            f"derived for a serial read that is no longer serial"
        )
    else:
        # `code`, NOT `source`: both required strings already occur in this
        # function's own comment block, so grepping the unstripped text passes
        # against a purely sequential loop — a second form of the same trap the
        # default branch above was fixed for. (Independent review round 1,
        # 2026-09-23: executed the committed body with concurrency 1/2/4 against
        # the committed sequential function and all three passed.)
        assert "gather" in code, (
            f"VISION_WINDOW_CONCURRENCY is {VISION_WINDOW_CONCURRENCY} but the "
            f"window loop is still sequential — the divisor claims headroom the "
            f"read does not take"
        )
        assert "VISION_WINDOW_CONCURRENCY" in code, (
            "the fan-out limit must BE the constant the divisor uses, not a "
            "second number that happens to match it today"
        )


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


def test_composition_hands_the_client_the_derivations_inputs_not_a_budget():
    """The wiring half of the fix, read from COMPOSITION'S OWN SOURCE.

    This test used to assert the opposite — that composition resolved
    `media_fetch`'s budget through `ToolRegistry.budgets()` and fed it to
    `derive_vision_timeout`. That wiring was correct about the SECOND-READER
    hazard it was written for (R-33: `[llm.budgets]` moving R-7's ceiling while
    the timeout still divided by the raw table) and wrong about the quantity:
    the budget counts calls that OVERLAP, and a wall-clock ceiling must not be
    divided by those at all.

    **Its second half is gone rather than adjusted.** It looped over
    `[llm.budgets]` overrides asserting `budget * per_call < loop` — a property
    of a quantity that no longer exists, since the budget is not an input to the
    derivation and `derive_vision_timeout` has no `budget=` parameter to pass.
    Keeping it would have meant re-deriving the concurrent count somewhere to
    multiply by, which is the defect wearing a test's clothes.
    `test_the_media_fetch_budget_does_not_move_the_derived_timeout` states what
    replaced it. The budget is still resolved through `budgets()` — at
    `tool_registry`, for the refusal ceiling it genuinely bounds — just not here.

    What composition must do now is hand the client the derivation's INPUTS,
    because the divisor (the window count) is only known per request: it comes
    from `cut_video`, which runs a process after this object is built. Reading
    the source is the only way to see that wiring; a test that built its own
    client would be true by construction (external review, 2026-08-11)."""
    source = (Path(__file__).resolve().parents[1]
              / "nora" / "app" / "composition.py").read_text()
    call = source[source.index("vision_client = VisionClient("):]
    call = call[:call.index("\n    )")]
    # CODE only. The comment above the arguments explains the defect by naming
    # the budget that was removed, and a substring check would read that as the
    # bug still being wired.
    call = "\n".join(ln for ln in call.splitlines() if not ln.strip().startswith("#"))

    assert "loop_timeout_seconds=tier.timeout_seconds" in call, (
        "composition no longer passes the loop ceiling to VisionClient — the "
        "client cannot derive a per-call timeout it is never told the loop for"
    )
    assert "timeout_seconds=cfg.media.vision_timeout_seconds" in call, (
        "composition no longer passes the CONFIGURED ceiling — the operator's "
        "vision_timeout_seconds would stop binding entirely"
    )
    assert "derive_vision_timeout" not in call, (
        "composition is deriving the timeout at construction again — the window "
        "count is not known until read time, so a number derived here is "
        "derived from the wrong wave count for every video"
    )
    assert "budget" not in call, (
        "a budget term is back in the vision timeout wiring — budgeted calls "
        "overlap in time and must not divide a wall-clock ceiling "
        "(design/media-read-lane/07-timeout-derivation.md §2)"
    )


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


async def test_a_wildcard_subtype_is_refused_before_the_request(mimo):
    """`image/*` is what a platform declaring no mimetype honestly records —
    Instagram's wire carries none anywhere. `nora.domain.media.
    ground_media_type` resolves it from the bytes at custody time; when
    nothing recognises the container the wildcard survives, and a
    `data:image/*;base64,…` URI declares a container no decoder can act on.
    A model handed one answers anyway, wrong, at a rate nothing reports — so
    this fails closed before the round trip, the way `audio/*` already did by
    accident (there is no `input_audio` format named `*`)."""
    for media_type in ("image/*", "video/*", "audio/*"):
        result = await _client(mimo).describe(data=RED_PNG, media_type=media_type)
        assert not result.available, media_type
        assert result.reason == "unsupported_payload", media_type
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


# ---- provider routing (2026-08-30): kimi direct lane ------------------------

async def test_a_kimi_provider_without_a_direct_client_fails_closed(mimo):
    """A `provider="kimi"` request on a client with NO direct client is a
    WIRING GAP — the composition root failed to inject the key — and must be
    `unavailable`, not a silent fallback to the omniroute client. That fallback
    is exactly how a video gets sent to a router that drops `video_url` and
    "answers" about the text it did receive: an ERROR reported as a PASS."""
    client = _client(mimo)  # no direct_client
    result = await client.describe(
        data=RED_PNG, media_type="video/mp4", provider="kimi",
    )
    assert result.available is False
    assert result.reason == "error"
    assert mimo.requests == [], "no request may travel through the wrong client"


async def test_a_kimi_provider_uses_the_direct_client(mimo):
    """The kimi request must hit `_direct_client`, and its body must carry the
    same media block an omniroute request would — the transport changed, the
    payload did not. The fake records BOTH the body and the Authorization
    header (FakeMimo.auths); `_client_kimi` gives each client its own key, so
    the bearer key proves WHICH client travelled — a silent regression to the
    omniroute client would carry `Bearer omniroute-key` and go red."""
    result = await _client_kimi(mimo).describe(
        data=RED_PNG, media_type="video/mp4", provider="kimi",
    )
    assert result.available, result.reason
    assert len(mimo.requests) == 1
    assert "video_url" in _block_types(mimo.requests[-1])
    auth = mimo.auths[-1] if mimo.auths else ""
    assert "kimi-key" in auth, f"expected the kimi direct client to travel, got auth {auth!r}"


async def test_the_default_provider_is_still_omniroute(mimo):
    """A caller that never names a provider (every one before 2026-08-30)
    keeps travelling through the gateway client — unchanged behaviour. The
    bearer key carried by the request proves it used the omniroute client."""
    result = await _client_kimi(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available, result.reason
    assert len(mimo.requests) == 1
    assert "image_url" in _block_types(mimo.requests[-1])
    auth = mimo.auths[-1] if mimo.auths else ""
    assert "omniroute-key" in auth, f"expected the omniroute client to travel, got auth {auth!r}"
