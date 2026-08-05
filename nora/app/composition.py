"""The ONE place that reads Settings. Everything else receives dependencies.

A stage never sees Config; it receives an AuditStore, a MemoryStore, a Persons.
That is what makes stages testable in isolation and what stops the god object
from reforming. Enforced by import-linter (DEC-12).

`build_dependencies` is now the whole composition root: it owns the ordered,
fatal startup validation `nora.__main__.build_app` used to run inline
(`StartupError`, one `except` clause per fallible step, each message
unchanged from before this move) and builds the full object graph —
`audit`/`memory`/`persons`/`metrics` from before this phase, plus the
`EngagePolicy`, the `ContextAssembler`, both pipelines, the `Dispatcher` that
glues them, and the runtime pieces (`BackgroundTasks`, `Debounce`).
`nora.__main__.build_app` is left with exactly three steps: load config,
await this function, construct `App`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from nora.agent import factory
from nora.agent.registry import ToolRegistry
from nora.config import Config
from nora.domain.ports import (
    AuditStore,
    ContextAssembler,
    EngagePolicy,
    MemoryStore,
    PlatformAdapter,
)
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.infra.honcho import HonchoClient
from nora.infra.sql import SqlAuditStore, create_sql_engine, run_migrations
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import CompositePolicy, MentionPolicy, TriggerTextPolicy
from nora.obs.health import LLMState
from nora.obs.metrics import Metrics
from nora.persona.loader import Persona, PersonaError, PersonaStore
from nora.persons import Persons, PersonsError, load_persons
from nora.platform.registry import AdapterRegistry, AdapterRegistryError
from nora.platform.signal.adapter import SignalAdapter
from nora.policy import PolicyError, load_policy
from nora.tag import Debounce

from .agent_runner import LiveAgentRunner
from .dispatch import Dispatcher
from .runtime import BackgroundTasks

log = logging.getLogger("nora.app.composition")


class StartupError(Exception):
    """A fatal startup validation failure — refuse to start, with a readable
    message. Each site below wraps one operator-diagnosable condition; the
    messages are unchanged from `nora.__main__.build_app`'s pre-phase-3 shape,
    only relocated here (`migration error` was added deliberately in Phase 2
    — see `design/09-decision-index.md`)."""


@dataclass(frozen=True)
class Dependencies:
    """The wired collaborators the app receives instead of `Config`.

    Deliberately **no** `config`/`settings` field. A stage that can reach the
    whole config is a stage that eventually will — that is precisely how the
    229-line `Handler` god object grew. Add a named collaborator here instead
    of widening this type's reach.

    `audit`, `memory`, `policy`, `assembler` are annotated with their ports
    (`AuditStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`), not
    their concrete classes — all four live inside mypy's strict override
    (`nora.app.*`), so each is a permanent, statically-checked proof that its
    concrete class conforms to the port, not just a one-time `isinstance`
    name-check. `adapter` stays concrete (`SignalAdapter`, not
    `PlatformAdapter`) even though the platforms phase closed every gap
    between them (`tests/contract/test_port_conformance.py`) — `nora.app`
    still only ever builds one concrete adapter type, so there is nothing yet
    for the port annotation to buy here; `nora.platform.registry.
    AdapterRegistry` is where a second adapter type would actually need the
    port. `persons`, `metrics`, `persona`, `persona_store`, `debounce`,
    `background`, `llm_state`, `ingest`, `engagement`, `dispatcher` stay
    concrete too: none has a port at all.

    `client` (DEC-13): the process-wide `AsyncOpenAI` instance, constructed
    exactly once by `build_dependencies` below. It used to be a module-level
    singleton in `nora.agent.factory` (`_CLIENT`, memoized and reset by a
    test-only `close_client()`); it is now an ordinary collaborator like
    everything else here, and `nora.__main__.App.shutdown` closes it
    directly instead of calling back into `nora.agent.factory`.
    """

    audit: AuditStore
    memory: MemoryStore
    persons: Persons
    metrics: Metrics
    policy: EngagePolicy
    assembler: ContextAssembler
    ingest: IngestPipeline
    engagement: EngagementPipeline
    dispatcher: Dispatcher
    adapter: SignalAdapter
    account: str
    persona: Persona
    persona_store: PersonaStore
    debounce: Debounce
    background: BackgroundTasks
    llm_state: LLMState
    client: AsyncOpenAI


async def build_dependencies(cfg: Config) -> Dependencies:
    """Build the object graph from `cfg`.

    This function is the only place in the codebase allowed to know `Config`'s
    shape (besides `nora.__main__` itself, which only ever passes `cfg`
    straight through here). Everything downstream takes a `Dependencies`.
    """
    try:
        persons = load_persons(cfg.identity_file)
    except PersonsError as exc:
        raise StartupError(f"person mapping error: {exc}") from exc

    try:
        allowlist = load_policy(cfg.policy_file)
    except PolicyError as exc:
        raise StartupError(f"policy error: {exc}") from exc
    if allowlist.dm_persons:
        log.warning(
            "policy.toml lists %d DM principal(s), but the Signal parser still "
            "drops every non-group message — this section does nothing yet",
            len(allowlist.dm_persons),
        )

    try:
        run_migrations(cfg.store.db_path)
    except Exception as exc:
        raise StartupError(f"migration error: {exc}") from exc
    metrics = Metrics()
    audit = SqlAuditStore(
        create_sql_engine(cfg.store.db_path),
        max_per_conversation=cfg.store.max_per_conversation,
        metrics=metrics,
    )

    try:
        registry = await AdapterRegistry.from_config(cfg.platforms, metrics=metrics)
    except AdapterRegistryError as exc:
        raise StartupError(str(exc)) from exc
    if "signal" not in registry:
        # Only one platform exists today, and the rest of this function (the
        # engagement pipeline, delivery) has no code path that runs without an
        # adapter — a disabled Signal with nothing else enabled is not a valid
        # startup state yet, even though AdapterRegistry itself tolerates zero
        # enabled platforms.
        raise StartupError("no platform adapter is enabled (platforms.signal.enabled = false)")
    adapter = registry.get("signal")
    # Static conformance proof (DEC-12): `isinstance` against a `runtime_checkable`
    # `Protocol` only checks method/attribute NAMES, not signatures
    # (tests/contract/test_port_conformance.py::
    # test_runtime_checkable_protocols_only_check_method_names` demonstrates the
    # false-confidence trap directly) — this assignment is what mypy actually
    # checks SHAPES against, inside its strict `nora.app` override. If
    # `SignalAdapter` ever drifted from `PlatformAdapter`'s real signatures, this
    # line — not the isinstance test — is what would catch it.
    _adapter_satisfies_platform_adapter: PlatformAdapter = adapter
    account = registry.identity_for("signal").primary

    persona_store = PersonaStore(
        cfg.persona.dir, strict=cfg.persona.strict, reload=cfg.persona.reload,
        canary=cfg.persona.canary, display_name=cfg.persona.display_name,
        trigger=cfg.engagement.trigger.text, metrics=metrics,
    )
    try:
        persona = persona_store.get(applies_to="tag")
    except PersonaError as exc:
        raise StartupError(f"persona error: {exc}") from exc

    memory = HonchoClient(cfg.memory.base_url, cfg.memory.workspace_id)
    await memory.ensure_workspace()          # best-effort, never fatal — no try/except

    llm_state = LLMState()
    background = BackgroundTasks()
    debounce = Debounce(cfg.engagement.trigger.min_interval_seconds)

    # First match wins, deterministic text first. The mention policy uses the
    # same alias set R-12 uses to refuse self-tags — an @-mention arrives as
    # U+FFFC in the body plus a `mentions` array, so a substring trigger can
    # never see it, and tagging her the obvious way used to produce ingest,
    # attribution and silence.
    policy = CompositePolicy((
        TriggerTextPolicy(cfg.engagement.trigger.text),
        MentionPolicy(registry.identity_for("signal").aliases),
    ))
    assembler = TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
    )

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        # IngestPipeline's `spawn` is `Callable[[Coroutine], None]` — it never
        # needs the Task back. `BackgroundTasks.spawn` returns one (callers that
        # DO want it, like Dispatcher, call it directly), so this drops the
        # return value rather than widening the pipeline's port to match.
        background.spawn(coro)

    ingest = IngestPipeline(
        audit=audit, memory=memory, persons=persons,
        policy=policy, allowlist=allowlist, metrics=metrics, spawn=_spawn,
    )

    tier = cfg.llm.tiers.flash
    api_key = os.environ.get(cfg.llm.api_key_env, "")  # presence already checked by config.load
    client = factory.create_client(base_url=cfg.llm.base_url, api_key=api_key)
    model = factory.create_model_provider(
        model=tier.model, max_tokens=tier.max_tokens, temperature=tier.temperature,
        client=client,
    )
    runner = LiveAgentRunner(model=model)
    limits = factory.build_limits(
        max_tokens=tier.max_tokens, turns=tier.limits.turns, total_tokens=tier.limits.total_tokens,
    )

    tool_registry = ToolRegistry()
    engagement = EngagementPipeline(
        adapter=adapter, audit=audit, memory=memory, persons=persons,
        persona_store=persona_store, context_assembler=assembler, runner=runner,
        tool_registry=tool_registry,
        debounce=debounce, canary=cfg.persona.canary, display_name=cfg.persona.display_name,
        nora_sender_id=cfg.platforms.signal.nora_sender_id,
        context_window=cfg.store.context_window, timeout_seconds=tier.timeout_seconds,
        limits=limits, metrics=metrics, spawn=background.spawn, llm_state=llm_state,
    )
    dispatcher = Dispatcher(ingest=ingest, engagement=engagement, background=background)

    return Dependencies(
        audit=audit, memory=memory, persons=persons, metrics=metrics,
        policy=policy, assembler=assembler, ingest=ingest, engagement=engagement,
        dispatcher=dispatcher, adapter=adapter, account=account, persona=persona,
        persona_store=persona_store, debounce=debounce, background=background,
        llm_state=llm_state, client=client,
    )
