"""The ONLY module that constructs Strands objects (spec C.7).

One `AsyncOpenAI` client for the whole process (audit T2) — never one per
tag — but that guarantee now lives in the composition root's *call
discipline*, not in a module-level singleton (DEC-13): `create_client` is a
plain constructor, called exactly once by
`nora.app.composition.build_dependencies`, whose result is threaded through
`Dependencies.client` to every consumer. `nora.__main__.App.shutdown` closes
that same object directly (`await self.deps.client.close()`) — there is
nothing left in this module to reset between tests, and nothing left for a
test to leak into another one via import-time state.

Takes explicit scalars, never `nora.config` objects — this used to be the one
documented exception to "only app and `__main__` read config"
(`pyproject.toml`'s `ignore_imports`, deleted by the pipelines phase). The
composition root (Task 8) resolves `LLMConfig`/`LLMTierConfig`/`LimitsConfig`
down to the scalars below and hands them over already resolved, including the
API key itself — this module no longer reads the environment.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI
from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.models.openai import OpenAIModel

from .guards import ReplyGuard

log = logging.getLogger("nora.factory")


def create_client(*, base_url: str, api_key: str) -> AsyncOpenAI:
    if not api_key:
        raise RuntimeError("api_key is empty — refusing to start")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def create_model_provider(
    *, model: str, max_tokens: int, temperature: float, client: AsyncOpenAI,
    reasoning_effort: str = "none",
) -> OpenAIModel:
    """`tool_choice="required"` is load-bearing, not a tuning knob.

    `send_reply` is a tool, and it is the ONLY way Nora can speak (A9, R-2).
    `deepseek-v4-flash` — the configured tier — does not emit `tool_calls` on
    its own: given an explicit tool definition AND a system prompt demanding
    one, it answers in plain text with `finish_reason="stop"`, verified
    directly against the gateway. `tool_choice="auto"` changes nothing;
    `"required"` produces the call every time.

    Without it every tag ran the model, got prose, and dropped it as
    `no_send_call` — Nora reacted 👀 then ⚠️ and never spoke. The failure was
    honest and counted, which is how it was found on the first real tag, but
    it made her structurally mute.

    This constrains the model to call SOME tool, not `send_reply` specifically,
    so `read_buffer`/`read_peer_card` remain reachable and R-7's default-deny
    is untouched.
    """
    return OpenAIModel(
        model_id=model,
        client=client,
        params={
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tool_choice": "required",
            # Comes from the tier (config.toml), not from here — a runtime
            # value belongs in config, not in code (DEC-8). See
            # `LLMTierConfig.reasoning_effort` for the measurements.
            "reasoning_effort": reasoning_effort,
        },
    )


def build_limits(*, max_tokens: int, turns: int, total_tokens: int) -> dict:
    """R-8: `output_tokens` derives from the active tier; `turns` and
    `total_tokens` come from the limits object.

    Scalarised in Task 8 (Phase 3's composition-root cutover): this used to
    take `(tier, limits)` object pairs because the now-deleted
    `Handler.handle_tag` called it positionally on every tag, and Tasks 5-6
    were scoped not to touch `handler.py`'s call shape while it still carried
    real traffic. `Handler` is gone and this is now called exactly once, at
    process startup, by `nora.app.composition.build_dependencies` — so there
    is no longer a caller forcing an object-shaped signature, and scalars
    keep this module's inputs framework-free the same way
    `create_client`/`create_model_provider` already are.
    """
    return {
        "turns": turns,
        "output_tokens": max_tokens,
        "total_tokens": total_tokens,
    }


def create_agent(
    *,
    model: OpenAIModel,
    system_prompt: str,
    tools: list,
    pre_fill: list[dict],
    guard: ReplyGuard,
    trace_attributes: dict,
) -> Agent:
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        # pre_fill/messages are plain dicts by design end-to-end (build_prefill in
        # nora.agent.prefill through nora.engagement's stages/pipeline, none of which
        # import strands — see this module's own docstring and
        # tests/test_boundaries.py::test_strands_import_boundary). Strands' `Agent`
        # wants `list[Message]` (a TypedDict); retyping this one call site to match
        # would force list[Message] up through that whole framework-free chain, which
        # is out of scope for this fix. The dicts already have the right shape at
        # runtime; only the static type is being asserted here.
        messages=list(pre_fill),  # type: ignore[arg-type]
        hooks=[guard],
        conversation_manager=NullConversationManager(),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


async def invoke(agent: Agent, tag_message: str, *, invocation_state: dict, limits: dict):
    return await agent.invoke_async(
        tag_message,
        invocation_state=invocation_state,
        # limits is a plain dict end-to-end for the same reason pre_fill is above:
        # nora.app.composition/nora.engagement build and thread it as `dict` without
        # importing strands. Strands' `Limits` is a TypedDict with the same three
        # keys build_limits() always fills (turns/output_tokens/total_tokens); the
        # value is shape-correct at runtime, only the static type differs.
        limits=limits,  # type: ignore[arg-type]
    )
