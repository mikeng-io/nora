from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from nora.agent import factory, tool_choice
from nora.agent.dreamer import (
    DreamerRuntime,
)
from nora.app.composition_dream import _dream_tool_source
from nora.app.composition_helpers import _reasoning_effort
from nora.app.composition_review import build_review_runner

"""Dreamer runtime and review wiring."""


log = logging.getLogger("nora.app.composition")




@dataclass(frozen=True)
class EnabledWiring:
    dreamer_runtime: Any
    review: Any = None


def enabled(
    allowlist: Any, audit: Any, cfg: Any, dreamer_instructions: Any,
    metrics: Any, model_client_for: Any, persons: Any, reasoning_strip_store: Any,
    registry: Any, rosters: Any, route_model: Any, tier: Any, tool_registry: Any,
    access: Any = None,
) -> EnabledWiring:
    rt = _dreamer_runtime(
        allowlist=allowlist, audit=audit, cfg=cfg,
        metrics=metrics, model_client_for=model_client_for,
        persons=persons, access=access,
        reasoning_strip_store=reasoning_strip_store, registry=registry,
        rosters=rosters, route_model=route_model, tier=tier,
        tool_registry=tool_registry,
    )
    review = build_review_runner(
        cfg, dreamer_instructions, rt.dream_tools, model_client_for, metrics,
    )
    return EnabledWiring(dreamer_runtime=rt.dreamer_runtime, review=review)


@dataclass(frozen=True)
class DreamerRuntimeWiring:
    dream_model: Any
    dream_models: Any
    dream_tools: Any
    dreamer_runtime: Any


def dreamer_model_source(cfg: Any, tier: Any, dream_model: str, client: Any) -> factory.ModelSource:
    dream_effort = cfg.dreamer.reasoning_effort or tier.reasoning_effort
    _reasoning_effort(
        dream_effort,
        source="[dreamer] reasoning_effort (or [llm]'s, inherited)",
    )
    return factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            # `cfg.dreamer_max_tokens()` and NOT `tier.max_tokens`. The tier's number is size…
            # TAG TURN; a batch pass whose objective asks for one verdict per message has an…
            # that scales with the batch, and a tag-sized ceiling fails every run with…
            model=dream_model,
            max_tokens=cfg.dreamer_max_tokens(),
            temperature=tier.temperature,
            # `[dreamer] reasoning_effort` where set, else the tier's. At the tier's "low" this
            # model reasons its way to `{"candidates": []}` on a transcript that plainly cont…
            # claims, and to `MaxTokensReachedException` on a real one — reasoning is billed as…
            reasoning_effort=dream_effort,
            tool_choice=tool_choice.AFTER_SPEAKING,
        ),
    )


def _dreamer_runtime(
    allowlist: Any, audit: Any, cfg: Any,
    metrics: Any, model_client_for: Any, persons: Any,
    reasoning_strip_store: Any, registry: Any, rosters: Any, route_model: Any,
    tier: Any, tool_registry: Any, access: Any,
) -> DreamerRuntimeWiring:
    """The model runtime and scoped tool source for Cognition."""
    dream_model = route_model("dreamer", cfg.dreamer_model())
    dream_models = dreamer_model_source(cfg, tier, dream_model, model_client_for("dreamer"))
    dream_tools = _dream_tool_source(
        allowlist=allowlist, audit=audit, cfg=cfg,
        metrics=metrics, persons=persons,
        registry=registry, rosters=rosters, tool_registry=tool_registry, access=access,
    )
    dreamer_runtime = DreamerRuntime(
        models=dream_models,
        tools=dream_tools,
        # The SAME derivation the tag path uses, so a throttle can never be
        # misreported as a timeout (`factory.build_retry_policy`).
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        metrics=metrics,
        # The Dreamer's reasoning-strip store: a tool-using run at `[dreamer] reasoning_effort =
        # low` is the multi-turn shape the transport strips. Low-traffic and judgement-forming,
        # so a discarded reasoning there is both closer to being the verdict and harder to…
        reasoning_strip_store=reasoning_strip_store,
    )
    return DreamerRuntimeWiring(
        dream_model=dream_model,
        dream_models=dream_models,
        dream_tools=dream_tools,
        dreamer_runtime=dreamer_runtime,
    )
