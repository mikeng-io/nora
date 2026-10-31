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

# The operator's settled settings. Asserted with the REAL numbers rather than
# round test ones: only at these values does a configured ceiling that never
# takes effect show.
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
        self.sessions: list[str] = []    # `x-opencode-session` per request
        self.require_session = False     # answer 400 MissingSessionID without
                                         # one, the way OpenCode Go does
        self.fail_times = 0          # how many leading calls answer 500
        self.stall_seconds = 0.0     # how long each call sleeps before answering
        self.empty_choices = False   # answer a well-formed 200 with `choices: []` —
                                     # the malformed-body case: real gateways do
                                     # answer this on some upstream failures
        self.text_only = False       # accept requests with no media block — the
                                     # real gateway answers text-only calls too;
                                     # `complete` is that lane
        self.reply = "A red square."

    async def completions(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.requests.append(body)
        self.auths.append(request.headers.get("Authorization", ""))
        self.sessions.append(request.headers.get("x-opencode-session", ""))
        if self.require_session and not self.sessions[-1]:
            # OpenCode Go's real answer.
            return web.json_response({"type": "error", "error": {
                "type": "MissingSessionID",
                "message": (
                    "Request is missing x-opencode-session and cannot be routed efficiently."
                ),
            }}, status=400)
        # Behave like the real server, INCLUDING its wrong-call shapes
        # (CLAUDE.md). A body with no media is a bad request, and so is media
        # carried in the wrong content block: a real OpenAI-compatible gateway
        # rejects `data:audio/aac;…` inside `image_url`, it does not describe it.
        #
        # Accepting ANY `image_url` whose url begins `data:` would let an audio
        # payload smuggled into an image block pass its negative control.
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
        if not self.text_only and not any(
                p.get("type") in {"image_url", "input_audio", "video_url"} for p in parts
        ):
            return web.json_response({"error": {"message": "no media"}}, status=400)
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        if len(self.requests) <= self.fail_times:
            return web.json_response({"error": {"message": "upstream boom"}}, status=500)
        if self.empty_choices:
            return web.json_response({
                "id": "cmpl-1", "object": "chat.completion", "created": 0,
                "model": body.get("model", "opencode-go/mimo-v2.5"),
                "choices": [],
            })
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

    The client derives its per-call ceiling (`_call_timeout`) rather than being
    handed one, so every scenario below must say what loop it is inside.
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
        direct_clients={"kimi": AsyncOpenAI(base_url=fake.url, api_key="kimi-key", max_retries=0)},  # type: ignore[attr-defined]
        model="kimi-code/k3-256k",
        max_tokens=1024,
        reasoning_effort="low",
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        loop_timeout_seconds=300.0,
    )


# ---- the derived timeout is provably shorter than the loop budget -----------

# `(360.0, 120.0)` and `(120.0, 40.0)` are NOT filler: they are the operator's
# settled pair and config.example.toml's own pair — the only two points in this
# file where `configured` is SMALLER than `loop * 0.6 / waves`, i.e. where the
# `min(configured, per_call)` actually binds. Every other pair here has the
# derived value below `configured`, so the sweep alone passes with the `min`
# deleted outright.
@pytest.mark.parametrize(
    "loop,configured",
    [(30.0, 20.0), (30.0, 999.0), (10.0, 8.0), (8.0, 20.0), (360.0, 120.0), (120.0, 40.0)],
)
@pytest.mark.parametrize("windows", [1, 2, 3, 10])
@pytest.mark.parametrize("concurrency", [1, 2, 4])
def test_the_waves_of_one_read_together_stay_under_the_loop(
    loop, configured, windows, concurrency,
):
    """The WALL-CLOCK form of the loop bound: one call < loop is not
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

    # THE CEILING MUST ACTUALLY BIND when it is the smaller of the two. Without
    # this the `min(configured, per_call)` could be deleted outright and this
    # test would stay green: with the parameters above, `per_call` is already
    # below `configured`, so `derived <= configured` holds either way.
    assert derived == min(configured, loop * VISION_LOOP_SHARE / waves)
    assert derived <= configured, "the configured value is a CEILING, never a floor"
    if configured < loop * VISION_LOOP_SHARE / waves:
        assert derived == configured, (
            "the configured value is the smaller of the two, so it must be what "
            "binds — a derived value below it means the ceiling was bypassed"
        )
    # `+ 1e-9` is an ULP allowance, not slack in the invariant. The left side
    # re-associates `loop * SHARE / waves` by multiplying back, and IEEE-754
    # does not promise that round trip is exact — `8.0 * 0.6` is already
    # 4.799999999999999822. The bound that a turn actually dies on is the
    # strict one below, against the LOOP, and it has the whole 0.4 share of
    # headroom.
    assert waves * derived <= loop * VISION_LOOP_SHARE + 1e-9
    assert waves * derived < loop            # headroom for the text turns


def test_the_derived_timeout_honours_the_configured_value_at_the_instagram_window_count():
    """The configured ceiling must take effect when one window is read.

    An Instagram Reel, Story or Post is 90 seconds or less, so `cut_video`
    produces ONE window and one invocation runs ONE wave. At the operator's
    settled settings the configured 120 must therefore be what binds — the
    whole point of setting it.

    Dividing by the `media_fetch` budget (4) would derive
    `min(120, 360 * 0.6 / 4) = 54 s`, cutting off the slower reads of a 90-second window
    that the configured ceiling exists to allow.
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
    """The loop protection, on the quantity that actually elapses.

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


def test_the_client_derives_one_ceiling_per_wave_count(mimo):
    """`_call_timeout` is the path the CLIENT actually uses — the pure function
    above is only half the change.

    The other tests in this file call `derive_vision_timeout`/`vision_waves`
    directly and never touch the client's own derivation; this one guards the
    wiring that decides `windows=None` vs a real count.

    Asserts on the client's own ceiling, not on the function's: a `windows=None`
    read (image, voice note, the text pass) is one wave; a ten-window read is
    ten, so it gets a tenth of the loop share.
    """
    # `_client`'s own default is `timeout_seconds=5.0`, which IS `configured` —
    # so the operator's pair has to be passed explicitly, or this measures
    # the helper's default rather than the deployment's numbers.
    client = _client(
        mimo, timeout_seconds=OPERATOR_CONFIGURED, loop_timeout_seconds=OPERATOR_LOOP,
    )
    assert client._call_timeout(None) == OPERATOR_CONFIGURED, (
        "a single-view read must honour the configured ceiling"
    )
    assert client._call_timeout(1) == client._call_timeout(None), (
        "one window is one wave — it must derive the same ceiling as a "
        "single-view read, or a 90 s Instagram clip and a photo disagree"
    )
    long_read = client._call_timeout(10)
    assert long_read < client._call_timeout(1), (
        "ten windows must derive a tighter per-window ceiling than one"
    )
    assert long_read == pytest.approx(OPERATOR_LOOP * 0.6 / 10)


def test_the_media_fetch_budget_is_not_an_input_to_the_derivation():
    """The `media_fetch` budget is not an input to the derived timeout.

    Four `media_fetch` calls in one turn run CONCURRENTLY. They occupy
    `max(their durations)` of wall clock, not the sum, so dividing a wall-clock
    ceiling by their count would shrink it by a factor corresponding to no
    elapsed time at all. `budget` bounds REFUSALS (DEC-10's count), not seconds.

    There is no `budget=` parameter to pass — this test states the property the
    signature enforces, in the terms an operator raising
    `[llm.budgets] media_fetch` would ask it in.
    """
    # The property is enforced by the SIGNATURE, so assert the signature. A test
    # parametrised over `budget` whose body never reads it would run identical
    # computations and prove nothing.
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

    `waves = ceil(windows / concurrency)`. If `read_video_windows` grows a bounded
    `asyncio.gather` while the constant stays 1, every window gets a ceiling derived
    for a serial read — ten times tighter than the wall clock actually permits, and
    every long clip starts timing out. If the constant rises while the loop stays
    sequential, the invariant this file proves is simply false.

    Read from the SOURCE rather than by timing the calls: the property is «these
    two agree», which no run of the current sequential code can falsify.
    """
    source = inspect.getsource(read_video_windows)
    # CODE only, comments stripped. `read_video_windows`'s own comment NAMES
    # `asyncio.gather` — it explains that this guard exists and what moving the
    # two together would mean — so a substring check reads that explanation as
    # the bug it warns about. The sibling test
    # (`test_composition_hands_the_client_the_derivations_inputs_not_a_budget`)
    # strips comments the same way.
    code = "\n".join(
        ln for ln in source.splitlines() if not ln.strip().startswith("#")
    )
    # EVERY way a window read can fan out, not just the one this file happens to
    # name. Checking only `"gather" not in code` lets a `TaskGroup` fan-out walk
    # straight through while genuinely reading every window at once. A guard that
    # only knows one spelling of the thing it guards is decoration.
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
        # against a purely sequential loop.
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
    """A byte ceiling below `media.max_bytes`: over it, `too_large` and NO
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
    """The wiring half, read from COMPOSITION'S OWN SOURCE.

    The budget counts calls that OVERLAP, and a wall-clock ceiling must not be
    divided by those, so composition does not feed `media_fetch`'s budget to
    `derive_vision_timeout`;
    `test_the_media_fetch_budget_is_not_an_input_to_the_derivation` states that
    property. The budget is resolved through `budgets()` at `tool_registry`, for
    the refusal ceiling it genuinely bounds.

    Composition hands the client the derivation's INPUTS, because the divisor
    (the window count) is only known per request: it comes from `cut_video`,
    which runs a process after this object is built. Reading the source is the
    only way to see that wiring; a test that built its own client would be true
    by construction."""
    source = (Path(__file__).resolve().parents[1]
              / "nora" / "app" / "composition.py").read_text()
    call = source[source.index("vision_client = VisionClient("):]
    call = call[:call.index("\n    )")]
    # CODE only. The comment above the arguments names the budget that is not
    # wired, and a substring check would read that as the budget being wired.
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
    assert "direct_clients=_direct_describer_clients(cfg)" in call, (
        "composition no longer hands VisionClient its DIRECT lanes — every "
        "kimi and opencode-go read would answer unavailable, with no test red "
        "(DEC-197 review)"
    )
    assert "budget" not in call, (
        "a budget term is back in the vision timeout wiring — budgeted calls "
        "overlap in time and must not divide a wall-clock ceiling "
        "(design/media-read-lane/07-timeout-derivation.md §2)"
    )


# ---- the per-modality model override ----------------------------------------

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
    """A call that names no model reaches the configured one."""
    await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert mimo.requests[-1]["model"] == "opencode-go/mimo-v2.5"


# ---- the payload shape per modality -----------------------------------------

def _block_types(body: dict) -> list[str]:
    return [p.get("type") for p in body["messages"][-1]["content"] if isinstance(p, dict)]


async def test_audio_is_sent_as_an_input_audio_block(mimo):
    """Each modality is shipped in its own content block: shipping audio in an
    `image_url` block sends `data:audio/aac;…` as an image and the gateway 400s.

    Asserted against what the fake gateway RECEIVED, and the fake rejects media
    in the wrong block, so this cannot pass by agreeing with itself.
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
    """`DESCRIBE_PROMPTS` selection happens inside this client, because
    `media_fetch` never passes a prompt.

    Each leg asserts ITS OWN outcome and that the request count grew by
    exactly one BEFORE looking at the prompt text: under `make gate`'s
    parallel load the video leg's preprocessing can fail or time out, so
    `describe` returns without calling the gateway and `requests[-1]` is still
    the PRIOR leg's request. Checked this way, that reads as a video
    preprocessing failure, not as a wrong prompt."""
    result = await _client(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available, result.reason
    assert len(mimo.requests) == 1
    assert mimo.requests[-1]["messages"][-1]["content"][0]["text"] == DESCRIBE_PROMPTS["image"]

    result = await _client(mimo).describe(data=RED_PNG, media_type="audio/mpeg")
    assert result.available, result.reason
    assert len(mimo.requests) == 2
    assert mimo.requests[-1]["messages"][-1]["content"][0]["text"] == DESCRIBE_PROMPTS["audio"]

    result = await _client(mimo).describe(data=RED_PNG, media_type="video/mp4")
    assert result.available, result.reason
    assert len(mimo.requests) == 3
    assert mimo.requests[-1]["messages"][-1]["content"][0]["text"] == DESCRIBE_PROMPTS["video"]


# ---- provider routing: kimi direct lane -------------------------------------

async def test_a_kimi_provider_without_a_direct_client_fails_closed(mimo):
    """A `provider="kimi"` request on a client with NO direct client is a
    WIRING GAP — the composition root failed to inject the key — and must be
    `unavailable`, not a silent fallback to the omniroute client. That fallback
    is exactly how a video gets sent to a router that drops `video_url` and
    "answers" about the text it did receive: an ERROR reported as a PASS."""
    client = _client(mimo)  # no direct clients
    result = await client.describe(
        data=RED_PNG, media_type="video/mp4", provider="kimi",
    )
    assert result.available is False
    assert result.reason == "error"
    assert mimo.requests == [], "no request may travel through the wrong client"


async def test_a_kimi_provider_uses_the_direct_client(mimo):
    """The kimi request must hit its direct client, and its body must carry the same
    media block an omniroute request would — only the transport differs. The fake
    records BOTH the body and the Authorization header (FakeMimo.auths);
    `_client_kimi` gives each client its own key, so the bearer key proves WHICH
    client travelled — a silent regression to the omniroute client would carry
    `Bearer omniroute-key` and go red."""
    result = await _client_kimi(mimo).describe(
        data=RED_PNG, media_type="video/mp4", provider="kimi",
    )
    assert result.available, result.reason
    assert len(mimo.requests) == 1
    assert "video_url" in _block_types(mimo.requests[-1])
    auth = mimo.auths[-1] if mimo.auths else ""
    assert "kimi-key" in auth, f"expected the kimi direct client to travel, got auth {auth!r}"


async def test_the_default_provider_is_still_omniroute(mimo):
    """A caller that never names a provider travels through the gateway
    client. The bearer key carried by the request proves it used the omniroute
    client."""
    result = await _client_kimi(mimo).describe(data=RED_PNG, media_type="image/png")
    assert result.available, result.reason
    assert len(mimo.requests) == 1
    assert "image_url" in _block_types(mimo.requests[-1])
    auth = mimo.auths[-1] if mimo.auths else ""
    assert "omniroute-key" in auth, f"expected the omniroute client to travel, got auth {auth!r}"


# ---- provider routing (DEC-197): OpenCode Go direct lane --------------------

async def test_an_opencode_go_provider_without_its_client_fails_closed(mimo):
    """A kimi client being present must not satisfy an `opencode-go` request:
    each direct lane is its own transport, and a lane the composition root did
    not build (its key was unset) is a wiring gap — `unavailable`, with no
    request travelling through any other client."""
    result = await _client_kimi(mimo).describe(
        data=RED_PNG, media_type="video/mp4", provider="opencode-go",
    )
    assert result.available is False
    assert result.reason == "error"
    assert mimo.requests == [], "no request may travel through the wrong client"


async def test_an_opencode_go_provider_travels_its_own_direct_client(mimo):
    """Three clients on one fake, three keys: the bearer proves the
    `opencode-go` request left through the opencode-go client, not kimi's and
    not the gateway's."""
    client = VisionClient(
        client=AsyncOpenAI(base_url=mimo.url, api_key="omniroute-key", max_retries=0),  # type: ignore[attr-defined]
        direct_clients={
            "kimi": AsyncOpenAI(base_url=mimo.url, api_key="kimi-key", max_retries=0),  # type: ignore[attr-defined]
            "opencode-go": AsyncOpenAI(base_url=mimo.url, api_key="go-key", max_retries=0),  # type: ignore[attr-defined]
        },
        model="mimo-v2.6-flash", max_tokens=4096, reasoning_effort="low",
        max_bytes=8 * 1024 * 1024, timeout_seconds=5.0, loop_timeout_seconds=300.0,
    )
    result = await client.describe(
        data=RED_PNG, media_type="video/mp4", provider="opencode-go",
    )
    assert result.available, result.reason
    assert "go-key" in mimo.auths[-1], mimo.auths


async def test_the_client_built_by_composition_sends_the_session_header(mimo, monkeypatch):
    """The client COMPOSITION builds — not one constructed here — must carry a fresh
    `x-opencode-session` on every request, because OpenCode Go answers
    `400 MissingSessionID` without one. The fake refuses exactly like the real
    endpoint, so a lane built without the hook fails this as an unavailable
    read, not merely a missing header.

    The client is repointed at the fake with `with_options(base_url=…)`, which
    keeps the transport (and so the hook) composition gave it."""
    from nora.app.composition import _direct_describer_clients
    from nora.config import Config, MediaConfig

    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-key")
    cfg = Config.model_construct(media=MediaConfig(describe={
        "video": {"enabled": True, "model": "mimo-v2.6-flash", "provider": "opencode-go"},
    }))
    built = _direct_describer_clients(cfg)["opencode-go"]
    assert str(built.base_url).startswith("https://opencode.ai/zen/go/v1")

    mimo.require_session = True
    client = VisionClient(
        client=AsyncOpenAI(base_url=mimo.url, api_key="omniroute-key", max_retries=0),  # type: ignore[attr-defined]
        direct_clients={"opencode-go": built.with_options(base_url=mimo.url, max_retries=0)},  # type: ignore[attr-defined]
        model="mimo-v2.6-flash", max_tokens=4096, reasoning_effort="low",
        max_bytes=8 * 1024 * 1024, timeout_seconds=5.0, loop_timeout_seconds=300.0,
    )
    for _ in range(2):
        result = await client.describe(
            data=RED_PNG, media_type="video/mp4", provider="opencode-go",
        )
        assert result.available, result.reason
    assert all(s.startswith("ses_") for s in mimo.sessions), mimo.sessions
    assert len(set(mimo.sessions)) == 2, "each request carries its OWN session id"


# ---- the enclosing turn's deadline bounds a read (DEC-197) -------------------
#
# `derive_vision_timeout` bounds ONE invocation. Reads the model issues in
# SEPARATE loop turns run one after another and add up — three one-window reads
# at 120 s are the whole of a 360 s loop — and a heartbeat turn runs under 120 s,
# not the `[llm]` loop this client is built from.

def test_a_read_takes_at_most_the_share_of_what_is_left_of_the_turn():
    from nora.domain.turn_deadline import turn_deadline

    client = VisionClient(
        client=AsyncOpenAI(base_url="http://127.0.0.1:1/v1", api_key="k", max_retries=0),
        model="m", max_tokens=16, reasoning_effort="low", max_bytes=1,
        timeout_seconds=120.0, loop_timeout_seconds=360.0,
    )
    assert client._call_timeout(None) == 120.0, "outside a turn: the derivation alone"
    with turn_deadline(100.0):
        assert 59.0 < client._call_timeout(None) <= 60.0, "0.6 of the 100 s left"
    with turn_deadline(1000.0):
        assert client._call_timeout(None) == 120.0, "never MORE than the derivation"


async def test_a_heartbeat_sized_turn_cuts_a_slow_read_short(mimo):
    """Behaviourally, through `describe`: a read inside a turn with 0.5 s left
    gives up at ~0.3 s although its own ceiling is 5 s, and says `timeout`."""
    import time as _time

    from nora.domain.turn_deadline import turn_deadline

    mimo.stall_seconds = 3.0
    started = _time.monotonic()
    with turn_deadline(0.5):
        result = await _client(mimo, timeout_seconds=5.0).describe(
            data=RED_PNG, media_type="image/png",
        )
    assert result.available is False
    assert result.reason == "timeout"
    assert _time.monotonic() - started < 1.5


async def test_describe_hands_its_window_count_to_the_ceiling(mimo, monkeypatch):
    """`describe(windows=10)` must reach
    `wait_for` with the ten-window ceiling (360 x 0.6 / 10 = 21.6 s), not the
    one-window 120 — a `describe` that ignored `windows` would pass every other test."""
    from nora.infra.vision import client as client_module

    seen: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy(aw, **kwargs):
        seen.append(kwargs["timeout"])
        return await real_wait_for(aw, **kwargs)

    monkeypatch.setattr(client_module.asyncio, "wait_for", spy)
    client = _client(mimo, timeout_seconds=120.0, loop_timeout_seconds=360.0)
    await client.describe(data=RED_PNG, media_type="video/mp4", windows=10)
    await client.describe(data=RED_PNG, media_type="video/mp4")
    assert seen == [pytest.approx(21.6), 120.0]


# ---- speech is asked for on the live look too (DEC-197) ----------------------

async def test_the_live_look_asks_for_what_is_said_in_audio_and_video(mimo):
    """`DESCRIBE_PROMPTS` is the `media_fetch` family and asks for speech on the
    live look. Asserted on the body the endpoint RECEIVED, not by comparing the
    request to the constant itself."""
    client = _client(mimo)
    await client.describe(data=b"ID3audio", media_type="audio/mpeg")
    await client.describe(data=RED_PNG, media_type="video/mp4")
    audio_prompt, video_prompt = (
        r["messages"][-1]["content"][0]["text"] for r in mimo.requests
    )
    for prompt in (audio_prompt, video_prompt):
        assert "transcribe what" in prompt, prompt
        assert "phone number, ID number or account number" in prompt, prompt
        assert "rather than writing out" not in prompt
        assert "word-for-word" not in prompt


async def test_a_direct_lane_with_no_key_sends_nothing_anywhere(mimo, monkeypatch):
    """End to end: composition with the lane's key
    UNSET, then a read through that lane — `unavailable`, and ZERO requests
    reach the gateway client (an OmniRoute fallback would drop the video and
    answer about the text it did receive)."""
    from nora.app.composition import _direct_describer_clients
    from nora.config import Config, MediaConfig

    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    cfg = Config.model_construct(media=MediaConfig(describe={
        "video": {"enabled": True, "model": "mimo-v2.6-flash", "provider": "opencode-go"},
    }))
    client = VisionClient(
        client=AsyncOpenAI(base_url=mimo.url, api_key="omniroute-key", max_retries=0),  # type: ignore[attr-defined]
        direct_clients=_direct_describer_clients(cfg),
        model="mimo-v2.6-flash", max_tokens=4096, reasoning_effort="low",
        max_bytes=8 * 1024 * 1024, timeout_seconds=5.0, loop_timeout_seconds=300.0,
    )
    result = await client.describe(
        data=RED_PNG, media_type="video/mp4", provider="opencode-go",
    )
    assert result.available is False
    assert mimo.requests == []


# ---- the model-call span closes exactly once on every path --------------------
#
# `describe` wraps its retry loop in ONE `asyncio.wait_for`: a timeout arrives
# inside `_create` as CancelledError (a BaseException), and a malformed body
# raises IndexError when `choices[0]` is indexed — both bypassed the old
# `except Exception`, and each left its span open forever. The recorder is the
# injected `VisionSpanRecorder` seam; the events ARE the assertion.


class _RecordingSpans:
    """A `VisionSpanRecorder` that logs lifecycle events instead of spans."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.errors: list[str] = []
        self.broken = False  # a recorder that raises on every method — the
                             # call must still answer, never inherit telemetry's
                             # failure

    def start(self, *, messages, model_id, provider):
        if self.broken:
            raise RuntimeError("recorder down")
        self.events.append("start")
        return {"token": len(self.events)}  # opaque to the client, like a Span

    def finish(self, span, **kwargs):
        if self.broken:
            raise RuntimeError("recorder down")
        self.events.append("finish")

    def fail(self, span, *, error):
        if self.broken:
            raise RuntimeError("recorder down")
        self.events.append("fail")
        self.errors.append(type(error).__name__)


def _client_spans(
    fake: FakeMimo, recorder: _RecordingSpans, *,
    timeout_seconds: float = 5.0, max_bytes: int = 8 * 1024 * 1024,
) -> VisionClient:
    return VisionClient(
        client=AsyncOpenAI(base_url=fake.url, api_key="test-key", max_retries=0),  # type: ignore[attr-defined]
        model="opencode-go/mimo-v2.5",
        max_tokens=1024,
        reasoning_effort="low",
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        loop_timeout_seconds=300.0,
        span_recorder=recorder,
    )


async def test_a_successful_describe_closes_its_span_exactly_once(mimo):
    rec = _RecordingSpans()
    result = await _client_spans(mimo, rec).describe(
        data=RED_PNG, media_type="image/png")
    assert result.available is True
    assert rec.events == ["start", "finish"]


async def test_a_timed_out_describe_fails_its_span_it_never_leaks(mimo):
    """wait_for's cancellation is CancelledError — a BaseException the old
    `except Exception` let through, leaving the span open forever."""
    mimo.stall_seconds = 2.0
    rec = _RecordingSpans()
    result = await _client_spans(mimo, rec, timeout_seconds=0.3).describe(
        data=RED_PNG, media_type="image/png")
    assert result.available is False
    assert result.reason == "timeout"
    assert rec.events == ["start", "fail"]
    assert rec.errors == ["CancelledError"]


async def test_a_transport_failure_fails_each_attempts_span(mimo):
    """Both the first call and the one bounded retry open AND fail a span —
    a persistent failure is not one span leaked per attempt."""
    mimo.fail_times = 99
    rec = _RecordingSpans()
    result = await _client_spans(mimo, rec).describe(
        data=RED_PNG, media_type="image/png")
    assert result.available is False
    assert result.reason == "error"
    assert rec.events == ["start", "fail", "start", "fail"]


async def test_an_empty_choices_answer_fails_each_span(mimo):
    """`choices[0]` on a malformed body raises IndexError — outside the old
    guarded block, so each attempt leaked its span while still retrying."""
    mimo.empty_choices = True
    rec = _RecordingSpans()
    result = await _client_spans(mimo, rec).describe(
        data=RED_PNG, media_type="image/png")
    assert result.available is False
    assert result.reason == "error"
    assert rec.events == ["start", "fail", "start", "fail"]
    assert rec.errors == ["IndexError", "IndexError"]


async def test_a_refused_or_oversized_read_opens_no_span(mimo):
    """No model call, no span: the byte ceiling and the missing-direct-client
    wiring gap return before `start` is ever reached."""
    rec = _RecordingSpans()
    oversized = _client_spans(mimo, rec, max_bytes=len(RED_PNG) - 1)
    result = await oversized.describe(data=RED_PNG, media_type="image/png")
    assert result.reason == "too_large"
    no_lane = _client_spans(mimo, rec)
    result = await no_lane.describe(
        data=RED_PNG, media_type="image/png", provider="kimi")
    assert result.available is False
    assert rec.events == []


async def test_a_broken_recorder_never_costs_the_answer(mimo):
    """Telemetry is never load-bearing: a recorder that raises on every call
    must not turn a good read into `unavailable`."""
    rec = _RecordingSpans()
    rec.broken = True
    result = await _client_spans(mimo, rec).describe(
        data=RED_PNG, media_type="image/png")
    assert result.available is True
    assert result.description == mimo.reply


async def test_a_successful_complete_closes_its_span_exactly_once(mimo):
    """`complete` is the text-only sibling path — same one-span contract."""
    mimo.text_only = True
    rec = _RecordingSpans()
    text = await _client_spans(mimo, rec).complete(
        prompt="Describe this transcript.", text="a synthetic transcript")
    assert text == mimo.reply
    assert rec.events == ["start", "finish"]


async def test_an_externally_cancelled_complete_fails_its_span(mimo):
    """`complete` under an OUTER cancellation — a caller's own wait_for or a
    cancelled task — gets CancelledError at its await: a BaseException the
    old `except Exception` let escape with the span left open. Its own inner
    wait_for raises TimeoutError (an Exception), which the old code already
    caught — only the external path was leaking."""
    mimo.text_only = True
    mimo.stall_seconds = 2.0
    rec = _RecordingSpans()
    client = _client_spans(mimo, rec, timeout_seconds=5.0)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            client.complete(
                prompt="Describe.", text="a synthetic transcript"),
            timeout=0.3,
        )
    assert rec.events == ["start", "fail"]
    assert rec.errors == ["CancelledError"]
