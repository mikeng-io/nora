"""The ONLY module that constructs Strands objects (spec C.7).

One AsyncOpenAI client for the whole process (audit T2) — never one per tag. The
caller owns the client lifecycle; close_client() runs on shutdown.

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

_CLIENT: AsyncOpenAI | None = None


def create_client(*, base_url: str, api_key: str) -> AsyncOpenAI:
    global _CLIENT
    if _CLIENT is None:
        if not api_key:
            raise RuntimeError("api_key is empty — refusing to start")
        _CLIENT = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return _CLIENT


async def close_client() -> None:
    global _CLIENT
    if _CLIENT is not None:
        await _CLIENT.close()
        _CLIENT = None


def close_client_sync() -> None:
    """Test helper: drop the singleton without awaiting."""
    global _CLIENT
    _CLIENT = None


def create_model_provider(
    *, model: str, max_tokens: int, temperature: float, client: AsyncOpenAI
) -> OpenAIModel:
    return OpenAIModel(
        model_id=model,
        client=client,
        params={"max_tokens": max_tokens, "temperature": temperature},
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
        messages=list(pre_fill),
        hooks=[guard],
        conversation_manager=NullConversationManager(),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


async def invoke(agent: Agent, tag_message: str, *, invocation_state: dict, limits: dict):
    return await agent.invoke_async(
        tag_message, invocation_state=invocation_state, limits=limits
    )
