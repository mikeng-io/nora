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
  single `asyncio.wait_for(timeout=llm.timeout_seconds)` (30 s). A MiMo
  *reasoning* call with a generous `max_tokens` is seconds, not `about`'s ~0.2 s,
  and two of them plus the text turns would overrun 30 s → the loop's silent
  `invocation_timeout` (R-5). `derive_vision_timeout` below caps this call's
  budget at a fixed fraction of the loop timeout, so a slow or hung MiMo becomes
  THIS tool's `unavailable` inside the turn instead — the same discipline
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

from openai import AsyncOpenAI

from nora.domain.ports import MediaDescription

log = logging.getLogger("nora.vision")

# How much of the loop's own timeout one vision call may spend. A fraction < 1
# is what MAKES the derived budget provably shorter than the loop's
# `asyncio.wait_for`, so a hung MiMo can never become the loop's silent
# `invocation_timeout`. The share is divided by the tool's BUDGET in
# `derive_vision_timeout`, so `budget * derived < loop`: two hung image reads in
# one turn (the case budget 2 exists for) still leave headroom for the text
# turns. A per-CALL fraction alone reopened that hole — both reviews 2026-08-07.
VISION_LOOP_SHARE = 0.6

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
    # Transcription first and description second: a voice note in this group is
    # somebody talking, and the words are the point. «If nothing is audible»
    # gives the model a way to report an empty result that is not an invention.
    "audio": (
        "Transcribe what is said in this audio. If there is no speech, describe "
        "what can be heard instead. If nothing is audible, say so."
    ),
    # No frame-by-frame narration: the useful answer is what HAPPENS, plus any
    # text on screen, which is the video analogue of `_PROMPT`'s second clause.
    "video": (
        "Describe what happens in this video. If there is speech or text on "
        "screen, transcribe it."
    ),
}


def derive_vision_timeout(
    *, loop_timeout_seconds: float, configured: float, budget: int
) -> float:
    """The per-call vision budget: small enough that `budget` calls together stay
    under the loop's `wait_for`, leaving headroom for the text turns.

    Derived from `llm.timeout_seconds` AND the tool's `budget`, never configured
    beside them (the `factory.build_retry_policy` argument): the invariant that
    matters — `budget * derived < loop_timeout_seconds` — is one nobody can
    violate if it is never written down twice. `VISION_LOOP_SHARE / budget` is
    what guarantees it: for any `configured`, however large, the result is at
    most `loop_timeout_seconds * VISION_LOOP_SHARE / budget`, so the AGGREGATE of
    `budget` calls is at most `loop * VISION_LOOP_SHARE`, strictly less than the
    loop. No `max(0.1, …)` floor: for any positive loop the per-call ceiling is
    positive and below the loop, and a floor could exceed the ceiling for a
    sub-second loop and void the guarantee (both reviews' low finding).
    """
    per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, budget)
    return min(configured, per_call) if configured > 0 else per_call


@dataclass(frozen=True)
class VisionResult:
    """One MiMo describe attempt. `available=False` is the tool's `unavailable`.

    `reason` is for the `{status}` distinction and the log line, never for a
    caller to branch on beyond that: `too_large` (over the byte ceiling),
    `timeout` (did not answer inside the derived budget), `error` (transport, or
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
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._max_bytes = max_bytes
        self._timeout = timeout_seconds

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = ""
    ) -> VisionResult:
        """MiMo's sentence about `data`, or an unavailable result. Never raises.

        `prompt` defaults to the transcription prompt `media_fetch` has always
        used — that call site omits the argument and keeps its exact existing
        behaviour. `StickerInterpreter` is the other caller, passing
        `_STICKER_PROMPT` instead: same connection, same budget, a different
        question.

        `model` is the same shape one level up (2026-08-17 spec): empty keeps
        the configured model, so every existing call site is unchanged, and a
        per-modality caller passes the model ITS modality is configured with.
        One client, one connection, one budget — the modality picks the model
        and the prompt, not a second client. That matters for the aggregate
        timeout proof `derive_vision_timeout` makes: three clients would be
        three budgets, and three hung calls could outlast the loop.
        """
        # An unset prompt resolves from the blob's OWN MIME type, so the
        # modality picks its question without any caller having to know the
        # table. `media_fetch` omitted this argument before the split and still
        # does; for an image it resolves to the identical string, so that path
        # is unchanged. `StickerInterpreter` passes its own and is untouched.
        prompt = prompt or DESCRIBE_PROMPTS.get(media_type.partition("/")[0], _PROMPT)
        if len(data) > self._max_bytes:
            log.warning(
                "vision blob %d bytes over the %d ceiling — not sent",
                len(data), self._max_bytes,
            )
            return VisionResult(available=False, reason="too_large")
        try:
            # The whole retry loop is inside ONE wait_for, so even two attempts
            # cannot push the total past the derived budget (< the loop's own).
            text = await asyncio.wait_for(
                self._describe_with_retry(
                    data=data, media_type=media_type, prompt=prompt, model=model),
                timeout=self._timeout,
            )
        except TimeoutError:
            log.warning("vision call did not answer within %.1fs", self._timeout)
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

    async def _describe_with_retry(
        self, *, data: bytes, media_type: str, prompt: str, model: str = ""
    ) -> str:
        data_uri = f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
        last_exc: Exception = RuntimeError("vision retry loop did not run")
        for attempt in range(2):
            try:
                return await self._create(data_uri, prompt, model)
            except Exception as exc:  # noqa: BLE001 — retried once, then re-raised
                last_exc = exc
                log.warning("vision attempt %d failed: %s", attempt + 1, exc)
        raise last_exc

    async def _create(self, data_uri: str, prompt: str, model: str = "") -> str:
        response = await self._client.chat.completions.create(
            model=model or self._model,
            max_tokens=self._max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            # MiMo reads reasoning effort as a top-level body field. Passed via
            # extra_body rather than a typed kwarg so this call does not depend
            # on the installed openai SDK carrying `reasoning_effort` in its
            # signature — the gateway reads the JSON, not the Python stub.
            extra_body={"reasoning_effort": self._reasoning_effort},
        )
        choice = response.choices[0]
        return (choice.message.content or "").strip()


# Authored, fixed, and small — never model-supplied, like `_PROMPT` above. The
# final clause is load-bearing rather than polite: ingest holds the sticker and
# NOT the conversation, so an intent judgment made here is made blind, cannot be
# checked against anything, and would replace the evidence that lets anything
# downstream disagree with it (spec §2.1). The pixels are the claim; the
# conversational force is Nora's, made where the transcript is.
_STICKER_PROMPT = (
    "This is a chat sticker. In one sentence, describe what it depicts, "
    "including any text written in it. Describe only what is shown — do not "
    "interpret the sender's mood or intent."
)

# What a vision model can actually decode. `application/json` (Discord lottie)
# is the case this exists for: handing JSON to a vision model is not a degraded
# read, it is a wrong one, and the model would answer something anyway.
_INTERPRETABLE = ("image/",)


class StickerInterpreter:
    """Satisfies `nora.domain.ports.MediaInterpreter` over `VisionClient`.

    A thin adapter, not a second client: the same process-wide OmniRoute
    connection, a different prompt and its own timeout. NOT a smaller token
    budget — that was the original reasoning ("a sticker needs a sentence, not
    `media_fetch`'s 1024-token transcription allowance"), and the 2026-08-07
    probe refuted it: MiMo's reasoning tokens share the same `max_tokens`
    budget as its answer, and 128 failed 0/4 attempts to get a sentence back.
    The shipped `sticker_prompt_max_tokens` is 1024, identical to
    `vision_max_tokens`.
    """

    def __init__(self, client: VisionClient, *, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def describe(self, *, data: bytes, media_type: str) -> MediaDescription:
        if not media_type.startswith(_INTERPRETABLE):
            return MediaDescription(available=False, reason="unsupported_format")
        result = await self._client.describe(
            data=data, media_type=media_type, prompt=_STICKER_PROMPT
        )
        if not result.available:
            return MediaDescription(available=False, reason=result.reason or "vision")
        return MediaDescription(available=True, text=result.description)
