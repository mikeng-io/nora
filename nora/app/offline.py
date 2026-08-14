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

from collections.abc import Coroutine
from typing import Any

from nora.agent import factory
from nora.agent.registry import ToolRegistry
from nora.config import Config
from nora.doctrine.loader import DoctrineStore
from nora.domain.ports import MemoryStore, RecordStore, RosterSource
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.slots import ConversationSlots
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.tag import Debounce

from .dispatch import Dispatcher
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
) -> tuple[Dispatcher, EngagementPipeline, BackgroundTasks]:
    """Wire ingest+engagement+dispatcher from already-built collaborators.

    Mirrors `build_dependencies`'s own pipeline-wiring tail exactly (same
    stages, same scalar fields read off `cfg`) but takes `adapter`/`memory`/
    `runner` as parameters instead of constructing the real, networked ones —
    the split `build_dependencies` doesn't need since production always wants
    the real collaborators.
    """
    metrics = metrics or Metrics()
    background = BackgroundTasks()
    debounce = Debounce(cfg.engagement.trigger.min_interval_seconds)
    policy = TriggerTextPolicy(cfg.engagement.trigger.text)
    assembler = TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        ai_peer=cfg.memory.ai_peer,
        self_card_conclusions=cfg.memory.self_card_conclusions,
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
    # Default: a bare registry (every tool inert/`unavailable`). The eval's live
    # routing gate passes one wired with canned backends (F4) so `weather`/
    # `web_search`/`place_info`/… answer `ok`, removing the fallback confound.
    # `budget_overrides` threaded, so an offline replay runs under the SAME
    # ceilings production does. Without it, `[llm.budgets]` would silently make
    # replay a different agent from the one deployed — which is the whole thing
    # a replay path exists to rule out.
    tool_registry = tool_registry if tool_registry is not None else ToolRegistry(
        budget_overrides=cfg.llm.budgets)
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
        roster_source=roster_source,
        slots=ConversationSlots(
            maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn,
        ),
    )
    dispatcher = Dispatcher(ingest=ingest, engagement=engagement, background=background)
    return dispatcher, engagement, background
