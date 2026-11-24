from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from nora.agent import factory, tool_choice
from nora.agent.dreamer import (
    DreamerRuntime,
)
from nora.app.cognition_session import CognitionSessionRunner
from nora.app.cognition_wiring import (
    CognitionShutdown,
)
from nora.app.cognition_wiring import session_guidance as cognition_session_guidance
from nora.app.composition_cognition import cognition_session
from nora.app.composition_dream import (
    _dream_tool_source,
    _lessons_evidence,
)
from nora.app.composition_helpers import _reasoning_effort
from nora.app.composition_review import build_review_runner
from nora.app.startup import StartupError
from nora.language.lessons import CognitionLessons

"""Dreamer and Cognition wiring steps."""


log = logging.getLogger("nora.app.composition")




@dataclass(frozen=True)
class GuidanceWiring:
    cognition_lessons: Any
    cognition_memory: Any
    cognition_runner: Any
    session_guidance: Any


def guidance(dreamer_instructions: Any)-> GuidanceWiring:

    # The Cognition session: built inside the Dreamer block below ONLY under `[cognition]
    # enabled`; off builds nothing here, no model client and no agent-runner import. : Hot-
    # loaded session guidance, gated by the offered tools during assembly.
    session_guidance = cognition_session_guidance(dreamer_instructions)
    cognition_runner: CognitionSessionRunner | None = None
    cognition_memory = None
    # The lesson evidence seam the session reads and settles (`[lessons]
    # enabled` fills it inside the Dreamer block below; Cognition needs the
    # Dreamer anyway — `_lessons_need_cognition`).
    cognition_lessons: CognitionLessons | None = None
    return GuidanceWiring(
        cognition_lessons=cognition_lessons,
        cognition_memory=cognition_memory,
        cognition_runner=cognition_runner,
        session_guidance=session_guidance,
    )


@dataclass(frozen=True)
class CognitionInitsWiring:
    cognition_session_units: Any
    cognition_upcoming: Any


def cognition_inits() -> CognitionInitsWiring:
    #: The units a session can run for (a home room each); only they are woken.
    cognition_session_units: Collection[str] | None = None
    cognition_upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None
    return CognitionInitsWiring(
        cognition_session_units=cognition_session_units, cognition_upcoming=cognition_upcoming
    )


@dataclass(frozen=True)
class EnabledWiring:
    cognition_runner: Any
    cognition_session_units: Any
    cognition_upcoming: Any
    dreamer_runtime: Any
    review: Any = None


async def enabled(
    allowlist: Any, audit: Any, cfg: Any, clock: Any,
    cognition_context_screen: Any, cognition_lessons: Any, cognition_memory: Any,
    content_checks: Any, doctrine_store: Any, document_service: Any,
    documents_context: Any,
    dreamer_instructions: Any, engine: Any, entry_store: Any,
    event_store: Any,
    ledger_store: Any, lineage: Any, media_join: Any,
    memo_store: Any, metrics: Any, mobility: Any,
    model_client_for: Any, note_store: Any, observation_store: Any, persons: Any,
    presence_circles: Any, presence_reader: Any, profile_index: Any,
    profile_reader: Any, reasoning_strip_store: Any, registry: Any, reminder_caps: Any,
    reminders: Any, rosters: Any, route_model: Any, session_guidance: Any,
    shared: Any, standing_fold: Any, standing_store: Any, stimulus_store: Any,
    tag_trace_store: Any, tier: Any, tool_registry: Any, unit_rooms: Any,
    unit_scopes: Any, access: Any = None,
) -> EnabledWiring:
    dreamer_runtime = None
    review = None
    cognition_runner = None
    cognition_session_units = None
    cognition_upcoming = None
    if cfg.dreamer.enabled:
        cognition_lessons = _lessons_evidence(
            allowlist=allowlist, cfg=cfg, content_checks=content_checks,
            doctrine_store=doctrine_store,
            dreamer_instructions=dreamer_instructions, engine=engine,
            metrics=metrics, model_client_for=model_client_for,
            persons=persons, registry=registry, route_model=route_model,
        )
        rt = _dreamer_runtime(
            allowlist=allowlist, audit=audit, cfg=cfg,
            metrics=metrics, model_client_for=model_client_for,
            persons=persons, access=access,
            reasoning_strip_store=reasoning_strip_store, registry=registry,
            rosters=rosters, route_model=route_model, tier=tier,
            tool_registry=tool_registry,
        )
        dreamer_runtime = rt.dreamer_runtime
        review = build_review_runner(
            cfg, dreamer_instructions, rt.dream_tools, model_client_for, metrics,
        )
        if cfg.cognition.enabled and stimulus_store is not None:
            cog = await cognition_session(
                allowlist=allowlist, audit=audit, cfg=cfg, clock=clock,
                cognition_context_screen=cognition_context_screen,
                cognition_lessons=cognition_lessons,
                cognition_memory=cognition_memory,
                content_checks=content_checks,
                document_service=document_service,
                documents_context=documents_context,
                dream_model=rt.dream_model, dream_models=rt.dream_models,
                dream_tools=rt.dream_tools,
                dreamer_instructions=dreamer_instructions,
                dreamer_runtime=dreamer_runtime, engine=engine,
                entry_store=entry_store,
                event_store=event_store,
                ledger_store=ledger_store, lineage=lineage,
                media_join=media_join, memo_store=memo_store,
                metrics=metrics, mobility=mobility, note_store=note_store,
                observation_store=observation_store, persons=persons,
                presence_circles=presence_circles,
                presence_reader=presence_reader,
                profile_reader=profile_reader, registry=registry,
                reminder_caps=reminder_caps, reminders=reminders,
                rosters=rosters, session_guidance=session_guidance,
                shared=shared, standing_fold=standing_fold,
                standing_store=standing_store,
                stimulus_store=stimulus_store,
                tag_trace_store=tag_trace_store, tier=tier,
                tool_registry=tool_registry, unit_rooms=unit_rooms, access=access,
                unit_scopes=unit_scopes,
            )
            cognition_runner = cog.cognition_runner
            cognition_session_units = cog.cognition_session_units
            cognition_upcoming = cog.cognition_upcoming
    return EnabledWiring(
        cognition_runner=cognition_runner,
        cognition_session_units=cognition_session_units,
        cognition_upcoming=cognition_upcoming,
        dreamer_runtime=dreamer_runtime,
        review=review,
    )


def legacy_cognition_shutdown(cfg: Any, runner: Any) -> CognitionShutdown | None:
    if runner is None:
        if cfg.cognition.enabled:
            raise StartupError("[cognition] enabled but no session runner was built")
        return None
    return CognitionShutdown(runner=runner)


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
