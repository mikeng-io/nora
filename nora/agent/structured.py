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
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from strands import Agent
from strands.models.openai import OpenAIModel

from .tool_choice import FIRST_CALL

T = TypeVar("T", bound=BaseModel)


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
    """
    return OpenAIModel(
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
) -> StructuredResult:
    """One call, `tools=[]`, no streaming, bounded.

    `system_prompt` is the caller's instruction, and it goes in the SYSTEM
    role rather than being concatenated onto `prompt`. That is the whole
    reason this exists: a caller that glues its rules and its untrusted input
    into one user string has thrown away the only structural difference
    between the two, and nothing downstream can put it back.

    A fresh `Agent` per call: `OpenAIModel` carries mutable `config["params"]`
    and an agent carries conversation state, so a shared one would let one
    call's exchange decide the next one's (`factory.ModelSpec`'s argument, and
    `create_agent` builds per-tag for exactly this).
    """
    agent = Agent(model=model, tools=[], callback_handler=None,
                  system_prompt=system_prompt)
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
