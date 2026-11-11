from __future__ import annotations

import asyncio
from collections.abc import Mapping

from openai import AsyncOpenAI
from openai.types.shared.reasoning_effort import ReasoningEffort
from opentelemetry.util.types import AttributeValue

from nora.agent.structured import ask_text, build_structured_model
from nora.agent.telemetry import (
    end_raw_model_span,
    end_raw_model_span_with_error,
    start_raw_model_span,
)
from nora.agent.telemetry import timed as telemetry_timed
from nora.domain.speech_markup import normalise as normalise_for_speech
from nora.engagement.stages.debrief import Completion as DebriefCompletion
from nora.ingest.relevance_gate import Completion as GateCompletion

"""Raw chat-completions model clients used by ports above the agent."""


class _RelevanceGateModelClient:
    """Satisfies `nora.ingest.relevance_gate.ModelClient` — ONE RAW
    `json_object` COMPLETION.

    **The gate is the exception `nora/agent/structured.py` warns about.** Its
    verdict decides whether Nora may speak into a room she was not invited
    into, so it does not go through the SDK's `structured_output`: Strands
    converts the schema into a TOOL SPECIFICATION and feeds the result back, so
    one logical call becomes three messages, and against OmniRoute's Chat
    Completions transport the SDK drops the assistant turn's reasoning before
    the verdict is formed. A member who can forge Nora's side of a transcript
    (a `[assistant]:` row inside a message) would then steer the admission
    decision, which is an unauthenticated turn. The gate is
    `tests/architecture/test_the_gate_does_not_form_its_verdict_through_the_sdk.py`.

    The instruction goes in the SYSTEM role and the room in the USER role.

    **`tool_choice` is not declared here and must not be.** A raw `json_object`
    completion sends no tools at all, so there is nothing for a tool_choice to
    select and the field never reaches the wire. The shape is
    `tests/offline_eval_support.py::_MeasuringGateClient`'s, argument for
    argument, with one difference: the eval's client sends `NO_CACHE_HEADERS`
    and this one sends no such header. Eval readings are therefore
    cache-bypassed; a comparison between eval arms holds, but an absolute eval
    reading does not transfer to production unqualified.

    What a raw client owes
    (`tests/architecture/test_raw_model_calls_carry_their_own_machinery.py` is
    the gate): an explicit `asyncio.wait_for`, because the bare `AsyncOpenAI`
    default is 600s x retries and this call runs while the conversation's slot
    is HELD, so an unbounded one is a room that stops answering; a
    `finish_reason` read, because a raw completion does not raise on a cut-off
    the way `MaxTokensReachedException` does, it returns `"length"` and a
    silently truncated body that can still parse as JSON into something shorter
    and wrong; and a usage annotation, because Strands emits `gen_ai.usage.*`
    itself and a raw call emits nothing, so without it the call exports to
    Langfuse costing zero.

    It PINS `reasoning_effort` rather than leaving it to the route: a setting
    left to the route is a setting nobody chose. `max_tokens` and
    `temperature` are the gate's own — a gate with room to write paragraphs
    has room to start drafting the reply it must never draft — and its model
    is its OWN combo (`nora-gate`), never the tag lane's.
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
        reasoning_effort: ReasoningEffort,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._reasoning_effort = reasoning_effort

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, system: str, prompt: str) -> GateCompletion:
        # A span of its own — `start_raw_model_span`/`end_raw_model_span`
        # (`nora.agent.telemetry`, thin wrappers around Strands'
        # `Tracer.start_model_invoke_span`/`end_model_invoke_span`) — nested under whatever…
        span = start_raw_model_span(
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            model_id=self._model,
            system_prompt=system,
        )
        with telemetry_timed() as elapsed:
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            # The instruction in the SYSTEM role, the room in the USER role. Conc…
                            # user string, a member typing `[assistant]:` would sit in exactly th…
                            # operator's rules occupy, and nothing downstream could tell them apart.
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=self._max_tokens,
                        temperature=self._temperature,
                        reasoning_effort=self._reasoning_effort,
                    ),
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:
                end_raw_model_span_with_error(
                    span=span,
                    error_message=str(exc) or type(exc).__name__,
                    exception=exc,
                )
                raise
            latency_seconds = elapsed()
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        # `or ""` and not a `"stop"` default: a provider that omits the
        # field has told us nothing, and inventing `"stop"` would assert
        # the call completed.
        finish_reason = choice.finish_reason or ""
        usage = getattr(response, "usage", None)
        end_raw_model_span(
            span=span,
            content=content,
            finish_reason=finish_reason,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            latency_seconds=latency_seconds,
        )
        return GateCompletion(content=content, finish_reason=finish_reason)


class _DebriefModelClient:
    """Satisfies `nora.engagement.stages.debrief.ModelClient` — a raw one-shot
    text completion over the process-wide OmniRoute client, with three things
    on it that every other raw `chat.completions.create` in this repo needs.

    **`finish_reason` comes back with the content.** A raw
    `chat.completions.create` does not raise on a cut-off the way a Strands
    agent's `MaxTokensReachedException` does — it returns
    `finish_reason="length"` and a **silently truncated body**, which can still
    parse as JSON into something shorter and wrong. That is the R-27
    substitution (an ERROR reported as a PASS) one layer down from where this
    repo usually enforces it, and the only way to keep it out is to hand the
    caller the field it must check.

    **`response_format={"type": "json_object"}`**, because prompt-only JSON
    enforcement is insufficient: nothing on the wire would ask the provider to
    honour it.

    **An explicit `asyncio.wait_for`**, because the bare `AsyncOpenAI` default
    is 600s x retries — and this call HOLDS THE CONVERSATION'S SLOT, so an
    unbounded one is not a slow background job, it is a room that stops
    answering.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        reasoning_effort: ReasoningEffort,
        max_tokens: int,
        temperature: float,
        agent_name: str = "debrief",
    ) -> None:
        self._model_id = model
        self._timeout_seconds = timeout_seconds
        # `agent_name` becomes the Strands agent span (`invoke_agent <name>`) inside `ask_text`
        # — without it every debrief/standing-fold Strands agent falls back to the SDK default
        # "Strands Agents", so the call under `engagement.debrief.fold` would be…
        self._agent_name = agent_name
        self._provider = build_structured_model(
            base_url=base_url,
            api_key=api_key,
            model_id=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=str(reasoning_effort),
        )

    @property
    def model(self) -> str:
        return self._model_id

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        trace_attributes: Mapping[str, AttributeValue] | None = None,
    ) -> DebriefCompletion:
        got = await ask_text(
            model=self._provider,
            prompt=prompt,
            timeout_seconds=self._timeout_seconds,
            system_prompt=system,
            agent_name=self._agent_name,
            trace_attributes=trace_attributes,
        )
        # No usage annotation here: this is a real Strands `Agent`
        # invocation (`ask_text`), and Strands emits `gen_ai.usage.*` on its
        # own model-invoke span itself.
        return DebriefCompletion(
            content=got.content,
            finish_reason=got.finish_reason,
        )


class _NormaliseOnly:
    """`[speech.tts.format] mode = "normalise"`: the deterministic half only,
    no model call (`nora.domain.speech_markup.normalise`)."""

    async def format(self, *, text: str) -> str:
        return normalise_for_speech(text)
