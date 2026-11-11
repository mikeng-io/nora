from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent import factory, tool_choice
from nora.agent.dreamer import (
    AdaptorSchedule,
    DreamerRuntime,
    verify_tool_subsets,
)
from nora.app.cognition_session import CognitionSessionRunner
from nora.app.cognition_wiring import (
    SESSION_SHUTDOWN_GRACE_SECONDS,
    CognitionShutdown,
    require_session_hook,
)
from nora.app.cognition_wiring import session_guidance as cognition_session_guidance
from nora.app.composition_cognition import cognition_session
from nora.app.composition_dream import (
    _dream_grants_and_budgets,
    _dream_objectives,
    _dream_tool_source,
    _lessons_evidence,
)
from nora.app.composition_helpers import _adaptor_budget, _reasoning_effort
from nora.app.startup import StartupError
from nora.app.wake import Floor, WakeEvaluator
from nora.config import Config
from nora.domain.ports import DreamAdaptor
from nora.domain.stimuli import WakeHook, WakePolicy
from nora.infra.sql.dream_schedule import SqlDreamScheduleClock
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.language.lessons import CognitionLessons
from nora.obs.metrics import Metrics

"""Dreamer and Cognition wiring steps."""


log = logging.getLogger("nora.app.composition")




def _build_wake_evaluator(
    cfg: Config, store: SqlStimulusStore, units: Callable[[], Sequence[str]],
    metrics: Metrics, on_wake: WakeHook | None,
    session_units: Callable[[], Collection[str]] | None = None, floors_ready: bool = True,
    upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None,
) -> WakeEvaluator:
    """The cognition loop's wake evaluator: every"""
    return WakeEvaluator(
        store=store,
        units=units,
        policy=WakePolicy(
            quiet_seconds=cfg.cognition.quiet_seconds,
            cue_quiet_seconds=cfg.cognition.cue_quiet_seconds,
            batch_max=cfg.cognition.batch_max,
            max_age_seconds=cfg.cognition.max_age_seconds,
            cooldown_seconds=cfg.cognition.cooldown_seconds,
            max_concurrent_sessions=cfg.cognition.max_concurrent_sessions,
            session_timeout_seconds=cfg.cognition.session_timeout_seconds,
            backoff_max_seconds=cfg.cognition.backoff_max_seconds,
            max_consecutive_failures=cfg.cognition.max_consecutive_failures,
        ),
        metrics=metrics,
        floors=tuple(Floor.parse(f) for f in cfg.cognition.time_floors),
        tz=ZoneInfo(cfg.cognition.timezone),
        floor_grace_seconds=cfg.cognition.floor_grace_seconds,
        mode="live",
        store_timeout_seconds=cfg.cognition.store_timeout_seconds,
        on_wake=on_wake,
        session_units=session_units,
        floors_ready=floors_ready,
        upcoming=upcoming,
    )


@dataclass(frozen=True)
class PreambleWiring:
    dream_adaptors: Any
    dream_schedule_clock: Any
    dream_schedules: Any
    dreamer_runtime: Any


def preamble(engine: Any)-> PreambleWiring:


    # ---- the Dreamer runtime -------------------- Constructed only when `[dreamer]
    # enabled` is true, the shape every gated subsystem in this file uses: a subsystem that
    # is off builds NOTHING, so a wiring mistake in it cannot cost a deployment that never…
    dream_adaptors: tuple[DreamAdaptor, ...] = ()
    dreamer_runtime: DreamerRuntime | None = None
    dream_schedules: Mapping[str, AdaptorSchedule] = {}
    # ONE schedule-state store, shared by the loop's slots and
    # any other schedule clock: two instances would be two
    # engines over one table for no reason.
    dream_schedule_clock = SqlDreamScheduleClock(engine)
    return PreambleWiring(
        dream_adaptors=dream_adaptors,
        dream_schedule_clock=dream_schedule_clock,
        dream_schedules=dream_schedules,
        dreamer_runtime=dreamer_runtime,
    )


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
    dream_adaptors: Any
    dream_schedules: Any
    dreamer_runtime: Any


async def enabled(
    allowlist: Any, audit: Any, cfg: Any, clock: Any,
    cognition_context_screen: Any, cognition_lessons: Any, cognition_memory: Any,
    content_checks: Any, culture_reader: Any, doctrine_store: Any, document_service: Any,
    documents_context: Any, dream_adaptors: Any, dream_schedules: Any,
    dreamer_instructions: Any, dreamer_runtime: Any, engine: Any, event_store: Any,
    events: Any, ledger_store: Any, lineage: Any, media_join: Any,
    memo_store: Any, memory: Any, metrics: Any, mobility: Any,
    model_client_for: Any, note_store: Any, observation_store: Any, persons: Any,
    presence_circles: Any, presence_reader: Any, proactive_homes: Any, profile_index: Any,
    profile_reader: Any, reasoning_strip_store: Any, registry: Any, reminder_caps: Any,
    reminders: Any, rosters: Any, route_model: Any, session_guidance: Any,
    shared: Any, standing_fold: Any, standing_store: Any, stimulus_store: Any,
    tag_trace_store: Any, tier: Any, tool_registry: Any, unit_rooms: Any,
    unit_scopes: Any,
) -> EnabledWiring:
    dream_adaptors = ()
    dreamer_runtime = None
    dream_schedules = {}
    cognition_runner = None
    cognition_session_units = None
    cognition_upcoming = None
    if cfg.dreamer.enabled:
        objectives = _dream_objectives(
            allowlist=allowlist, cfg=cfg, clock=clock,
            content_checks=content_checks, engine=engine,
            media_join=media_join, metrics=metrics, note_store=note_store,
            persons=persons, profile_index=profile_index,
            unit_scopes=unit_scopes,
        )
        dream_adaptors = objectives.adaptors
        dream_schedules = objectives.schedules
        cognition_lessons = _lessons_evidence(
            allowlist=allowlist, cfg=cfg, content_checks=content_checks,
            doctrine_store=doctrine_store,
            dreamer_instructions=dreamer_instructions, engine=engine,
            metrics=metrics, model_client_for=model_client_for,
            persons=persons, registry=registry, route_model=route_model,
        )
        rt = _dreamer_runtime(
            allowlist=allowlist, audit=audit, cfg=cfg,
            content_checks=content_checks, dream_adaptors=dream_adaptors,
            dream_schedules=dream_schedules,
            dreamer_instructions=dreamer_instructions, memory=memory,
            metrics=metrics, model_client_for=model_client_for,
            persons=persons, proactive_homes=proactive_homes,
            reasoning_strip_store=reasoning_strip_store, registry=registry,
            rosters=rosters, route_model=route_model, tier=tier,
            tool_registry=tool_registry,
        )
        dreamer_runtime = rt.dreamer_runtime
        if cfg.cognition.enabled and stimulus_store is not None:
            cog = await cognition_session(
                allowlist=allowlist, audit=audit, cfg=cfg, clock=clock,
                cognition_context_screen=cognition_context_screen,
                cognition_lessons=cognition_lessons,
                cognition_memory=cognition_memory,
                content_checks=content_checks, culture_reader=culture_reader,
                document_service=document_service,
                documents_context=documents_context,
                dream_model=rt.dream_model, dream_models=rt.dream_models,
                dream_tools=rt.dream_tools,
                dreamer_instructions=dreamer_instructions,
                dreamer_runtime=dreamer_runtime, engine=engine,
                event_store=event_store, events=events,
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
                tool_registry=tool_registry, unit_rooms=unit_rooms,
                unit_scopes=unit_scopes,
            )
            cognition_runner = cog.cognition_runner
            cognition_session_units = cog.cognition_session_units
            cognition_upcoming = cog.cognition_upcoming
    return EnabledWiring(
        cognition_runner=cognition_runner,
        cognition_session_units=cognition_session_units,
        cognition_upcoming=cognition_upcoming,
        dream_adaptors=dream_adaptors,
        dream_schedules=dream_schedules,
        dreamer_runtime=dreamer_runtime,
    )


@dataclass(frozen=True)
class WakeWiring:
    cognition_shutdown: Any
    wake_evaluator: Any


def wake(
    cfg: Any, cognition_runner: Any, cognition_session_units: Any, cognition_upcoming: Any,
    metrics: Any, stimulus_store: Any,
) -> WakeWiring:

    # The wake evaluator, after the Dreamer block so it can be handed the
    # runner's hook. The boot check below refuses `enabled` with no runner or
    # no hook. Only units a session can run for are woken.
    wake_evaluator: WakeEvaluator | None = None
    cognition_shutdown: CognitionShutdown | None = None
    if stimulus_store is not None:
        wake_evaluator = _build_wake_evaluator(
            cfg,
            store=stimulus_store,
            units=lambda: sorted(cognition_session_units or ()),
            metrics=metrics,
            on_wake=cognition_runner.wake_hook if cognition_runner is not None else None,
            session_units=(
                None if cognition_session_units is None else (lambda: cognition_session_units)
            ),
            floors_ready=True,
            upcoming=cognition_upcoming,
        )
        if cognition_runner is not None:
            cognition_shutdown = CognitionShutdown(
                evaluator=wake_evaluator,
                runner=cognition_runner,
                grace_seconds=SESSION_SHUTDOWN_GRACE_SECONDS,
            )
    try:
        require_session_hook(
            enabled=cfg.cognition.enabled,
            runner=cognition_runner,
            evaluator=wake_evaluator,
            session_units=cognition_session_units,
        )
    except RuntimeError as exc:
        raise StartupError(str(exc)) from exc
    return WakeWiring(cognition_shutdown=cognition_shutdown, wake_evaluator=wake_evaluator)


@dataclass(frozen=True)
class DreamerRuntimeWiring:
    dream_model: Any
    dream_models: Any
    dream_tools: Any
    dreamer_runtime: Any


def _dreamer_runtime(
    allowlist: Any, audit: Any, cfg: Any, content_checks: Any,
    dream_adaptors: Any, dream_schedules: Any, dreamer_instructions: Any, memory: Any,
    metrics: Any, model_client_for: Any, persons: Any, proactive_homes: Any,
    reasoning_strip_store: Any, registry: Any, rosters: Any, route_model: Any,
    tier: Any, tool_registry: Any,
) -> DreamerRuntimeWiring:
    """The DreamerRuntime the loop hosts every adaptor on."""
    dream_model = route_model("dreamer", cfg.dreamer_model())
    dream_effort = cfg.dreamer.reasoning_effort or tier.reasoning_effort
    _reasoning_effort(
        dream_effort,
        source="[dreamer] reasoning_effort (or [llm]'s, inherited)",
    )
    dream_models = factory.ModelSource(
        client=model_client_for("dreamer"),
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
    dream_tools = _dream_tool_source(
        allowlist=allowlist, audit=audit, cfg=cfg, memory=memory,
        metrics=metrics, persons=persons, proactive_homes=proactive_homes,
        registry=registry, rosters=rosters, tool_registry=tool_registry,
    )
    dream_grants, dream_budgets, fallback = _dream_grants_and_budgets(cfg)
    dreamer_runtime = DreamerRuntime(
        models=dream_models,
        instructions=dreamer_instructions,
        tools=dream_tools,
        budgets=dream_budgets,
        default_budget=_adaptor_budget(fallback),
        # The SAME derivation the tag path uses, so a throttle can never be
        # misreported as a timeout (`factory.build_retry_policy`).
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        metrics=metrics,
        model_name=dream_model,
        schedules=dream_schedules,
        # The SAME `[llm.budgets]` the tag path applies through
        # `ToolRegistry(budget_overrides=...)`, so one number in
        # `config.toml` is one ceiling whichever loop asks.
        tool_budget_overrides=cfg.llm.budgets,
        grants=dream_grants,
        # The per-CALL-SITE allowance for `inputs`/`commit`,
        # multiplied by each adaptor's own declared `store_calls_per_tick`.
        store_timeout_seconds=cfg.dreamer.store_timeout_seconds,
        # The Dreamer's reasoning-strip store: a tool-using run at `[dreamer] reasoning_effort =
        # low` is the multi-turn shape the transport strips. Low-traffic and judgement-forming,
        # so a discarded reasoning there is both closer to being the verdict and harder to…
        reasoning_strip_store=reasoning_strip_store,
        content_checks=content_checks,
    )
    verify_tool_subsets(
        dream_adaptors,
        tools=dream_tools,
        grants=dream_grants,
        # The per-adaptor per-tool ceilings, read off the SAME budgets the runtime got above —
        # so «this ceiling names a tool the adaptor does not hold» is a boot failure rather than
        # a raise the operator made that quietly does nothing.
        tool_calls={name: b.tool_calls for name, b in cfg.dreamer.budgets.items()},
    )
    return DreamerRuntimeWiring(
        dream_model=dream_model,
        dream_models=dream_models,
        dream_tools=dream_tools,
        dreamer_runtime=dreamer_runtime,
    )
