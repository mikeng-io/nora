"""The ONLY module that constructs Strands objects (spec C.7).

One AsyncOpenAI client for the whole process (audit T2) — never one per tag. The
caller owns the client lifecycle; close_client() runs on shutdown.
"""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI
from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.models.openai import OpenAIModel

from ..config import LimitsConfig, LLMConfig, LLMTierConfig
from .guards import ReplyGuard

log = logging.getLogger("nora.factory")

_CLIENT: AsyncOpenAI | None = None


def create_client(cfg: LLMConfig) -> AsyncOpenAI:
    global _CLIENT
    if _CLIENT is None:
        key = os.environ.get(cfg.api_key_env)
        if not key:
            raise RuntimeError(f"{cfg.api_key_env} is not set — refusing to start")
        _CLIENT = AsyncOpenAI(api_key=key, base_url=cfg.base_url)
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


def create_model_provider(cfg: LLMTierConfig, client: AsyncOpenAI) -> OpenAIModel:
    return OpenAIModel(
        model_id=cfg.model,
        client=client,
        params={"max_tokens": cfg.max_tokens, "temperature": cfg.temperature},
    )


def build_limits(tier: LLMTierConfig, limits: LimitsConfig) -> dict:
    """R-8: output_tokens derives from the active tier; turns and total come from config."""
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
