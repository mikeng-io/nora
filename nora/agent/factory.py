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
from typing import Protocol

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


class _TierLike(Protocol):
    max_tokens: int


class _LimitsLike(Protocol):
    turns: int
    total_tokens: int


def build_limits(tier: _TierLike, limits: _LimitsLike) -> dict:
    """R-8: output_tokens derives from the active tier; turns and total come
    from the limits object.

    Deliberately NOT scalar-only like `create_client`/`create_model_provider`
    above: `nora.handler.Handler.handle_tag` (`nora/handler.py:183-185`)
    calls this positionally and *unconditionally* — on every tag, not only
    through the untested live-network branch of `_invoke` that
    `create_client`/`create_model_provider`/`create_agent`/`invoke` live
    behind (see `tests/integration/harness.py`'s `build_handler` docstring:
    every harness-built `Handler` gets a non-`None` `invoke_fn`, so that
    branch is unreachable from the suite by construction — `build_limits`
    has no such gate). A scalar signature here would require handler.py to
    change its call shape too, which Tasks 5-6 are explicitly scoped not to
    touch (`nora/handler.py` stays wired to real traffic until Task 8's
    composition-root cutover). `_TierLike`/`_LimitsLike` are structural
    Protocols, not `nora.config` types — the actual thing this task closes
    (`pyproject.toml`'s now-deleted `ignore_imports` exception) is the
    IMPORT, which this module no longer has; `LLMTierConfig`/`LimitsConfig`
    still satisfy this shape structurally, so `handler.py`'s call is
    unaffected.
    """
    return {
        "turns": limits.turns,
        "output_tokens": tier.max_tokens,
        "total_tokens": limits.total_tokens,
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
