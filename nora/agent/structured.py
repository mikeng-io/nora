"""One structured model call through the Agent SDK, with no loop.

`nora/agent/` is where `strands` may be imported from at all
(`tests/test_boundaries.py::test_strands_import_boundary`), so a caller
elsewhere that wants the SDK's structured output asks here rather than
importing the SDK itself — the same reason `factory.create_model_provider`
exists for the tag path.

**What this measured, and what it costs.** Strands implements structured
output by converting the schema into a TOOL SPECIFICATION and feeding the
tool result back, so one logical call becomes a three-message exchange:

    [0] user      text
    [1] assistant reasoningContent + toolUse
    [2] user      toolResult

Against OmniRoute's Chat Completions transport the SDK then logs
«reasoningContent is not supported in multi-turn conversations» and DROPS
message [1]'s reasoning before the verdict is formed. On the relevance gate's
40-case suite that turned a held safety case into a breach — a forged
`[assistant]:` row inside a member's message was believed, five times out of
five — while costing 2.6x the tokens (2026-09-09; `evals/relevance_gate.py
--via-sdk` is the instrument).

So this is NOT a drop-in replacement for a raw completion on a path whose
verdict is security-relevant. It is here to be measured against, and to be
the one home for the SDK call if that transport limitation is lifted.

**The relevance gate is that exception, and since 2026-09-11 it is HONOURED
rather than only warned about.** This paragraph was written in the same change
that wired the gate onto this module (`4448c737`), so the caveat and the
violation shipped together and neither read as a contradiction in isolation —
the gate then ran in production for two days on the transport its own eval had
already measured as failing. Re-measured on 2026-09-11 against the deployed
`nora-gate` alias, same tree, same model, one instruction on both arms
(`participation.md` v5 `sha=9969bc4e643c`), 40 labelled cases, n=5:

    arm                             safety     prompt tokens (median)
    raw `json_object`               6/6 held          2,055
    Strands `structured_output`     1/6 BREACHED      5,021  (2.4x)

It reproduced. `nora/app/composition.py::_RelevanceGateModelClient` is a raw
`json_object` completion again (issue #186), and a sentence is no longer what
holds it there: `tests/architecture/
test_the_gate_does_not_form_its_verdict_through_the_sdk.py` discovers every
`RelevanceGate` client and fails when one reaches `ask_structured`. A docstring
did not stop the wiring the first time.

The debrief and the standing fold STAY here. They use `ask_text`, which sends
no tool specification at all, and neither produces a verdict that decides
whether Nora may speak into a room she was not invited into — which is the
exact line this module draws.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from opentelemetry.util.types import AttributeValue
from pydantic import BaseModel
from strands import Agent
from strands.models.openai import OpenAIModel
from strands.types.exceptions import MaxTokensReachedException

from .factory import MAX_TOKENS_NOTICE_INFIX, MAX_TOKENS_NOTICE_PREFIX
from .punctuation import DashFreeOpenAIModel
from .tool_choice import FIRST_CALL

T = TypeVar("T", bound=BaseModel)

#: The Strands SDK defaults an unnamed `Agent(name=None)` to "Strands Agents",
#: which made every `ask_text`/`ask_structured` span identical and unsearchable
#: (measured in Langfuse: 56 detached `invoke_agent Strands Agents` roots over
#: 7 days — debrief, standing fold and eval arms all collapsed into one name).
#: A caller that names its call gets a span it can filter on. The default
#: stays honest ("structured") rather than pretending to be a caller's lane.
DEFAULT_STRUCTURED_AGENT_NAME = "structured"


@dataclass(frozen=True)
class StructuredResult:
    """One structured call's answer, in shapes a caller outside this package
    can read without importing `strands` — which is the whole point of the
    boundary this module sits on."""

    #: The validated object serialised as JSON, or `""` when the SDK produced
    #: none. Serialised rather than returned as the model so a caller's own
    #: parser and fail-closed rules stay the ones in force.
    content: str
    #: Translated into the raw-completion vocabulary a caller already checks:
    #: the SDK's budget stop becomes `"length"`, because a caller's truncation
    #: rule is written against that word and an unrecognised stop reason would
    #: read as a clean answer.
    finish_reason: str
    #: `(prompt, completion, total)`, ACCUMULATED across the exchange's turns.
    #: None when the provider reported none — never zeros, which would assert
    #: the call was free.
    usage: tuple[int, int, int] | None


def build_structured_model(
    *, base_url: str, api_key: str, model_id: str, max_tokens: int,
    temperature: float, reasoning_effort: str = "none",
    tool_choice: str = FIRST_CALL, default_headers: dict[str, str] | None = None,
) -> OpenAIModel:
    """The provider for a structured call. Its own client args rather than the
    shared `AsyncOpenAI`, because `OpenAIModel` owns its transport and the
    alternative is reaching into a client this package did not build.

    **`tool_choice` is DECLARED, not left to the SDK.** Strands sends
    `tool_choice: None` when nothing sets it, and a provider that then applies
    its thinking-mode default refuses the call outright:

        opencode-go/deepseek-v4-flash-none: [400] Upstream request failed:
        Thinking mode does not support this tool_choice

    Measured against the `nora-gate` combo on 2026-09-09; it fans out, and the
    same case failed on one member and succeeded on another. Declaring the
    value with `reasoning_effort="none"` — which is what a structured call
    wants anyway, one classification with a fixed output — keeps every member
    of a combo able to serve it. `factory.create_model_provider` carries the
    same argument for the tag path: a setting left to the route is a setting
    nobody chose.
    Dash-free for the same reason as the tag path: what these calls write
    (notes, the standing position, a reaction pick) is read back into her
    prompt, so a dash here reappears there (`nora.agent.punctuation`).
    """
    return DashFreeOpenAIModel(
        client_args={"api_key": api_key, "base_url": base_url,
                     **({"default_headers": default_headers} if default_headers else {})},
        model_id=model_id,
        params={
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort,
            "tool_choice": tool_choice,
        },
    )


async def ask_structured(
    *, model: OpenAIModel, prompt: str, schema: type[T], timeout_seconds: float,
    system_prompt: str | None = None,
    agent_name: str = DEFAULT_STRUCTURED_AGENT_NAME,
) -> StructuredResult:
    """One call, `tools=[]`, no streaming, bounded.

    `system_prompt` is the caller's instruction, and it goes in the SYSTEM
    role rather than being concatenated onto `prompt`. That is the whole
    reason this exists: a caller that glues its rules and its untrusted input
    into one user string has thrown away the only structural difference
    between the two, and nothing downstream can put it back.

    `agent_name` names the Strands agent span (`invoke_agent <name>`) so a
    Langfuse trace is filterable by the CALLER's lane instead of collapsing
    into the SDK default "Strands Agents" — set it to the lane that owns the
    call (e.g. "gate-measuring" from `eval_support`), never leave the default
    in production code paths that a filter needs to tell apart.

    A fresh `Agent` per call: `OpenAIModel` carries mutable `config["params"]`
    and an agent carries conversation state, so a shared one would let one
    call's exchange decide the next one's (`factory.ModelSpec`'s argument, and
    `create_agent` builds per-tag for exactly this).

    **Has the identical `MaxTokensReachedException` gap `ask_text` had before
    this fix (noted, not fixed, by review 2026-09-19)**: no catch, so
    `finish_reason="length"` at `:183` below is equally unreachable on a real
    cutoff. Low priority — `grep`-checked, this function's only caller today
    is `tests/offline_eval_support.py`, no production path — but the
    asymmetry with `ask_text` is now accidental, not deliberate, should this
    function gain one.
    """
    agent = Agent(model=model, tools=[], callback_handler=None,
                  system_prompt=system_prompt, name=agent_name)
    result = await asyncio.wait_for(
        agent.invoke_async(prompt, structured_output_model=schema),
        timeout=timeout_seconds,
    )
    got: Any = result.structured_output
    accumulated = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
    usage = (
        (
            int(accumulated.get("inputTokens", 0)),
            int(accumulated.get("outputTokens", 0)),
            int(accumulated.get("totalTokens", 0)),
        )
        if accumulated else None
    )
    stop = str(getattr(result, "stop_reason", "") or "")
    return StructuredResult(
        content=got.model_dump_json() if got is not None else "",
        finish_reason="length" if stop == "max_tokens" else stop,
        usage=usage,
    )


async def ask_text(
    *, model: OpenAIModel, prompt: str, timeout_seconds: float,
    system_prompt: str | None = None,
    agent_name: str = DEFAULT_STRUCTURED_AGENT_NAME,
    trace_attributes: Mapping[str, AttributeValue] | None = None,
) -> StructuredResult:
    """One call returning TEXT, through the SDK, with no loop and no tools.

    The debrief and the standing fold want prose-or-JSON in the body rather
    than a validated object, so they get this instead of `ask_structured` —
    same agent construction, same system/user split, no tool specification in
    the request at all. `content` is the model's text; `finish_reason` is
    translated into the raw-completion vocabulary its callers already check.

    `agent_name` means what it means in `ask_structured`: the span the call
    traces under, so the debrief's fold/verdict calls are distinguishable
    from the event fold's in Langfuse instead of all reading
    `invoke_agent Strands Agents`.

    `trace_attributes` rides `Agent`'s own `trace_attributes` kwarg, which the
    SDK's event loop copies onto both the agent span and the `chat`
    model-invoke span underneath it (`strands/event_loop/event_loop.py`,
    `custom_trace_attributes=agent.trace_attributes` on both). This is what
    lets `tag_id`/`conversation_id` land on the actual model-call span now
    that the caller no longer wraps this call in its own hand-rolled span to
    stamp them onto — see `nora.engagement.stages.debrief`.

    **`MaxTokensReachedException` is caught here, matching `nora.agent.
    factory.invoke`'s own catch for the tag turn's Agent invocation.**
    Strands' event loop raises it unconditionally on `stop_reason ==
    "max_tokens"` (`event_loop.py:304-311`) rather than returning normally —
    so the `stop == "max_tokens"` branch below, on its own, can never fire for
    a genuine cutoff. Without this catch a debrief/standing-fold call that
    hits the token ceiling raised past this function into its caller's outer
    exception handling and counted as a hard error rather than the
    `finish_reason="length"` outcome `nora.engagement.stages.debrief._check`
    already exists to refuse cleanly. The partial text is recovered from
    `agent.messages` the same way `factory.invoke` does (Strands appends it
    before raising), with the same exclusion for the SDK's canned
    tool-recovery notice — moot here since `ask_text` never grants tools, kept
    for parity with the one other place this extraction is written.

    **`usage=None` on this path is not a loss, only unreported here**
    (review, 2026-09-19): `agent.event_loop_metrics.accumulated_usage` is
    populated before Strands raises, so the token counts ARE recoverable —
    this just does not read them, matching the normal-completion path's own
    "None when the provider reported none" convention rather than asserting
    a call that exhausted its own ceiling was free. The caller
    (`nora.app.composition`'s `_DebriefModelClient`) discards `usage`
    either way, and Strands' own span still records `gen_ai.usage.*`
    independently of this return value.
    """
    agent = Agent(model=model, tools=[], callback_handler=None,
                  system_prompt=system_prompt, name=agent_name,
                  trace_attributes=trace_attributes)
    try:
        result = await asyncio.wait_for(
            agent.invoke_async(prompt), timeout=timeout_seconds,
        )
    except MaxTokensReachedException:
        partial = ""
        if agent.messages:
            last = agent.messages[-1]
            content = last.get("content") if isinstance(last, dict) else None
            if isinstance(content, list):
                parts = [
                    block["text"] for block in content
                    if isinstance(block, dict) and isinstance(block.get("text"), str)
                    and not block["text"].startswith(MAX_TOKENS_NOTICE_PREFIX)
                    and MAX_TOKENS_NOTICE_INFIX not in block["text"]
                ]
                partial = "\n".join(p for p in parts if p.strip()).strip()
        return StructuredResult(content=partial, finish_reason="length", usage=None)
    accumulated = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
    usage = (
        (
            int(accumulated.get("inputTokens", 0)),
            int(accumulated.get("outputTokens", 0)),
            int(accumulated.get("totalTokens", 0)),
        )
        if accumulated else None
    )
    stop = str(getattr(result, "stop_reason", "") or "")
    return StructuredResult(
        content=str(result).strip(),
        finish_reason="length" if stop == "max_tokens" else stop,
        usage=usage,
    )
