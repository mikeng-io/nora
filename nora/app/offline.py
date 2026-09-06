"""Pipeline wiring shared by every offline (no real platform/network)
composition root — `tests/integration/harness.py` and `evals/harness.py` both
assemble a real `IngestPipeline` + `EngagementPipeline`, glued by a real
`Dispatcher`, against already-built collaborators (fakes for the adapter,
memory store, and model runner everywhere except production). This is the
one definition of that wiring, extracted out of `tests/integration/harness.py`
so `evals/` does not need a second copy of it (or `tests/` itself) to build
one: `evals/` may import `nora.app` and `nora.testing` and nothing else from
`nora/` — this module is the `nora.app` side of that boundary.

Deliberately builds NO collaborator itself — no `PlatformAdapter`,
`MemoryStore`, `AgentRunner`, or `RecordStore` is constructed here. The caller
always supplies those (real for `nora.app.composition.build_dependencies`'s
production path; fakes from `nora.testing` everywhere else), which is what
keeps this module free of any network concern.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from nora.agent import factory
from nora.agent.registry import ToolRegistry
from nora.config import Config
from nora.doctrine.loader import DoctrineStore
from nora.domain.ports import (
    EngagePolicy,
    MemoryStore,
    RecordStore,
    RosterSource,
    StandingStore,
)
from nora.engagement.context import StandingNotesReader
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.slots import ConversationSlots
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import CompositePolicy, RelevanceGatePolicy, TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.tag import Debounce

from .assembler import build_assembler
from .dispatch import Dispatcher
from .fire import FirePath, gathering_occasion, reminder_occasion
from .participation import ParticipationTrigger
from .runtime import BackgroundTasks


def build_offline_pipeline(
    cfg: Config,
    *,
    adapter: Any,
    audit: RecordStore,
    memory: MemoryStore,
    persons: Persons,
    doctrine_store: DoctrineStore,
    runner: Any,
    metrics: Metrics | None = None,
    roster_source: RosterSource | None = None,
    tool_registry: ToolRegistry | None = None,
    tag_trace_store: Any = None,
    debrief_wiring: Any = None,
    standing_event_wiring: Any = None,
    # The assembler's remaining collaborators (`nora.app.assembler.
    # build_assembler`), all optional and `None` by default so every existing
    # caller keeps working unchanged.
    notes: StandingNotesReader | None = None,
    standing: StandingStore | None = None,
    participation: ParticipationTrigger | None = None,
    extra_engage_policies: tuple[EngagePolicy, ...] = (),
    participation_runner: Any = None,
    participation_clock: Callable[[], float] | None = None,
    participation_opt_in: bool = False,
) -> tuple[Dispatcher, EngagementPipeline, BackgroundTasks, ParticipationTrigger | None]:
    """Wire ingest+engagement+dispatcher from already-built collaborators.

    Mirrors `build_dependencies`'s own pipeline-wiring tail exactly (same
    stages, same scalar fields read off `cfg`) but takes `adapter`/`memory`/
    `runner` as parameters instead of constructing the real, networked ones —
    the split `build_dependencies` doesn't need since production always wants
    the real collaborators. The same is true of the assembler's own
    collaborators above: this module builds none of them (module docstring),
    so a caller that wants `<notes>`/`<standing>` to render passes
    its own `nora.testing` in-memory doubles.

    `participation_runner` is the participation occasion's eval seam
    (design/dreamer-agent-loop/35 §9 gap 1): when given, this builds a REAL
    `FirePath` — same `deliver.build`, guards and accounting as production —
    plus the quiescence `ParticipationTrigger`, and wires the trigger into the
    dispatcher, so an eval drives the participation path exactly as deployed
    (`evals/` may not import `nora.app.fire` itself — the boundary that put
    this module here). The trigger is the fourth return value (`None` when
    neither `participation` nor `participation_runner` was given); the caller
    drives `close_due_windows` on it, with `participation_clock` injecting the
    clock the settle/cooldown durations are measured against.
    """
    metrics = metrics or Metrics()
    background = BackgroundTasks()
    debounce = Debounce(cfg.engagement.trigger.min_interval_seconds)
    # `extra_engage_policies` is the participation occasion's test seam
    # (design/dreamer-agent-loop/35): an integration harness exercising the
    # occasion wraps the trigger-text policy in a `CompositePolicy` carrying a
    # real `RelevanceGatePolicy`, so an untagged message in an opted-in room
    # reaches the dispatcher with `EngageDecision.relevance_gate` set exactly
    # as production's wiring does it.
    policy: Any = TriggerTextPolicy(cfg.engagement.trigger.text)
    # `participation_opt_in` is the eval half of the room gate: a real
    # `RelevanceGatePolicy` over THIS harness's `permissive_policy()` (whose
    # rooms are all opted in), so an untagged message is marked
    # `EngageDecision.relevance_gate` exactly as production's opted-in rooms
    # do. `evals/` may not import `nora.ingest.policy` itself — the boundary
    # this module exists for.
    if participation_opt_in:
        extra_engage_policies = (
            *extra_engage_policies, RelevanceGatePolicy(permissive_policy()),
        )
    if extra_engage_policies:
        policy = CompositePolicy((policy, *extra_engage_policies))
    assembler = build_assembler(
        cfg, audit=audit, memory=memory, persons=persons, metrics=metrics,
        policy=permissive_policy(),
        notes=notes, standing=standing,
    )

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        background.spawn(coro)

    ingest = IngestPipeline(
        audit=audit, memory=memory, persons=persons,
        policy=policy, allowlist=permissive_policy(), metrics=metrics, spawn=_spawn,
        standing_event_wiring=standing_event_wiring,
    )

    tier = cfg.llm
    limits = factory.build_limits(
        output_tokens=tier.limits.output_tokens,
        turns=tier.limits.turns,
        total_tokens=tier.limits.total_tokens,
    )
    # Default: a bare registry (every tool inert/`unavailable`). The eval's live
    # routing gate passes one wired with canned backends (F4) so `weather`/
    # `web_search`/`place_info`/… answer `ok`, removing the fallback confound.
    # `budget_overrides` threaded, so an offline replay runs under the SAME
    # ceilings production does. Without it, `[llm.budgets]` would silently make
    # replay a different agent from the one deployed — which is the whole thing
    # a replay path exists to rule out.
    tool_registry = tool_registry if tool_registry is not None else ToolRegistry(
        budget_overrides=cfg.llm.budgets)
    # Hoisted out of the `EngagementPipeline(...)` call because the
    # participation trigger below must hold the SAME instance — two slot maps
    # would be two locks and no serialisation at all.
    slots = ConversationSlots(
        maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn,
    )
    engagement = EngagementPipeline(
        # One adapter, keyed by the platform it declares itself to be: an
        # offline harness replays a single platform's traffic, so this is the
        # whole routing table rather than a degenerate case of it.
        adapters={adapter.platform: adapter},
        audit=audit, memory=memory, persons=persons,
        doctrine_store=doctrine_store, context_assembler=assembler, runner=runner,
        tool_registry=tool_registry,
        # `permissive_policy()`, matching `ingest`'s `allowlist` above: an
        # offline harness has no real `policy.toml` to resolve a per-conversation
        # MCP allowlist from, and this module builds no MCP tools either
        # (`ToolRegistry()` above takes no `mcp_tools`), so the allowlist this
        # resolves to is never disclosed to anything real. A caller that wants
        # to exercise MCP scoping supplies its own `EngagementPipeline`.
        policy=permissive_policy(),
        debounce=debounce, canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        ai_peer=cfg.memory.ai_peer,
        context_window=cfg.store.context_window, timeout_seconds=tier.timeout_seconds,
        limits=limits, metrics=metrics, spawn=background.spawn, llm_state=None,
        # Defaults to None: an offline harness has no signal-cli to ask, and
        # `nora.agent.room.Room` reports an absent source as UNAVAILABLE rather
        # than as an empty room — so the three room-scoped tools degrade
        # honestly here instead of claiming the group is empty. A harness that
        # wants to exercise them passes a `RosterSource`.
        # One platform's roster, keyed by the adapter's own platform — the
        # same shape `adapters` above takes, for the same reason.
        rosters={adapter.platform: roster_source} if roster_source else {},
        slots=slots,
        # `None` by default (Task 6): most offline/eval scenarios have no
        # retention store and don't need one — `EngagementPipeline` treats it
        # as fully inert, matching every other optional collaborator here. A
        # caller that wants to assert on retained `TagTrace`s (e.g. the
        # integration suite) passes its own.
        tag_trace_store=tag_trace_store,
        # The debrief stage's collaborators (26-standing). `None` here in
        # every eval/integration run by default, for the same reason
        # `tag_trace_store` is: the fold spends a model call inside the
        # reply slot, and an eval measuring a tag's trajectory must not
        # have a second, unrelated completion added to its budget. A
        # caller that wants to exercise the stage passes its own.
        debrief_wiring=debrief_wiring,
    )
    participation_trigger = participation
    if participation_trigger is None and participation_runner is not None:
        # The production wiring's mirror (`composition.build_dependencies`):
        # the occasion goes through a REAL FirePath — same deliver.build,
        # guards, audit and accounting the deployment runs — with only the
        # model swapped. `tests/architecture/test_assembler_parity.py` pins
        # this FirePath(...) call's keyword NAMES to the composition root's,
        # so a collaborator added on one side and forgotten here fails a test
        # instead of letting the eval prompt drift from production's.
        fire_path = FirePath(
            persons=persons, records=audit, memory=memory,
            doctrine_store=doctrine_store, metrics=metrics,
            runner=runner, adapters={adapter.platform: adapter},
            registry=tool_registry,
            canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
            ai_peer=cfg.memory.ai_peer,
            context_window=cfg.store.context_window,
            limits=limits,
            # `[heartbeat]`, not `[llm]` — the same choice production makes;
            # nobody is waiting on a fire turn.
            timeout_seconds=cfg.heartbeat.timeout_seconds,
            clock=lambda: datetime.now(UTC),
            rosters={adapter.platform: roster_source} if roster_source else {},
            preview_source=None,
            llm_state=None,
            tool_call_log_store=None,
            event_registry={
                "reminder": reminder_occasion,
                "gathering": gathering_occasion,
            },
            standing=standing,
            standing_store_timeout_seconds=cfg.standing.store_timeout_seconds,
            # The SAME tier stores the assembler above was handed: the
            # participation determination's prompt is supposed to carry
            # whatever standing/notes the room has — an eval whose
            # FirePath read emptier tiers than its tag path would grade the
            # knowledge-cut cases against a prompt production never builds.
            notes=notes,
            policy=permissive_policy(),
            participation_runner=participation_runner,
            # The §10.6 shadow mirror, mirrored from the composition root's
            # gating (the parity test pins these kwarg NAMES to production's):
            # an offline/eval run can review determinations on the mirror
            # exactly as a deployment would. The eval suites leave
            # `[participation] shadow` off, so this is inert there by default.
            shadow_channel=(
                cfg.participation.shadow_channel
                if cfg.participation.enabled and cfg.participation.shadow else None
            ),
            shadow_platform=cfg.participation.shadow_platform,
            # The gate's review channel, mirrored from the composition root's
            # gating for the same parity reason as the mirror above. The eval
            # suites leave `[relevance_gate] shadow` off, so it is inert
            # there — an eval that reviewed instead of speaking would measure
            # the review surface, not the turn.
            gate_shadow_channel=(
                cfg.relevance_gate.shadow_channel
                if cfg.relevance_gate.enabled and cfg.relevance_gate.shadow else None
            ),
            gate_shadow_platform=cfg.relevance_gate.shadow_platform,
        )
        participation_trigger = ParticipationTrigger(
            speak=fire_path.speak,
            # The SAME `slots` the engagement pipeline holds — two instances
            # would be two locks and no serialisation at all.
            slots=slots,
            settle_seconds=cfg.participation.settle_seconds,
            cooldown_seconds=cfg.participation.cooldown_minutes * 60,
            # NO gate, deliberately. `gate=None` admits every closed window,
            # which is what an eval of the TURN needs: a suite that had to
            # get past a live gate call first would be measuring the gate's
            # verdict and the turn's behaviour in one number, and a flaky
            # gate would read as a flaky turn. The gate has its own cases.
            clock=participation_clock or time.monotonic,
        )
    dispatcher = Dispatcher(
        ingest=ingest, engagement=engagement, background=background,
        participation=participation_trigger,
    )
    return dispatcher, engagement, background, participation_trigger
