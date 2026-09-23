"""The MiMo-V2.5 vision call — a raw `chat.completions.create` under its OWN
timeout and byte ceiling (DEC-56, the READ half of media custody).

This is the one place a blob's bytes reach a model. It goes through the
process-wide OmniRoute `AsyncOpenAI` client (audit T2's single client), model
`opencode-go/mimo-v2.5`, the image passed as a base64 `data:` URI in an
`image_url` content block — the shape the 2026-08-07 probe verified end to end
(a red PNG → "Red", prompt_tokens 159).

**It is deliberately NOT an `OpenAIModel`.** A raw create gets none of strands'
retry/timeout/trace machinery, which means this module owns all three itself:

* **Its own timeout, PROVABLY shorter than the loop budget.** The whole agent
  loop — every text turn and every tool call, this one included — runs inside a
  single `asyncio.wait_for(timeout=llm.timeout_seconds)`. A MiMo *reasoning*
  call with a generous `max_tokens` is seconds, not `about`'s ~0.2 s, and a
  ten-window video read plus the text turns would overrun it → the loop's
  silent `invocation_timeout` (R-5). `derive_vision_timeout` below caps this
  call at a fixed fraction of the loop timeout DIVIDED BY THE WAVES one
  invocation awaits in sequence, so a slow or hung MiMo becomes THIS tool's
  `unavailable` inside the turn instead — the same discipline
  `factory.build_retry_policy` applies to its backoff.
* **A byte ceiling** (`vision_max_bytes`, config), far below `media.max_bytes`.
  Over it, `too_large` → the tool's `unavailable`, never a nine-figure request.
* **One bounded retry**, and it lives INSIDE the single timeout budget above, so
  the retry cannot push the total past the loop's `wait_for`.

Never raises: every failure — timeout, transport error, an answer in a shape
this reader does not understand — becomes a `VisionResult(available=False)` with
a `reason` for the metric label, the same `available` axis `MediaBytes` and
`Roster` carry so "could not describe" stays distinct from "described nothing".
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from math import ceil

from openai import AsyncOpenAI

from nora.domain.media import parse_media_type

log = logging.getLogger("nora.vision")

# How much of the loop's own timeout one vision call may spend. A fraction < 1
# is what MAKES the derived ceiling provably shorter than the loop's
# `asyncio.wait_for`, so a hung MiMo can never become the loop's silent
# `invocation_timeout`. The share is divided by the SERIAL factor in
# `derive_vision_timeout` — the waves one invocation awaits in sequence — so
# `waves * derived <= loop * VISION_LOOP_SHARE`, which is the wall clock that
# invocation actually consumes.
#
# It used to be divided by the tool's BUDGET, and that was a unit error
# (design/media-read-lane/07-timeout-derivation.md §2): budgeted calls run
# CONCURRENTLY and occupy `max(their durations)` of wall clock, never their
# sum, so dividing a wall-clock ceiling by them shrank the result by a factor
# corresponding to no elapsed time at all. At the settled settings — loop 360,
# `vision_timeout_seconds` 120, `media_fetch` budget 4 — it derived
# `min(120, 360 * 0.6 / 4) = 54 s` and the configured 120 never took effect,
# against a measured 90-second-window median of ~41.6 s. A third of reads
# degraded on the ceiling that existed to stop exactly that. `budget` bounds
# REFUSALS (DEC-10's count), not elapsed time; it has been REMOVED from this
# derivation and must not come back.
VISION_LOOP_SHARE = 0.6

# How many of ONE invocation's windows are read at once. One today: the fan-out
# in `comprehend.read_video_windows` is a sequential `for`, and this constant is
# that loop's width, written where the derivation can see it. It is the
# divisor's companion — `waves = ceil(windows / concurrency)` — so raising the
# fan-out without raising this (or the reverse) would silently void the
# invariant. `design/media-read-lane/01-chunk-parallelism.md` is where the
# bounded `asyncio.gather` lands; it must move this number and that loop in the
# same change, which
# `test_the_window_fan_out_and_the_wave_divisor_cannot_drift_apart` enforces.
VISION_WINDOW_CONCURRENCY = 1

# Authored, fixed, and small — never model-supplied. The description it asks for
# is untrusted egress (design/14 §2.1), screened by the tool on every serve.
_PROMPT = "Describe this image. If it contains text in it, transcribe that text."

# One authored prompt per modality (2026-08-17 spec). AUTHORED and never
# model-supplied, for the same reason `_PROMPT` above is: what comes back is
# untrusted egress (design/14 §2.1), screened by the tool on every serve.
# Selecting one of these by modality derives from a system-controlled fact — the
# blob's own MIME type — and keeps that property. Conditioning a prompt on what
# the TAGGER asked would not: that is group-authored text steering a model whose
# output returns to the room, i.e. an injection channel, and it is out of scope.
#
# `image` is `_PROMPT` unchanged, byte for byte, so the shipped path keeps its
# exact behaviour through this split.
DESCRIBE_PROMPTS = {
    "image": _PROMPT,
    # DELIBERATELY NOT a transcription prompt, and this is the load-bearing
    # line of the table. The first cut asked «Transcribe what is said in this
    # audio» — which IS speech-to-text, the capability this change excludes on
    # the grounds that a verbatim transcript is a materially larger R-18/R-28
    # egress surface than a caption. The only screen is `scan_identifiers`, and
    # `_serve_media`'s own docstring records that it does NOT catch a bare local
    # number; people say addresses and phone numbers aloud. Shipping the
    # transcription prompt while deferring the transcription safety pass would
    # have defeated the deferral in the same commit that made it.
    #
    # So: the GIST, explicitly not the words. When STT lands as
    # `[media.transcribe]` with its own egress analysis, that is where a
    # verbatim transcript belongs.
    "audio": (
        "Say briefly what this audio is about — the gist, the tone, and who is "
        "speaking if that is clear. Do NOT write out a word-for-word transcript, "
        "and do not repeat any phone number, address or account number you hear. "
        "If nothing is audible, say so."
    ),
    # What HAPPENS, plus on-screen text — the video analogue of `_PROMPT`'s
    # second clause, which already ships for images. Speech is deliberately
    # summarised rather than transcribed, for the audio reason above.
    "video": (
        "Describe what happens in this video, and transcribe any text on screen. "
        "If people speak, say briefly what it is about rather than writing out "
        "what was said."
    ),
}


# MIME subtype → the token `input_audio.format` takes. NOT the same vocabulary:
# the API names a container in its own short form, so `audio/mpeg` is `mp3`, and
# a subtype absent from this table is a payload we CANNOT BUILD rather than one
# to guess a token for (`unsupported_payload`, before any request).
#
# UNVERIFIED against a live route, and that is stated rather than glossed: no
# model in the OmniRoute catalogue advertises an `audio` capability at all (215
# of 406 advertise `vision`, none advertise audio or video), so there is nothing
# to probe this against today. `wav`/`mp3` are the two the OpenAI audio-input
# API documents; the rest are the containers gateways commonly accept and are
# the FIRST thing to check if enabling audio 400s.
_AUDIO_FORMATS = {
    "wav": "wav", "x-wav": "wav", "wave": "wav", "vnd.wave": "wav",
    "mpeg": "mp3", "mp3": "mp3", "mpeg3": "mp3", "x-mpeg-3": "mp3",
    "mp4": "m4a", "x-m4a": "m4a", "aac": "aac", "aacp": "aac",
    "ogg": "ogg", "opus": "opus", "webm": "webm", "flac": "flac", "x-flac": "flac",
}


def _checked(response: object, *, what: str) -> str:
    """The completion's text, or a raise — never a silently truncated body.

    A raw `chat.completions.create` does not raise on a cut-off the way a
    Strands agent's `MaxTokensReachedException` does: it returns
    `finish_reason="length"` with a body that stops mid-sentence. Here that
    body is a description of somebody's photo or video, and a partial one reads
    exactly like a complete one — an ERROR reported as a PASS (R-27), with no
    parser to fail on it the way the debrief's JSON would.

    `MediaComprehender` already owns a `MediaDescription` to degrade into and
    wraps these calls, so raising is the shape that reaches `unavailable`
    rather than a confident half-read.
    """
    choice = response.choices[0]                              # type: ignore[attr-defined]
    if choice.finish_reason == "length":
        raise ValueError(
            f"{what} was cut off at max_tokens — a truncated description "
            f"reads exactly like a complete one, so it is unavailable, not short"
        )
    return (choice.message.content or "").strip()




class UnsupportedPayloadError(Exception):
    """This modality's bytes cannot be put in a content block we can name.

    Raised BEFORE the request, so an unbuildable payload is `unavailable` with
    its own reason rather than a 400 arriving as an indistinguishable `error`
    after a doomed round trip — the same fail-closed argument the byte ceiling
    makes one level up.
    """


def _content_block(
    top: str, subtype: str, data: bytes, data_uri: str
) -> dict[str, object]:
    """The one content block this modality's bytes belong in.

    The original split changed WHICH MODEL was asked and left every modality in
    an `image_url` block, so enabling audio shipped `data:audio/aac;…` as an
    image. The shape is the other half of routing, and it lives here.

    A WILDCARD subtype is refused here, before any request. `image/*` is what a
    platform that declares no mimetype honestly records (Instagram's wire
    carries none), and `nora.domain.ground_media_type` resolves it from the
    bytes at custody time — but when nothing recognises the container the
    wildcard survives, and `data:image/*;base64,…` declares a container no
    decoder can act on. A model handed one answers ANYWAY, wrong, at a rate
    nothing reports; the audio branch below already fails closed on the same
    input by accident, because there is no `input_audio` format named `*`.
    Same answer for all three modalities, deliberately.
    """
    if subtype == "*":
        raise UnsupportedPayloadError(f"{top}/* names no container a payload can declare")
    if top == "image":
        return {"type": "image_url", "image_url": {"url": data_uri}}
    if top == "audio":
        fmt = _AUDIO_FORMATS.get(subtype)
        if fmt is None:
            raise UnsupportedPayloadError(f"no input_audio format for audio/{subtype}")
        return {
            "type": "input_audio",
            # Raw base64, NOT a data URI — `input_audio` carries the bytes and
            # the container separately, which is why the format table exists.
            "input_audio": {"data": base64.b64encode(data).decode("ascii"), "format": fmt},
        }
    if top == "video":
        return {"type": "video_url", "video_url": {"url": data_uri}}
    raise UnsupportedPayloadError(f"no content block for {top}/{subtype}")


def vision_waves(
    *, windows: int, concurrency: int = VISION_WINDOW_CONCURRENCY
) -> int:
    """How many model calls ONE invocation must await IN SEQUENCE.

    The serial factor, and the only one that adds up in wall clock. A video is
    cut into windows (`preprocess.VIDEO_CHUNK_SECONDS` = 90 s,
    `VIDEO_MAX_CHUNKS` = 10), `concurrency` of them are read at once, so the
    invocation's elapsed time is `ceil(windows / concurrency)` per-call ceilings
    — not `windows` of them, and not one, which is the other half of the old
    formula's error: one `media_fetch` was treated as one model call.

    A single-view read — an image, a voice note, the text synthesis, a clip
    ffmpeg could not cut — is one window and therefore exactly one wave.
    """
    return ceil(max(1, windows) / max(1, concurrency))


def derive_vision_timeout(
    *, loop_timeout_seconds: float, configured: float, waves: int
) -> float:
    """The per-call vision ceiling: small enough that the `waves` one invocation
    runs in sequence stay under the loop's `wait_for`, leaving headroom for the
    text turns.

    Derived from `llm.timeout_seconds` AND the wave count, never configured
    beside them (the `factory.build_retry_policy` argument): the invariant that
    matters — `waves * derived <= loop * VISION_LOOP_SHARE < loop` — is one
    nobody can violate if it is never written down twice. `VISION_LOOP_SHARE /
    waves` is what guarantees it: for any `configured`, however large, the
    result is at most `loop_timeout_seconds * VISION_LOOP_SHARE / waves`, so the
    wall clock one invocation consumes is at most `loop * VISION_LOOP_SHARE`,
    strictly less than the loop.

    **The divisor is the SERIAL factor, and it used to be a concurrent one.**
    Dividing by the tool's budget — the number of `media_fetch` calls a turn may
    make — divided a wall-clock ceiling by calls that OVERLAP in time, so the
    quantity the old invariant proved (`budget * derived < loop`) was one
    nothing ever elapses. The result is now independent of the budget by
    construction: concurrent invocations occupy `max(their durations)`, and a
    turn that runs four of them consumes one invocation's wall clock, not four.
    See `design/media-read-lane/07-timeout-derivation.md`; §6 records the one
    case that reconverts concurrency into a sum (a tool-concurrency ceiling BELOW
    the budget makes the calls QUEUE), which is a config relationship to assert
    across both keys rather than a term to put back here.

    No `max(0.1, …)` floor: for any positive loop the per-call ceiling is
    positive and below the loop, and a floor could exceed the ceiling for a
    sub-second loop and void the guarantee (both reviews' low finding).
    """
    per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, waves)
    return min(configured, per_call) if configured > 0 else per_call


@dataclass(frozen=True)
class VisionResult:
    """One MiMo describe attempt. `available=False` is the tool's `unavailable`.

    `reason` is for the `{status}` distinction and the log line, never for a
    caller to branch on beyond that: `too_large` (over the byte ceiling),
    `timeout` (did not answer inside the derived ceiling), `error` (transport, or
    an answer this reader could not read).
    """

    available: bool
    description: str = ""
    reason: str = ""


class VisionClient:
    """Describe one image's bytes through MiMo, under a fixed budget.

    A constructed collaborator (CLAUDE.md's test-seam policy), built once by the
    composition root over the process-wide client and threaded onto
    `ToolRegistry` exactly like `TransitClient`/`RouteClient`. The description
    CACHE is not here — it lives on the registry and is checked by the tool
    before this client is ever called, so a cache hit skips the blob decrypt too.
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        max_tokens: int,
        reasoning_effort: str,
        max_bytes: int,
        timeout_seconds: float,
        loop_timeout_seconds: float,
        direct_client: AsyncOpenAI | None = None,
    ) -> None:
        self._client = client
        self._direct_client = direct_client
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._max_bytes = max_bytes
        # The derivation's INPUTS, not its result. The divisor is the window
        # count, which `cut_video` only produces per request — long after this
        # object is built (`composition.py`) — so the client holds
        # `configured` and the loop ceiling and derives per call below.
        self._configured = timeout_seconds
        self._loop_timeout = loop_timeout_seconds

    def _call_timeout(self, windows: int | None) -> float:
        """This call's ceiling, derived HERE and nowhere else.

        Single-sourced on purpose: `derive_vision_timeout`'s own argument is
        that the invariant "is one nobody can violate if it is never written
        down twice", and computing it at the call sites is precisely how
        `[llm.budgets]` once produced a second reader of the same number.

        `windows is None` is every single-view read — an image, a voice note,
        the text pass — and `vision_waves` resolves it to one wave.
        """
        return derive_vision_timeout(
            loop_timeout_seconds=self._loop_timeout,
            configured=self._configured,
            waves=vision_waves(windows=1 if windows is None else windows),
        )

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = "",
        provider: str = "omniroute", max_bytes: int | None = None,
        windows: int | None = None,
    ) -> VisionResult:
        """MiMo's sentence about `data`, or an unavailable result. Never raises.

        `prompt` defaults to the transcription prompt `media_fetch` has always
        used — a call site that omits the argument keeps its exact existing
        behaviour. `MediaComprehender` is the other caller, passing one of
        `COMPREHEND_QUESTIONS` instead: same connection, same ceiling, a
        different question, selected by the media LABEL.

        `model` is the same shape one level up (2026-08-17 spec): empty keeps
        the configured model, so every existing call site is unchanged, and a
        per-modality caller passes the model ITS modality is configured with.
        One client, one connection, one ceiling — the modality picks the model
        and the prompt, not a second client. That matters for the timeout proof
        `derive_vision_timeout` makes: three clients would be three independent
        ceilings, and three hung calls could outlast the loop.

        `windows` is how many windows THIS invocation reads in total — the
        serial factor the per-call ceiling divides by. `None` is a single-view
        read (an image, a voice note, a clip ffmpeg could not cut) and derives
        exactly the ceiling those have always had. Only `read_video_windows`
        passes a count, because only `cut_video` produces one, and it can only
        do so per request.

        `provider` selects the transport: `"omniroute"` (default; the process-
        wide gateway client, `_client` — every pre-provider caller keeps its
        exact behaviour) or `"kimi"` (the direct `_direct_client`, when one
        was injected). A `"kimi"` provider with no direct client is a WIRING
        GAP and answers `unavailable` — never a silent fallback to OmniRoute,
        which would send the video bytes to a router that drops `video_url`
        and answer with a model claiming it saw no video (R-27: an ERROR is
        never a PASS).
        """
        # An unset prompt resolves from the blob's OWN MIME type, so the
        # modality picks its question without any caller having to know the
        # table. `media_fetch` omitted this argument before the split and still
        # does; for an image it resolves to the identical string, so that path
        # is unchanged. `MediaComprehender` passes its own and is untouched.
        # ONE parse, shared by the prompt and the payload — `parse_media_type`
        # is the module that owns this (RFC 6838: parameters stripped, case
        # normalised, unregistered top-level types rejected). Two local
        # `partition("/")` copies is how they drift.
        top, subtype = parse_media_type(media_type)
        prompt = prompt or DESCRIBE_PROMPTS.get(top, _PROMPT)
        ceiling = self._max_bytes if max_bytes is None else max_bytes
        if len(data) > ceiling:
            log.warning(
                "vision blob %d bytes over the %d ceiling — not sent",
                len(data), ceiling,
            )
            return VisionResult(available=False, reason="too_large")
        try:
            data_uri = f"data:{top}/{subtype};base64,{base64.b64encode(data).decode('ascii')}"
            block = _content_block(top, subtype, data, data_uri)
        except UnsupportedPayloadError as exc:
            log.warning("vision payload not buildable: %s", exc)
            return VisionResult(available=False, reason="unsupported_payload")
        timeout = self._call_timeout(windows)
        try:
            # The whole retry loop is inside ONE wait_for, so even two attempts
            # cannot push the total past the derived ceiling (< the loop's own).
            text = await asyncio.wait_for(
                self._describe_with_retry(
                    block=block, prompt=prompt, model=model, provider=provider,
                ),
                timeout=timeout,
            )
        except TimeoutError:
            log.warning("vision call did not answer within %.1fs", timeout)
            return VisionResult(available=False, reason="timeout")
        except Exception:
            log.exception("vision call failed")
            return VisionResult(available=False, reason="error")
        if not text:
            # A 200 with an empty message is not a description — treat it as a
            # failure to answer, never as "the image was blank".
            log.warning("vision call returned no text")
            return VisionResult(available=False, reason="error")
        return VisionResult(available=True, description=text)

    async def complete(self, *, prompt: str, text: str, model: str = "") -> str:
        """One TEXT-only call on this same client, connection and ceiling.

        The audio half of issue #116 and the video synthesis half of #117 both
        need a cheap pass over text Nora already holds — a voice note's
        STT transcript, a video's chunk run. Neither is a vision call
        and neither should build a second client for it: the timeout proof
        `derive_vision_timeout` makes is about ONE ceiling, and a separate
        client would be a second one that could outlast the loop.

        It is ONE call, so it is one wave — the synthesis happens after the
        window run has already finished, never alongside it.

        `text` rides in its OWN user message, after the authored `prompt`,
        rather than concatenated into it. The text is a transcript of what
        people said or a model's notes about somebody's video — untrusted, and
        an instruction inside it must read as data rather than as the tail of
        the instruction it was appended to.

        Raises rather than returning a typed failure, unlike `describe`: the
        one caller (`MediaComprehender.summarize`) already owns a
        `MediaDescription` shape to degrade into and wraps this, and adding a
        second never-raises result type for one call site would be a port for
        an audience of one.
        """
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=model or self._model,
                max_tokens=self._max_tokens,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "user", "content": text},
                ],
                extra_body={"reasoning_effort": self._reasoning_effort},
            ),
            timeout=self._call_timeout(None),
        )
        return _checked(response, what="the text-only vision call")

    async def _describe_with_retry(
        self, *, block: dict[str, object], prompt: str, model: str = "",
        provider: str = "omniroute",
    ) -> str:
        last_exc: Exception = RuntimeError("vision retry loop did not run")
        for attempt in range(2):
            try:
                return await self._create(block, prompt, model, provider)
            except ValueError as exc:  # deterministic wiring gap — retrying
                last_exc = exc          # cannot heal it (kimi without a direct
                log.warning("vision attempt %d failed: %s", attempt + 1, exc)
                break
            except Exception as exc:  # noqa: BLE001 — retried once, then re-raised
                last_exc = exc
                log.warning("vision attempt %d failed: %s", attempt + 1, exc)
        raise last_exc

    async def _create(
        self, block: dict[str, object], prompt: str, model: str = "",
        provider: str = "omniroute",
    ) -> str:
        # `provider` picks the TRANSPORT, not the model. A kimi request whose
        # direct client was not injected is a wiring gap — the caller has
        # asked for a provider the composition root did not build — and
        # answering `unavailable` is the honest fail-closed shape (R-27).
        # Falling back to `_client` here would be silent: the request would
        # travel through OmniRoute, which drops `video_url` content blocks
        # (probe 2026-08-30), and the model would answer about the text it
        # DID receive. An ERROR reported as a PASS.
        client = self._client
        if provider == "kimi":
            if self._direct_client is None:
                raise ValueError("provider 'kimi' requested but no direct client injected")
            client = self._direct_client
        response = await client.chat.completions.create(
            model=model or self._model,
            max_tokens=self._max_tokens,
            # `type: ignore[list-item]` — the content block is built per
            # modality by `_content_block`, so its shape is a union the SDK's
            # TypedDicts cannot narrow from a plain dict. The gateway reads
            # the JSON; the stub is not the contract (same argument as
            # `extra_body` below).
            messages=[
                {  # type: ignore[list-item,misc]
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        block,
                    ],
                }
            ],
            # MiMo reads reasoning effort as a top-level body field. Passed via
            # extra_body rather than a typed kwarg so this call does not depend
            # on the installed openai SDK carrying `reasoning_effort` in its
            # signature — the gateway reads the JSON, not the Python stub.
            extra_body={"reasoning_effort": self._reasoning_effort},
        )
        return _checked(response, what="the vision read")
