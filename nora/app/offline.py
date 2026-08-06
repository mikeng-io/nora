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
`MemoryStore`, `AgentRunner`, or `AuditStore` is constructed here. The caller
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
from nora.domain.ports import AuditStore, MemoryStore, RosterSource
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
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
    audit: AuditStore,
    memory: MemoryStore,
    persons: Persons,
    doctrine_store: DoctrineStore,
    runner: Any,
    metrics: Metrics | None = None,
    roster_source: RosterSource | None = None,
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
    tool_registry = ToolRegistry()
    engagement = EngagementPipeline(
        adapter=adapter, audit=audit, memory=memory, persons=persons,
        doctrine_store=doctrine_store, context_assembler=assembler, runner=runner,
        tool_registry=tool_registry,
        debounce=debounce, canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        nora_sender_id=cfg.platforms.signal.nora_sender_id,
        context_window=cfg.store.context_window, timeout_seconds=tier.timeout_seconds,
        limits=limits, metrics=metrics, spawn=background.spawn, llm_state=None,
        # Defaults to None: an offline harness has no signal-cli to ask, and
        # `nora.agent.room.Room` reports an absent source as UNAVAILABLE rather
        # than as an empty room — so the three room-scoped tools degrade
        # honestly here instead of claiming the group is empty. A harness that
        # wants to exercise them passes a `RosterSource`.
        roster_source=roster_source,
        queue_max=cfg.engagement.queue.max_depth,
    )
    dispatcher = Dispatcher(ingest=ingest, engagement=engagement, background=background)
    return dispatcher, engagement, background
