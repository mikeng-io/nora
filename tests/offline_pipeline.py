"""Pipeline wiring shared by every offline (no real platform/network) composition root —
`tests/integration/harness.py` and `tests/offline_eval_harness.py` both assemble a
real `IngestPipeline` + `EngagementPipeline`, glued by a real `Dispatcher`, against
already-built collaborators (fakes for the adapter, memory store, and model runner
everywhere except production). This is the one definition of that wiring, so neither
harness needs a second copy of it.

Deliberately builds NO collaborator itself — no `PlatformAdapter`, `MemoryStore`,
`AgentRunner`, or `RecordStore` is constructed here. The caller always supplies those
(real for `nora.app.composition.build_dependencies`'s production path; fakes from
`nora.testing` everywhere else), which is what keeps this module free of any network
concern.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any

from nora.agent import factory
from nora.agent.registry import ToolRegistry
from nora.app.assembler import build_assembler, lineage_policy
from nora.app.dispatch import Dispatcher
from nora.app.fire import FirePath, proactive_occasion, reminder_occasion
from nora.app.participation import ParticipationTrigger
from nora.app.runtime import BackgroundTasks
from nora.config import Config
from nora.doctrine.loader import DoctrineStore
from nora.domain.media_join import MediaJoin
from nora.domain.ports import (
    EngagePolicy,
    MemoryStore,
    RecordStore,
    RosterSource,
    StandingStore,
)
from nora.domain.quote_window import QuoteWindow
from nora.engagement.context import StandingNotesReader
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.self_presence import PresenceReader
from nora.engagement.slots import ConversationSlots
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import CompositePolicy, RelevanceGatePolicy, TriggerTextPolicy
from nora.ingest.relevance_gate import RelevanceGate
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.tag import Debounce


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
    # The assembler's remaining collaborators (`nora.app.assembler.
    # build_assembler`), all optional and `None` by default so every existing
    # caller keeps working unchanged.
    notes: StandingNotesReader | None = None,
    standing: StandingStore | None = None,
    media_join: MediaJoin | None = None,
    quote_window: QuoteWindow | None = None,
    participation: ParticipationTrigger | None = None,
    extra_engage_policies: tuple[EngagePolicy, ...] = (),
    participation_runner: Any = None,
    proactive_runner: Any = None,
    participation_clock: Callable[[], float] | None = None,
    participation_opt_in: bool = False,
    participation_gate: RelevanceGate | None = None,
    extra_adapters: Mapping[str, Any] | None = None,
) -> tuple[Dispatcher, EngagementPipeline, BackgroundTasks, ParticipationTrigger | None]:
    """Wire ingest+engagement+dispatcher from already-built collaborators.

    Mirrors `build_dependencies`'s own pipeline-wiring tail exactly (same stages, same
    scalar fields read off `cfg`) but takes `adapter`/`memory`/`runner` as parameters
    instead of constructing the real, networked ones — the split `build_dependencies`
    doesn't need since production always wants the real collaborators. The same is true of
    the assembler's own collaborators above: this module builds none of them (module
    docstring), so a caller that wants `<notes>`/`<standing>` to render passes its own
    `nora.testing` in-memory doubles.

    `participation_runner` is the participation occasion's eval seam: when given, this
    builds a REAL `FirePath` — same `deliver.build`, guards and accounting as production —
    plus the quiescence `ParticipationTrigger`, and wires the trigger into the dispatcher,
    so an eval drives the participation path exactly as deployed. The trigger is the
    fourth return value (`None` when neither `participation` nor `participation_runner`
    was given); the caller drives `close_due_windows` on it, with `participation_clock`
    injecting the clock the settle/cooldown durations are measured against, and
    `participation_gate` optionally putting a REAL `RelevanceGate` in front of it (see the
    `gate=` comment at the trigger's construction).

    `extra_adapters` serves rooms on other platforms, keyed by platform. A caller that
    asserts nothing was sent into such a room needs one: with no adapter for its
    platform, a send there raises instead of being recorded, and the assertion passes
    vacuously.
    """
    if adapter.platform in (extra_adapters or {}):
        raise ValueError(f"extra_adapters may not replace the {adapter.platform!r} adapter")
    adapters = {adapter.platform: adapter, **(extra_adapters or {})}
    metrics = metrics or Metrics()
    background = BackgroundTasks()
    debounce = Debounce(cfg.engagement.trigger.min_interval_seconds)
    # `extra_engage_policies` is the participation occasion's test seam: an integration
    # harness exercising the occasion wraps the trigger-text policy in a `CompositePolicy`
    # carrying a real `RelevanceGatePolicy`, so an untagged message in an opted-in room
    # reaches the dispatcher with `EngageDecision.relevance_gate` set exactly as
    # production's wiring does it.
    policy: Any = TriggerTextPolicy(cfg.engagement.trigger.text)
    # `participation_opt_in` is the eval half of the room gate: a real
    # `RelevanceGatePolicy` over THIS harness's `permissive_policy()` (whose
    # rooms are all opted in), so an untagged message is marked
    # `EngageDecision.relevance_gate` exactly as production's opted-in rooms
    # do.
    if participation_opt_in:
        extra_engage_policies = (
            *extra_engage_policies, RelevanceGatePolicy(permissive_policy()),
        )
    if extra_engage_policies:
        policy = CompositePolicy((policy, *extra_engage_policies))
    # `<presence>` — the production reader over this harness's own policy and adapters, so
    # an eval prompt carries the block a production turn carries: the network, the
    # harness's (empty) room table, the fake adapters' health, and the handles
    # `config.evaluation.toml` declares.
    presence_reader = PresenceReader(
        policy=permissive_policy(), adapters=adapters,
        handles=cfg.agent.handles,
    )
    assembler = build_assembler(
        cfg, audit=audit, memory=memory, persons=persons, metrics=metrics,
        policy=permissive_policy(),
        notes=notes, standing=standing,
        # The media join. `None` renders the bare handles a row actually carries; an eval
        # that wants `[image a3f2c1: …]` in its prompt builds a `MediaJoin` over
        # `nora.testing.FakeMediaObjectStore`, exactly as it passes its own doubles for
        # `notes`/`standing`.
        media_join=media_join,
        # DEC-195's shared quote-window resolver. `None` is the plain `context_window`
        # shape, no growth; an eval exercising quote-window growth builds one over the SAME
        # `audit` this module already takes.
        quote_window=quote_window,
        presence=presence_reader,
        # `<memos>` (DEC-306) — `None`: no memo store here, so no block.
        memos=None,
        # `<culture>` (DEC-207) — `None` here for the fire path's reason
        # below: the eval subject (`nora.subject`) reconstructs a room's real
        # account at a real instant, and this harness has no store to hold one.
        culture=None,
        # `<lessons>` (L5) — `None`, as `culture`: no store here, and the
        # eval subject seeds lessons through its own sandbox.
        lessons=None,
        # `<computer>` — `None`: this harness has no computer, and an eval's VM workspace
        # would be empty anyway.
        computer=None,
        # `<documents>` — `None`: this harness has no document store; the live eval
        # subject builds the real reader through composition.
        documents=None,
        # `<subagents>` — `None`: no job store here, and `[subagent]` ships off.
        subagents=None,
    )

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        background.spawn(coro)

    ingest = IngestPipeline(
        audit=audit, memory=memory, persons=persons,
        policy=policy, allowlist=permissive_policy(), metrics=metrics, spawn=_spawn,
    )

    tier = cfg.llm
    limits = factory.build_limits(
        output_tokens=tier.limits.output_tokens,
        turns=tier.limits.turns,
        total_tokens=tier.limits.total_tokens,
    )
    # Default: a bare registry (every tool inert/`unavailable`). The eval's live routing
    # gate passes one wired with canned backends so `weather`/`web_search`/`place_info`/…
    # answer `ok`, removing the fallback confound. `budget_overrides` threaded, so an
    # offline replay runs under the SAME ceilings production does. Without it,
    # `[llm.budgets]` would silently make replay a different agent from the one deployed —
    # which is the whole thing a replay path exists to rule out.
    tool_registry = tool_registry if tool_registry is not None else ToolRegistry(
        budget_overrides=cfg.llm.budgets)
    # Hoisted out of the `EngagementPipeline(...)` call because the
    # participation trigger below must hold the SAME instance — two slot maps
    # would be two locks and no serialisation at all.
    slots = ConversationSlots(
        maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn,
    )
    engagement = EngagementPipeline(
        # Keyed by the platform each adapter declares itself to be: the
        # replayed platform's adapter plus any `extra_adapters`.
        adapters=adapters,
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
        limits=limits, defer_tools=cfg.llm.defer_tools,
        metrics=metrics, spawn=background.spawn, llm_state=None,
        # Defaults to None: an offline harness has no signal-cli to ask, and
        # `nora.agent.room.Room` reports an absent source as UNAVAILABLE rather
        # than as an empty room — so the three room-scoped tools degrade
        # honestly here instead of claiming the group is empty. A harness that
        # wants to exercise them passes a `RosterSource`.
        # The replayed platform's roster only, keyed by that adapter's platform.
        rosters={adapter.platform: roster_source} if roster_source else {},
        slots=slots,
        # `None` by default: most offline/eval scenarios have no retention store and don't
        # need one — `EngagementPipeline` treats it as fully inert, matching every other
        # optional collaborator here. A caller that wants to assert on retained
        # `TagTrace`s (e.g. the integration suite) passes its own.
        tag_trace_store=tag_trace_store,
        # The debrief stage's collaborators. `None` here in every eval/integration run by
        # default, for the same reason `tag_trace_store` is: the fold spends a model call
        # inside the reply slot, and an eval measuring a tag's trajectory must not have a
        # second, unrelated completion added to its budget. A caller that wants to
        # exercise the stage passes its own.
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
            # The SAME join the tag assembler above is given, so an eval's fire
            # prompt shows what an eval's tag prompt shows (the parity gate
            # `tests/architecture/test_assembler_parity.py` enforces).
            media_join=media_join,
            # Same reason, same instance, for DEC-195's resolver.
            # `<computer>`: `None`, the assembler's reason above.
            computer=None,
            # `<documents>`: `None`, the assembler's reason above.
            documents=None,
            # `<memos>`: `None`, the assembler's reason above.
            memos=None,
            quote_window=quote_window,
            doctrine_store=doctrine_store, metrics=metrics,
            runner=runner, adapters=adapters,
            registry=tool_registry,
            canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
            ai_peer=cfg.memory.ai_peer,
            context_window=cfg.store.context_window,
            limits=limits, defer_tools=cfg.llm.defer_tools,
            # `[heartbeat]`, not `[llm]` — the same choice production makes;
            # nobody is waiting on a fire turn.
            timeout_seconds=cfg.heartbeat.timeout_seconds,
            clock=lambda: datetime.now(UTC),
            rosters={adapter.platform: roster_source} if roster_source else {},
            preview_source=None,
            llm_state=None,
            tool_call_log_store=None,
            # `None` is the honest inert default — the offline harness has no database —
            # but the NAME has to be here: `tests/architecture/test_assembler_parity.py`
            # requires both `FirePath` call sites to pass the same collaborators, because
            # a name on only one side means an eval measures a turn production never
            # builds.
            reasoning_strip_store=None,
            # Lineage: the same value `build_assembler` above builds for the tag prompt,
            # from the same policy and identity, so an eval's fire prompt is filtered as
            # production's is.
            lineage=lineage_policy(cfg, policy=permissive_policy(), persons=persons),
            event_registry={
                "reminder": reminder_occasion,
                # The proactive act turn, registered as production registers it.
                "proactive": proactive_occasion,
            },
            standing=standing,
            standing_store_timeout_seconds=cfg.standing.store_timeout_seconds,
            # The participation turn's memory reads. ONE knob set, read at the composition
            # root exactly as the tag assembler reads `tagger_card_conclusions`: two
            # readers each carrying their own idea of how much memory a prompt may hold
            # would let the fire path render none at all. Not gated on
            # `[participation] enabled`: every read is gated on
            # `occasion.kind == "participation"` in `_read_memory`, which is a condition
            # no other occasion can meet, and a second gate here would be a place for the
            # two to disagree.
            people_in_window_conclusions=cfg.memory.people_in_window_conclusions,
            people_in_window_speakers=cfg.memory.people_in_window_speakers,
            memory_read_timeout_seconds=cfg.memory.read_timeout_seconds,
            # The shared decision log. `None` on purpose, and the keyword is written out
            # rather than defaulted so the parity gate can see the choice: an eval drives
            # ONE turn against a scripted world, so there is no prior loop to have decided
            # anything and a wired log would render an empty block on every case.
            # Production passes a real recorder; both sides name the collaborator, which
            # is what that gate is for.
            decisions=None,
            # `<presence>`: the SAME reader the assembler above holds.
            presence=presence_reader,
            # The SAME tier stores the assembler above was handed: the
            # participation determination's prompt is supposed to carry
            # whatever standing/notes the room has — an eval whose
            # FirePath read emptier tiers than its tag path would grade the
            # knowledge-cut cases against a prompt production never builds.
            notes=notes,
            # The culture head, named here because `test_assembler_parity` pins this
            # call's keyword NAMES to the composition root's: a collaborator production
            # passes and the harness does not means every participation eval measures a
            # prompt production never builds.
            #
            # `None` is the right value here rather than a gap: the rebuilt eval subject
            # (`nora.subject`) reconstructs a room's real culture at a real instant
            # (`evals/corpora/participation.yaml`), so this seam does not supply one,
            # and a case with no account should see no block.
            culture=None,
            lessons=None,
            policy=permissive_policy(),
            participation_runner=participation_runner,
            # The proactive act turn's runner: its OWN, as the composition root builds
            # it (a separate runner at `[proactive] reasoning_effort`). The act suite
            # drives `FirePath.run` through
            # `tests/offline_eval_support.run_proactive_act_case` rather than this seam,
            # so this is parity with production's kwarg names.
            proactive_runner=proactive_runner,
            # The shadow mirror, mirrored from the composition root's gating (the parity
            # test pins these kwarg NAMES to production's): an offline/eval run can review
            # determinations on the mirror exactly as a deployment would. The eval suites
            # leave `[participation] shadow` off, so this is inert there by default.
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
            max_window_messages=cfg.participation.max_window_messages,
            cooldown_seconds=cfg.participation.cooldown_minutes * 60,
            # `None` by DEFAULT, deliberately. `gate=None` admits every closed
            # window, which is what an eval of the TURN needs: a suite that had
            # to get past a live gate call first would be measuring the gate's
            # verdict and the turn's behaviour in one number, and a flaky gate
            # would read as a flaky turn. The gate has its own cases.
            #
            # `participation_gate` is the opt-in for the other kind of case: a
            # JOURNEY, where the gate's admit is one stage of the chain under
            # test and a trigger that admits everything would make that stage
            # vacuous. Passed a REAL `RelevanceGate` over a scripted model
            # client, the admit is a decision the case can assert on. Same
            # shape (and same reason) as `debrief_wiring`/`tag_trace_store`
            # above: built by nobody here, `None` unless a caller wants the
            # stage, so no existing eval run changes.
            gate=participation_gate,
            # `time.time`, matching the production default: the trigger's cursors are
            # wall-clock instants, and an offline wiring on a different clock would
            # measure a different thing from the one it exists to mirror.
            clock=participation_clock or time.time,
        )
    dispatcher = Dispatcher(
        ingest=ingest, engagement=engagement, background=background,
        participation=participation_trigger,
    )
    return dispatcher, engagement, background, participation_trigger
