"""The ONE place that reads Settings. Everything else receives dependencies.

A stage never sees Config; it receives a RecordStore, a MemoryStore, a Persons.
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
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from nora.agent import factory
from nora.agent.mcp import McpMount, build_mcp_tools
from nora.agent.registry import ToolRegistry
from nora.agent.relay import Relay
from nora.agent.room import Room
from nora.agent.whereis import Circle, check_disclosure_invariant, circles_from_persons
from nora.config import Config
from nora.doctrine.loader import Doctrine, DoctrineError, DoctrineStore
from nora.domain.compute import ComputeJobStore
from nora.domain.ports import (
    BlobStore,
    ComputeBackend,
    ContextAssembler,
    EngagePolicy,
    MediaSource,
    MemoryStore,
    PlatformAdapter,
    RecordStore,
    ReverseGeocoder,
    RosterSource,
)
from nora.domain.reminders import ReminderCaps, ReminderStore
from nora.domain.tool_call_log import ToolCallLogStore
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.slots import ConversationSlots
from nora.infra.blobs import FilesystemBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.compute.docker_backend import LocalDockerBackend
from nora.infra.honcho import HonchoClient, WorkspaceRouter
from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations
from nora.infra.sql.announcements import SqlAnnouncementLog
from nora.infra.sql.compute_jobs import SqlComputeJobStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.infra.version import OK as VERSION_OK
from nora.infra.version import VersionManifest
from nora.infra.vision import VisionClient, derive_vision_timeout
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import (
    CompositePolicy,
    MentionPolicy,
    ReplyToNoraPolicy,
    TriggerTextPolicy,
)
from nora.obs.health import LLMState
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError, load_persons
from nora.platform.registry import AdapterRegistry, AdapterRegistryError
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.media import SignalMediaSource
from nora.platform.signal.roster import SignalRoster
from nora.policy import Policy, PolicyError, load_policy
from nora.tag import Debounce

from .agent_runner import LiveAgentRunner
from .announce import AnnouncementTarget, VersionAnnouncer
from .compute_poller import ComputeJobPoller
from .compute_poller import recover as compute_poller_recover
from .dispatch import Dispatcher
from .fire import FirePath, lost_job_occasion, relay_occasion
from .heartbeat import HeartbeatLoop, publish_idle_gauge, recover
from .runtime import BackgroundTasks

log = logging.getLogger("nora.app.composition")


class StartupError(Exception):
    """A fatal startup validation failure — refuse to start, with a readable
    message. Each site below wraps one operator-diagnosable condition; the
    messages are unchanged from `nora.__main__.build_app`'s pre-phase-3 shape,
    only relocated here (`migration error` was added deliberately in Phase 2
    — see `design/09-decision-index.md`)."""


# The one subdirectory name a compute job's workspace lives under, appended to
# BOTH the container-side root (`store.data_dir`) and the host-side root, so the
# two paths handed to `LocalDockerBackend` cannot name different directories.
COMPUTE_WORKSPACE_DIRNAME = "compute-workspaces"


def _compute_workspace_host_root(cfg: Config) -> Path:
    """The host-side path of whatever is bind-mounted at `store.data_dir`.

    READ FROM THE ENVIRONMENT, and it cannot be anything else: `store.data_dir`
    is resolved against the config file's own directory, so in the container it
    is `/app/data` — a path the HOST Docker daemon, which resolves every
    bind-mount source on the host filesystem, has never heard of. Handing it
    that path does not error; the daemon creates an empty directory at
    `/app/data/...` on the host and the job's sandbox finds no `task.txt`,
    writes its result where nobody reads it, and reports nothing (R-5's exact
    shape, at job time, in front of the group).

    So this fails CLOSED at boot instead: unset, or set to a relative path
    nothing can resolve, refuses to start. `docker-compose.yml` populates the
    variable from the same expression it uses for the mount source itself.
    """
    raw = os.environ.get(cfg.compute.data_host_root_env, "").strip()
    if not raw:
        raise StartupError(
            f"[compute] enabled = true but environment variable "
            f"{cfg.compute.data_host_root_env} is not set — it must name the "
            f"HOST-side path bind-mounted at {cfg.store.data_dir} (docker "
            "compose sets it from the same expression as that mount), because "
            "a sandbox workspace is bind-mounted by the host Docker daemon and "
            "an in-container path silently resolves to an empty host directory; "
            "refusing to start"
        )
    root = Path(raw)
    if not root.is_absolute():
        raise StartupError(
            f"environment variable {cfg.compute.data_host_root_env} is "
            f"{raw!r}, a relative path — the host Docker daemon resolves a "
            "bind-mount source against nothing this process knows, so it must "
            "be absolute; refusing to start"
        )
    return root


def _check_compute_secrets(
    *, drop_token: str, maps_key: str, model_api_key: str, cfg: Config,
) -> None:
    """Every credential a compute job cannot run without, checked at BOOT.

    The `policy.toml` discipline (R-5: refuse to start rather than run in a
    state that looks like working) applied to `[compute]`. Each of these is
    read with a `""` default, so without this check an unset one starts the
    daemon cleanly and then fails EVERY job — `LocalDockerBackend` mints its
    Drop credential with an empty bearer token, or hands Smith an empty model
    key — minutes later, in front of the group, as "it failed" with an error
    string nobody can act on. A missing secret is an operator's problem and
    belongs in the operator's terminal, not in the group chat.

    All of them are named in ONE message rather than one-at-a-time, so an
    operator setting up compute fixes the whole set in one pass.
    """
    missing = [
        name for name, value in (
            (cfg.shortener.token_env, drop_token),
            (cfg.route.api_key_env, maps_key),
            (cfg.llm.api_key_env, model_api_key),
        ) if not value.strip()
    ]
    if missing:
        raise StartupError(
            "[compute] enabled = true but these environment variables are "
            f"unset or empty: {', '.join(missing)} — a compute job needs the "
            "drop credential it mints its per-job token against, the Google "
            "Maps key its map-rendering skill uses, and the model key Smith "
            "itself runs on. Starting without them would fail every job at "
            "run time instead of here; refusing to start"
        )


@dataclass(frozen=True)
class Dependencies:
    """The wired collaborators the app receives instead of `Config`.

    Deliberately **no** `config`/`settings` field. A stage that can reach the
    whole config is a stage that eventually will — that is precisely how the
    229-line `Handler` god object grew. Add a named collaborator here instead
    of widening this type's reach.

    `audit`, `memory`, `policy`, `assembler` are annotated with their ports
    (`RecordStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`), not
    their concrete classes — all four live inside mypy's strict override
    (`nora.app.*`), so each is a permanent, statically-checked proof that its
    concrete class conforms to the port, not just a one-time `isinstance`
    name-check. `adapter` stays concrete (`SignalAdapter`, not
    `PlatformAdapter`) even though the platforms phase closed every gap
    between them (`tests/contract/test_port_conformance.py`) — but no longer
    because Signal is the only adapter this process builds. It isn't, as of
    `design/discord-integration/`. It stays concrete because the few things
    still reading it are Signal-specific BY CONSTRUCTION and should say so out
    loud rather than pretend to be platform-neutral: `nora_sender_id`, the
    signal-cli roster, and the `sse` block `/health` pins for the watchdog.
    Everything that must work across platforms reads `registry` instead
    (DEC-102) — so widening this field would hide the split rather than close
    it. `persons`, `metrics`, `doctrine`, `doctrine_store`, `debounce`,
    `background`, `llm_state`, `ingest`, `engagement`, `dispatcher` stay
    concrete too: none has a port at all.

    `client` (DEC-13): the process-wide `AsyncOpenAI` instance, constructed
    exactly once by `build_dependencies` below. It used to be a module-level
    singleton in `nora.agent.factory` (`_CLIENT`, memoized and reset by a
    test-only `close_client()`); it is now an ordinary collaborator like
    everything else here, and `nora.__main__.App.shutdown` closes it
    directly instead of calling back into `nora.agent.factory`.
    """

    audit: RecordStore
    memory: MemoryStore
    persons: Persons
    metrics: Metrics
    policy: EngagePolicy
    assembler: ContextAssembler
    ingest: IngestPipeline
    engagement: EngagementPipeline
    dispatcher: Dispatcher
    adapter: SignalAdapter
    # EVERY built adapter, keyed by platform. `adapter` above stays the concrete
    # Signal one because a handful of call sites are still Signal-specific by
    # construction (`nora_sender_id`, the signal-cli roster); this is what the
    # paths that must not be — starting each listener, routing a reply back to
    # the platform it came from, reporting health per platform — read instead.
    registry: AdapterRegistry
    account: str
    doctrine: Doctrine
    doctrine_store: DoctrineStore
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

    # The Nora aiPeer id is RESERVED — no human may hold it (council 2026-08-07,
    # Honcho v2 corpus-safety). If an operator gave a person the id `nora`, that
    # human's words would feed under the aiPeer and merge IRREVERSIBLY into
    # Nora's self-model card (Honcho keeps conclusions, not text). Fail closed at
    # startup rather than pollute the corpus.
    if cfg.memory.ai_peer in persons.by_person:
        raise StartupError(
            f"identity.toml maps a person to the id '{cfg.memory.ai_peer}', which "
            "is reserved for Nora's own aiPeer (memory.ai_peer) — a human under "
            "that id would corrupt her self-model card. Rename the person or the "
            "aiPeer."
        )

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

    database_url = os.environ.get(cfg.store.database_url_env, "")
    if not database_url:
        raise StartupError(
            f"environment variable {cfg.store.database_url_env} is not set — "
            "the PostgreSQL URL (postgresql+asyncpg://…) must be injected by the "
            "host (DEC-8); refusing to start"
        )
    try:
        run_migrations(database_url)
    except Exception as exc:
        raise StartupError(f"migration error: {exc}") from exc
    metrics = Metrics()
    # ONE engine (one pool) shared by the record store and the presence reader —
    # the shape `PresenceStore`'s own docstring asks for. `audit.aclose()` (in
    # `nora.__main__.App.shutdown`) disposes it for both; `PresenceStore.aclose`
    # is deliberately NOT also called on this shared engine.
    engine = create_sql_engine(database_url)
    audit = SqlRecordStore(
        engine,
        max_per_conversation=cfg.store.max_per_conversation,
        metrics=metrics,
    )
    # The reminders store, REUSING `engine` for the reason above: a second
    # `create_sql_engine` forks the pool and its `aclose()` would dispose the
    # engine the record store and the presence reader are still using.
    #
    # Built HERE, beside the other stores, rather than down beside the heartbeat
    # loop it feeds — because it has TWO consumers and only one of them is
    # gated. `ToolRegistry` (below) needs it whether or not `[heartbeat] enabled`
    # is true: the model may set, list and cancel reminders on a deployment with
    # the tick switched off, and `remind` answering `unavailable` there would be
    # a wiring accident that no test catches (every test builds its own registry
    # with the fake). The tick is the only part behind the flag.
    reminders: ReminderStore = SqlReminderStore(engine)
    # The compute-job outbox, REUSING `engine` for the same reason `reminders`
    # does above. Built HERE, unconditionally, beside the other stores — the
    # exact `reminders` argument applies again: `ToolRegistry` (below) needs
    # `compute_jobs` for `make_artifact`'s slow path whether or not
    # `[compute] enabled` is true, and boot recovery a few hundred lines down
    # (beside `reminders`' own unconditional sweep) must find a store to sweep
    # even when the poller itself is off. Only the poller's tick loop
    # (`compute_backend`, further down) is behind the flag.
    compute_jobs: ComputeJobStore = SqlComputeJobStore(engine)
    # Second consumer wired here (EngagementPipeline/FirePath, via
    # deliver.build); the THIRD — ToolRegistry(...) below, for
    # recall_lookups — is Task 7's own wiring.
    tool_call_log_store: ToolCallLogStore = SqlToolCallLogStore(engine)

    # Resolved here, not inside AdapterRegistry — the same convention every
    # other `*_env` secret (the LLM key, the route key) follows: composition.py
    # reads the environment once and hands the already-resolved value to
    # whatever constructs the client that needs it (design/discord-integration/).
    discord_token = os.environ.get(cfg.platforms.discord.token_env, "")
    try:
        registry = await AdapterRegistry.from_config(
            cfg.platforms, metrics=metrics, discord_token=discord_token,
        )
    except AdapterRegistryError as exc:
        raise StartupError(str(exc)) from exc
    if "signal" not in registry:
        # Only one platform exists today, and the rest of this function (the
        # engagement pipeline, delivery) has no code path that runs without an
        # adapter — a disabled Signal with nothing else enabled is not a valid
        # startup state yet, even though AdapterRegistry itself tolerates zero
        # enabled platforms.
        raise StartupError("no platform adapter is enabled (platforms.signal.enabled = false)")
    signal_adapter = registry.get("signal")
    # `AdapterRegistry.get` returns the port type now that a second concrete
    # adapter exists (design/discord-integration/) — narrowed back to the
    # concrete class here because `Dependencies.adapter` stays SignalAdapter
    # (nora.app still only ever builds Signal-specific tool wiring around it;
    # see that field's own docstring). An internal invariant violation, not a
    # config error, so it fails loudly rather than via a silent type lie.
    if not isinstance(signal_adapter, SignalAdapter):
        raise StartupError("internal error: registry's 'signal' entry is not a SignalAdapter")
    adapter = signal_adapter
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

    doctrine_store = DoctrineStore(
        cfg.doctrine.dir, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text,
        language_hint=cfg.doctrine.language_hint,
        tool_names=ToolRegistry().tool_names(),
        capability=ToolRegistry().capability(),
        metrics=metrics,
    )
    try:
        doctrine = doctrine_store.get(applies_to="tag")
    except DoctrineError as exc:
        raise StartupError(f"doctrine error: {exc}") from exc

    # Two machine ids for one agent: `[agent].name` (trace lineage) and
    # `[memory].ai_peer` (the Honcho observer peer). A clone ("delia") must set
    # BOTH; if they diverge, the agent traces under one name but builds its
    # memory under another — a silent split. Warn rather than fail: a deliberate
    # divergence is rare but conceivable, and this is a config smell, not an
    # invariant (review M6, 2026-08-08).
    if cfg.agent.name != cfg.memory.ai_peer:
        log.warning(
            "agent.name (%r) != memory.ai_peer (%r): traces and Honcho memory "
            "will use different agent identities — set both to the same value "
            "unless this split is intended",
            cfg.agent.name, cfg.memory.ai_peer,
        )
    # The conversation→workspace seam (design/24 §S.4). Today a router with NO
    # overrides — every conversation resolves to the single `workspace_id`, so
    # this changes nothing — but the multi-social-unit routing (a per-group
    # workspace from identity.toml / a future workspaces.toml) lands here without
    # a client refactor.
    memory = HonchoClient(
        cfg.memory.base_url, cfg.memory.workspace_id, ai_peer=cfg.memory.ai_peer,
        workspace_router=WorkspaceRouter(cfg.memory.workspace_id),
    )
    await memory.ensure_workspace()          # best-effort, never fatal — no try/except
    # The Nora aiPeer (Honcho v2, design/24): her replies feed under it to build
    # her self-model card. Created here like the workspace — best-effort, never
    # fatal (a missing peer is auto-created on first feed anyway).
    await memory.ensure_peer(peer_id=cfg.memory.ai_peer)

    llm_state = LLMState()
    background = BackgroundTasks()
    debounce = Debounce(cfg.engagement.trigger.min_interval_seconds)

    # First match wins, deterministic text first. The mention policy uses the
    # same alias set R-12 uses to refuse self-tags — an @-mention arrives as
    # U+FFFC in the body plus a `mentions` array, so a substring trigger can
    # never see it, and tagging her the obvious way used to produce ingest,
    # attribution and silence.
    # EVERY platform's aliases, not just Signal's. These two policies match a
    # mention/quote against "is this Nora?", and they were built from the Signal
    # identity alone — so on Discord an @mention carried her SNOWFLAKE while the
    # match set held a phone number and a UUID, and the obvious way to address
    # her produced ingest, attribution and silence. That is the exact failure
    # `MentionPolicy` was written to end (see its docstring), reintroduced by a
    # second platform rather than by the parser: the Discord parser fills
    # `mentions` correctly.
    #
    # A UNION rather than a per-platform lookup: these id spaces are disjoint by
    # construction (a Signal phone is `+…`, its ACI a hyphenated UUID, a Discord
    # id a bare 17-19 digit snowflake), so no cross-platform false positive is
    # reachable, and the alternative — threading `message.platform` into every
    # policy — buys nothing here. R-12's self-tag check is the one that must
    # stay strictly per-platform, and it does: it reads the resolved identity
    # for the tag's own platform (DEC-102).
    #
    # Every id form Nora answers to, on every enabled platform — see
    # `AdapterRegistry.self_aliases`. Built from Signal's identity ALONE until
    # 2026-08-14, so on Discord an @mention carried her snowflake while the
    # match set held a phone number and a UUID: the obvious way to address her
    # produced ingest, attribution and silence, which is the exact failure
    # `MentionPolicy` was written to end, reintroduced by a second platform.
    self_aliases = registry.self_aliases()
    policy = CompositePolicy((
        TriggerTextPolicy(cfg.engagement.trigger.text),
        MentionPolicy(self_aliases),
        ReplyToNoraPolicy(self_aliases),
    ))
    assembler = TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        ai_peer=cfg.memory.ai_peer,
        self_card_conclusions=cfg.memory.self_card_conclusions,
        tagger_card_conclusions=cfg.memory.tagger_card_conclusions,
    )

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        # IngestPipeline's `spawn` is `Callable[[Coroutine], None]` — it never
        # needs the Task back. `BackgroundTasks.spawn` returns one (callers that
        # DO want it, like Dispatcher, call it directly), so this drops the
        # return value rather than widening the pipeline's port to match.
        background.spawn(coro)

    media_source, blobs = _build_media_custody(cfg)
    ingest = IngestPipeline(
        audit=audit, memory=memory, persons=persons,
        policy=policy, allowlist=allowlist, metrics=metrics, spawn=_spawn,
        media_source=media_source, blobs=blobs,
    )

    tier = cfg.llm
    api_key = os.environ.get(cfg.llm.api_key_env, "")  # presence already checked by config.load
    client = factory.create_client(base_url=cfg.llm.base_url, api_key=api_key)
    # One CLIENT (audit T2, DEC-13), MANY MODELS — one per tag, built by this
    # source. The model carries `tool_choice`, which `ReplyGuard` flips
    # mid-turn from `required` to `auto` (design/16 §2), so it is per-turn
    # mutable state and two concurrent conversations must not share it.
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=tier.model, max_tokens=tier.max_tokens, temperature=tier.temperature,
            reasoning_effort=tier.reasoning_effort,
        ),
    )
    runner = LiveAgentRunner(
        models=models,
        # Derived from the same `timeout_seconds` the invoke stage enforces, so
        # a throttle can never be misreported as a timeout (see
        # `factory.build_retry_policy`).
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        # The on-demand skill layer, ON (DEC-50, activating DEC-48). Skills sit
        # beside doctrine under `nora/` — `cfg.doctrine.dir` is the resolved
        # absolute doctrine path, so its sibling `skills/` is the operator skill
        # directory (`nora/skills`). It is baked into the image (Dockerfile
        # `COPY nora/`), so — unlike the mtime-hot-reloaded doctrine mount — it
        # needs no bind mount: skills load once per tag, never re-read from the
        # host. `AgentSkills` is built per tag inside `factory.create_agent`.
        skills_dir=cfg.doctrine.dir.parent / "skills",
        agent_name=cfg.agent.name,
    )
    # `output_tokens` comes from `[llm.limits]`, NOT from `tier.max_tokens`.
    # The two were the same value until 2026-08-06, which made the whole
    # loop's output budget equal to one call's ceiling — see
    # `factory.build_limits` for the measurement.
    limits = factory.build_limits(
        output_tokens=tier.limits.output_tokens,
        turns=tier.limits.turns,
        total_tokens=tier.limits.total_tokens,
    )

    # `route` (Google, DEC-46) is wired ONLY when [route] enabled AND its key
    # env var holds a value — the same enabled-AND-key gate as media custody.
    # Off (or keyless) leaves the registry without a RouteClient, so `route`
    # answers `unavailable` and never reaches Google (a legitimate three-state,
    # not a leak). R-28 screens person names before any call regardless.
    route_client = None
    if cfg.route.enabled:
        route_key = os.environ.get(cfg.route.api_key_env, "")
        if route_key:
            from nora.infra.routes.client import RouteClient
            route_client = RouteClient(api_key=route_key)
        else:
            log.warning(
                "route enabled but %s is unset — routing stays unavailable",
                cfg.route.api_key_env,
            )
    # `media_fetch` (DEC-56) reads a stored blob back through MiMo. Its two
    # collaborators are wired together: the blob store (already built above,
    # `None` while `[media] enabled=false` or unkeyed) and a `VisionClient` over
    # the process-wide OmniRoute client. The vision call gets its OWN timeout,
    # DERIVED from the same `timeout_seconds` the invoke stage enforces so it is
    # provably shorter than the loop's `asyncio.wait_for` — a hung MiMo becomes
    # the tool's `unavailable`, never the loop's silent `invocation_timeout`
    # (the way `factory.build_retry_policy` derives its backoff). The client is
    # always present (the LLM key is required to start); `media_fetch` still
    # answers `unavailable` whenever `blobs` is None, the inert-by-config shape.
    vision_client = VisionClient(
        client=client,
        model=cfg.media.vision_model,
        max_tokens=cfg.media.vision_max_tokens,
        reasoning_effort=cfg.media.vision_reasoning_effort,
        max_bytes=cfg.media.vision_max_bytes,
        timeout_seconds=derive_vision_timeout(
            loop_timeout_seconds=tier.timeout_seconds,
            configured=cfg.media.vision_timeout_seconds,
            # The tool's OWN budget (DEC-10, one number): `budget` calls share the
            # loop, so the per-call ceiling divides by it — two image reads still
            # leave headroom for the text turns (both reviews 2026-08-07).
            #
            # RESOLVED, not read off the table. This said
            # `NATIVE_TOOL_BUDGETS["media_fetch"]` until `[llm.budgets]` shipped,
            # at which point it became the SECOND reader of a number the new
            # config knob could move — and `derive_vision_timeout`'s whole
            # argument is that `budget * derived < loop_timeout_seconds` "is one
            # nobody can violate if it is never written down twice". An operator
            # setting `media_fetch = 8` would have raised R-7's ceiling to 8
            # while the per-call timeout stayed derived from 4: 8 x 18 s = 144 s
            # against a 120 s loop, which is R-33's silent `invocation_timeout`
            # coming back. A throwaway registry is how this file already reads
            # `tool_names()`/`capability()` above; `budgets()` is the one
            # resolver, so this asks it.
            budget=ToolRegistry(
                budget_overrides=cfg.llm.budgets).budgets()["media_fetch"],
        ),
    )
    # `web_search` (Tavily, DEC-73) is wired ONLY when `[search] enabled` AND a
    # key is present — the `route`-keyless gate. Off (or keyless) leaves the
    # registry without a provider and `web_search` answers `unavailable` (R-5),
    # never a silent empty. The key is resolved from the environment here (DEC-8),
    # never read inside the adapter. No SearXNG/Firecrawl and no `web_fetch`.
    search_client = None
    if cfg.search.enabled:
        search_key = os.environ.get(cfg.search.api_key_env, "")
        if search_key:
            from nora.infra.search import TavilySearchProvider
            search_client = TavilySearchProvider(
                api_key=search_key,
                url=cfg.search.url,
                max_results=cfg.search.max_results,
                search_depth=cfg.search.search_depth,
                content_max_chars=cfg.search.content_max_chars,
                timeout_seconds=cfg.search.timeout_seconds,
            )
        else:
            log.warning(
                "search enabled but %s is unset — web_search stays unavailable",
                cfg.search.api_key_env,
            )
    # `weather` (DEC-70) is wired on `[weather] enabled` alone — the HK Observatory
    # provider is KEYLESS, so unlike `route`/`search` there is no key to resolve;
    # off leaves the registry without a provider and `weather` answers
    # `unavailable` (R-5). `provider` selects the adapter — "hko" today.
    weather_client = None
    if cfg.weather.enabled:
        if cfg.weather.provider == "hko":
            from nora.infra.weather import HkoWeatherProvider
            weather_client = HkoWeatherProvider(
                url=cfg.weather.url, lang=cfg.weather.lang,
                timeout_seconds=cfg.weather.timeout_seconds,
            )
        else:
            log.warning(
                "weather enabled but provider %r is unknown — weather stays unavailable",
                cfg.weather.provider,
            )
    # `whereis` (DEC-59, activating DEC-54's inert seam) is wired ONLY when
    # `[presence] enabled` AND identity.toml yields at least one circle — the
    # enabled-AND-configured gate `route` and media custody use. Off, or
    # circle-less, leaves the registry without a reader and `whereis` answers
    # `unavailable` (R-5's honest «not configured»). The reader shares the
    # `audit` engine (one pool, one lifecycle); the COLLECTOR process, not this
    # one, owns writing/pruning `presence_fixes` — Nora only ever reads it.
    presence_store: PresenceStore | None = None
    presence_circles: tuple[Circle, ...] = ()
    if cfg.presence.enabled:
        presence_circles = circles_from_persons(persons)
        if presence_circles:
            presence_store = PresenceStore(
                engine, retention_days=cfg.presence.retention_days, metrics=metrics,
            )
        else:
            log.warning(
                "presence enabled but identity.toml yields no circle "
                "([[persons.<id>.life360]] with circle_id) — whereis stays unavailable"
            )
    # The link shortener ([shortener], 2026-08-07): enabled AND token present,
    # the `route`-keyless gate. Off (or tokenless) leaves long URLs in replies —
    # never load-bearing either way.
    shortener_client = None
    if cfg.shortener.enabled:
        shortener_token = os.environ.get(cfg.shortener.token_env, "")
        if shortener_token:
            from nora.infra.shortener import LinkShortener
            shortener_client = LinkShortener(
                base_url=cfg.shortener.base_url, token=shortener_token,
                timeout_seconds=cfg.shortener.timeout_seconds,
            )
        else:
            log.warning(
                "shortener enabled but %s is unset — links stay long",
                cfg.shortener.token_env,
            )
    # Sender-generated link previews ([preview]). Keyless, so `enabled` is the
    # whole gate — unlike the shortener above, there is no token whose absence
    # could leave it half-wired. Off is the shipped default and is not a
    # degradation: a reply carrying a link goes out with no card, exactly as
    # every deployment before this section existed. It is ONE instance for the
    # process (it holds no per-turn state), handed to the pipeline and the fire
    # path, which hand it to each turn's `OutboundComposer`.
    preview_source = None
    if cfg.preview.enabled:
        from nora.infra.preview import OpenGraphPreviewSource
        preview_source = OpenGraphPreviewSource(
            timeout_seconds=cfg.preview.timeout_seconds,
            max_bytes=cfg.preview.max_bytes,
            fetch_image=cfg.preview.image,
            image_max_bytes=cfg.preview.image_max_bytes,
        )
    # The on-demand LIVE-refresh client (DEC-67): when `[presence].refresh_url` is
    # set AND presence is wired, `where_is` asks the collector to force a fresh
    # reading before it reads the store. Unset → `where_is` stays read-only
    # (offline/eval, or an operator who leaves it off).
    refresher = None
    if cfg.presence.refresh_url and presence_store is not None:
        from nora.infra.presence_refresh import HttpLocationRefresher
        refresher = HttpLocationRefresher(
            base_url=cfg.presence.refresh_url,
            # A margin over the collector's poll deadline, not a guarantee: the
            # collector's TOTAL time is request_location + read_circle (each up to
            # the Life360 client's own timeout) + the poll, so a slow network can
            # still exceed this and Nora cuts it off. Harmless — the path is never
            # load-bearing, so a cut-off just falls back to the stored fix (review
            # 2026-08-08).
            timeout_seconds=cfg.presence.refresh_timeout_seconds + 2.0,
        )
    # The on-demand reverse-geocoder (moved here from the presence collector,
    # 2026-08-14): the collector calling this on EVERY fix, for every tracked
    # person, every cadence_seconds tick, regardless of whether anyone ever
    # asked where they were, cost ~12k billed Google Geocoding calls over 6
    # days (~$60, confirmed against Cloud Monitoring's own request-count
    # metric) for addresses nobody read. `Whereis.locate` (nora/agent/whereis.py)
    # now calls this itself, ONLY when a fix has no address and someone
    # actually asked `where_is` about it — the collector talks to Life360
    # alone, never Google. Switchable behind the SAME `ReverseGeocoder` port
    # (`[presence] geocode_provider`, "google" default or "hkgov"), same
    # config fields this used to read on the collector's side.
    geocoder: ReverseGeocoder | None = None
    if cfg.presence.geocode_provider == "hkgov":
        hkgov_token = os.environ.get(cfg.presence.hkgov_token_env, "")
        if hkgov_token and cfg.presence.hkgov_geocode_url:
            from nora.infra.geocode_hkgov import HKGovGeocoder
            geocoder = HKGovGeocoder(
                service_url=cfg.presence.hkgov_geocode_url, token=hkgov_token,
                lang_zh=cfg.presence.hkgov_lang_zh, lang_en=cfg.presence.hkgov_lang_en,
            )
    else:
        maps_key = os.environ.get(cfg.presence.geocode_api_key_env, "")
        if maps_key:
            from nora.infra.geocode import GoogleGeocoder
            geocoder = GoogleGeocoder(api_key=maps_key)
    # `LocalDockerBackend` (Nora Compute spec §2/§3), wired ONLY when
    # `[compute] enabled` — the `[heartbeat]`-shaped gate: off leaves
    # `compute_backend` None and `make_artifact` answers `unavailable` (R-5),
    # never a silent no-op. It needs `docker.from_env()` to reach the HOST
    # Docker daemon, which is why the socket is bind-mounted into this very
    # container in docker-compose.yml — a named, already-ruled-on privilege
    # widening (see that file's own comment beside the mount).
    #
    # Three collaborators are REUSED rather than re-resolved, each for the
    # reason `nora/infra/compute/docker_backend.py`'s own docstring gives:
    # `api_key`/`tier.base_url`/`tier.model` are the SAME OmniRoute triple
    # Nora's own agent is configured with (Smith needs a model credential too,
    # not a second provider story), and `cfg.shortener.base_url`/token are the
    # SAME pair `LinkShortener` above resolves for `drop`'s internal
    # listener — `LocalDockerBackend` mints its own per-job Drop token against
    # that same listener. `google_maps_api_key` reads `[route].api_key_env`
    # directly (not `route_client`'s own scoped `route_key`, which only exists
    # inside that `if` block) — the SAME env var `render_map`, the skill that
    # consumes it, already shares with the `route` tool.
    compute_backend: ComputeBackend | None = None
    if cfg.compute.enabled:
        import docker

        drop_token = os.environ.get(cfg.shortener.token_env, "")
        maps_key = os.environ.get(cfg.route.api_key_env, "")
        _check_compute_secrets(
            drop_token=drop_token, maps_key=maps_key, model_api_key=api_key, cfg=cfg,
        )
        compute_backend = LocalDockerBackend(
            docker_client=docker.from_env(),
            drop_internal_url=cfg.shortener.base_url,
            drop_master_token=drop_token,
            model_api_key=api_key,
            model_base_url=tier.base_url,
            model_name=tier.model,
            google_maps_api_key=maps_key,
            sandbox_image=cfg.compute.sandbox_image,
            job_timeout_seconds=cfg.compute.job_timeout_seconds,
            max_concurrent_jobs=cfg.compute.max_concurrent_jobs,
            mem_limit=cfg.compute.mem_limit,
            nano_cpus=cfg.compute.nano_cpus,
            # Reuses Nora's own [obs.tracing] section rather than a second
            # [compute]-scoped one -- Smith's spans land in the SAME Langfuse
            # project, under service.name="smith" (tracing.py) for filtering,
            # not a separate destination an operator would have to configure
            # twice. Only forwarded when the exporter is actually "otlp_http"
            # -- "console"/"file" have no endpoint value meaningful to a
            # process on the other side of the container boundary.
            tracing_enabled=(
                cfg.obs.tracing.enabled and cfg.obs.tracing.exporter == "otlp_http"
            ),
            tracing_endpoint=cfg.obs.tracing.endpoint,
            tracing_headers=(
                os.environ.get(cfg.obs.tracing.headers_env, "")
                if cfg.obs.tracing.headers_env else ""
            ),
            # `store.data_dir`, the SAME data-volume convention media custody
            # uses (`_build_media_custody` below: `store.data_dir / "media"`)
            # — not a second, unmounted absolute path. Bind-mounted at
            # `./data:/app/data` in docker-compose.yml, so a job's workspace
            # survives a container restart exactly as media blobs do.
            workspace_root=cfg.store.data_dir / COMPUTE_WORKSPACE_DIRNAME,
            # The SAME directory, named as the HOST sees it — the only value
            # `LocalDockerBackend` may hand the Docker daemon as a bind source
            # (see its `__init__`'s two-roots comment, and
            # `_compute_workspace_host_root`'s own for why this cannot be
            # derived from `data_dir`). The identical `COMPUTE_WORKSPACE_DIRNAME`
            # is appended to both, so the container-side path and the host-side
            # path can only ever differ in the part the mount itself defines.
            workspace_host_root=(
                _compute_workspace_host_root(cfg) / COMPUTE_WORKSPACE_DIRNAME
            ),
        )
    # `nora.agent` may not import `nora.config` (import-linter's "only app and
    # __main__ read config") — this is the conversion point, the same shape
    # `ReminderCaps(**cfg.reminders.model_dump())` already uses just below.
    mcp_mounts = {
        name: McpMount(
            name=name, base_url=mount.base_url, token_env=mount.token_env, budget=mount.budget,
        )
        for name, mount in cfg.tools.mcp.items()
        if mount.enabled
    }
    mcp_tool_groups = build_mcp_tools(mcp_mounts)
    # The baked build stamp (DEC-97), read from `nora/version.json` — ONE
    # instance, shared by the `version` tool and the startup announcement. Two
    # would be two reads of the same file and, worse, two answers to "what am I
    # running" that a mid-boot file change could split. Absent in a source
    # checkout (it is generated by `scripts/build-image.sh` and gitignored),
    # which is why every version answer degrades to `unavailable` here rather
    # than to a guess.
    version_manifest = VersionManifest()
    giphy_client = None
    if giphy_key := os.environ.get("GIPHY_API_KEY"):
        from nora.infra.giphy import GiphyClient
        giphy_client = GiphyClient(api_key=giphy_key)
    # R-10's per-conversation lock and R-30's FIFO queue, built ONCE here.
    # Every path that may speak into a conversation holds this same instance —
    # two would be two locks and no serialisation at all. Built HERE, above the
    # registry, because the relay below needs it and the registry needs the
    # relay.
    slots = ConversationSlots(
        maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn,
    )

    # DEC-105: the cross-room relay. It does NOT send — it occasions a fire
    # turn into the destination (DEC-99's ruling), so R-1b's three links hold
    # and the relayed message is audited into the room it lands in.
    #
    # `fire_path` is genuinely circular: `FirePath` takes the tool registry,
    # and the registry takes this relay. Python resolves a closure's free
    # variables at CALL time and a relay can only be called inside a live turn
    # — long after both exist — so the cycle is closed by deferring rather than
    # by a mutable attribute nobody can see being set.
    async def _relay_speak(occasion: Any) -> str:
        if fire_path is None:
            # Every occasion needs a fire path, and this deployment has none
            # (heartbeat, announcements and compute all off). `failed_error` is
            # what `send_to` turns into an honest «I couldn't».
            log.warning("relay asked to speak with no fire path wired")
            return "failed_error"
        return await fire_path.speak(occasion)

    relay = Relay(
        policy=allowlist, speak=_relay_speak, slots=slots,
        occasion_for=relay_occasion, metrics=metrics,
    )
    tool_registry = ToolRegistry(
        route=route_client, vision=vision_client, blobs=blobs, search=search_client,
        weather=weather_client,
        presence=presence_store, circles=presence_circles,
        relay=relay,
        refresher=refresher,
        geocoder=geocoder,
        shortener=shortener_client,
        ai_peer=cfg.memory.ai_peer,
        mcp_tools=mcp_tool_groups,
        # The SAME store the tick reads from — two would be two queues, and the
        # rows `remind` wrote would sit in one while the heartbeat watched the
        # other.
        reminders=reminders,
        # `make_artifact`'s own pair (Nora Compute spec §4): `compute_jobs` is
        # ALWAYS the store built above, `compute_backend` is `None` whenever
        # `[compute] enabled` is false — the tool answers `unavailable` in
        # that case (R-5), never a silent no-op.
        compute_backend=compute_backend, compute_jobs=compute_jobs,
        # The third consumer of `tool_call_log_store` (Task 6 Step 8 wired the
        # first two: EngagementPipeline/FirePath, via `deliver.build`) —
        # `recall_lookups`'s own collaborator, tool-recall-log spec §4.
        tool_call_log_store=tool_call_log_store,
        # §9's caps as the DOMAIN value. `nora.agent` may not import
        # `nora.config` (import-linter's "only app and __main__ read config"),
        # so THIS is the conversion point: `RemindersConfig`'s fields are
        # `ReminderCaps`'s fields, and `model_dump()` keeps them one edit rather
        # than three. A field added to one and not the other is a `TypeError`
        # here, at startup, which is the failure worth having.
        reminder_caps=ReminderCaps(**cfg.reminders.model_dump()),
        # The same wall clock the tick runs on. `remind` compares `when` against
        # it for three of its six refusals, and a registry clock that disagreed
        # with the tick's would let a row be accepted as "two minutes out" and
        # then be found already due.
        clock=lambda: datetime.now(UTC),
        versions=version_manifest,
        giphy=giphy_client,
        # `[llm.budgets]`, the per-deployment delta over the table. Threaded
        # here for the same reason `reminder_caps` is: `nora.agent` may not
        # import `nora.config`, so the composition root is the conversion
        # point. Names are validated at config load, so anything arriving here
        # is a tool that exists.
        budget_overrides=cfg.llm.budgets,
    )
    # The room-scoped tools' one platform dependency. Built here rather than
    # taken off the adapter because membership is a read-only DIRECTORY query
    # (`nora.platform.signal.directory`'s two-method allowlist), not part of the
    # send/receive/react lifecycle — see `nora.domain.ports.RosterSource`.
    # ONE PER PLATFORM, not one for all of them. A single `SignalRoster` used
    # to serve every platform, so a Discord room was looked up in signal-cli —
    # which has never heard of that conversation id — and came back
    # `available=False`. `where_is` and every other room-scoped tool then
    # answered «I could not look», forever, on Discord. Fail-closed and
    # therefore harmless, but invisible: nothing errored and the honest refusal
    # is indistinguishable from the guard doing its job.
    #
    # Keyed the same way the adapter map is (DEC-102), and built only for
    # platforms that are actually enabled — a roster for a platform with no
    # adapter would be a client nothing can use.
    rosters: dict[str, RosterSource] = {}
    if "signal" in registry:
        rosters["signal"] = SignalRoster(
            base_url=cfg.platforms.signal.base_url, account=cfg.platforms.signal.account,
        )
    if "discord" in registry:
        from nora.platform.discord.roster import DiscordRoster
        rosters["discord"] = DiscordRoster(discord_token)
    if presence_store is not None:
        # The R-21/R-31 startup invariant (design/19 §7.7, wired with `whereis`
        # per DEC-54(4)): alarm the day a policy conversation is no longer a
        # subset of a circle. A BACKGROUND task, not a startup gate — it needs
        # the signal-cli roster, and an unreadable roster must not stop Nora
        # (the AUTHORITATIVE fail-closed check is per-call in `Whereis.locate`;
        # this is the belt-and-braces pre-flight its docstring names).
        background.spawn(_presence_preflight(
            allowlist=allowlist, rosters=rosters, persons=persons,
            circles=presence_circles, metrics=metrics,
            # Without her aliases Nora HERSELF is the «1 unmapped account» —
            # found live 2026-08-07, first boot of this pre-flight: her own
            # membership resolves to nobody in identity.toml, exactly the
            # stranger-shape the warning exists for. The tag path's Room gets
            # the same set (`EngagementPipeline._run_admitted`).
            #
            # EVERY platform's aliases, not Signal's alone: this pre-flight now
            # walks Discord rooms too, and Nora's SNOWFLAKE is what identifies
            # her there. Signal-only would have counted her own bot account as
            # one of the room's unmapped strangers — reintroducing, on the new
            # platform, the exact false warning found live on this check's
            # first boot.
            self_ids=registry.self_aliases(),
        ))
    engagement = EngagementPipeline(
        # EVERY built adapter, not just Signal's: a tag is answered on the
        # platform it arrived from, and `registry.adapters` is exactly the set
        # of platforms this process can both hear and speak on.
        adapters=dict(registry.adapters),
        audit=audit, memory=memory, persons=persons,
        doctrine_store=doctrine_store, context_assembler=assembler, runner=runner,
        tool_registry=tool_registry,
        # `allowlist` — policy.toml, the SAME instance the ingest pipeline
        # already checked this conversation is admitted under (DEC-18). This
        # is the second read of it: which MCP servers, not whether to engage
        # at all (DEC-71).
        policy=allowlist,
        debounce=debounce, canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        ai_peer=cfg.memory.ai_peer,
        context_window=cfg.store.context_window, timeout_seconds=tier.timeout_seconds,
        limits=limits, metrics=metrics, spawn=background.spawn, llm_state=llm_state,
        reactions=cfg.engagement.reactions.enabled,
        rosters=rosters,
        preview_source=preview_source,
        slots=slots,
        tool_call_log_store=tool_call_log_store,
    )
    dispatcher = Dispatcher(ingest=ingest, engagement=engagement, background=background)

    # Boot recovery (§8.2), UNCONDITIONALLY — deliberately NOT behind
    # `cfg.heartbeat.enabled`. A crash leaves rows FIRING, and a heartbeat that
    # has been switched off is exactly the state in which a stranded row would
    # otherwise sit there forever: at-most-once would silently stop applying on
    # the very config that looks safest. Only the LOOP is gated.
    #
    # `recover` counts what it sweeps; this call site logged and counted nothing,
    # which made a crash that ate reminders invisible to every dashboard.
    await recover(store=reminders, metrics=metrics, now=datetime.now(UTC))
    # Compute-job boot recovery, ALSO unconditional, and for the SAME reason —
    # deliberately NOT behind `cfg.compute.enabled`. An earlier draft of this
    # wiring put this call inside that gate; a deployment that crashes
    # mid-delivery and then flips `[compute] enabled=false` (to disable the
    # feature, unrelated to the crash) would leave any `FIRING` `ComputeJob`
    # row stranded forever — exactly the failure mode reminders' own
    # unconditional recovery above exists to prevent, silently reopened.
    # Fixed here per a subagent review that caught it by direct comparison
    # against `nora.app.heartbeat.recover`'s own stated reasoning.
    # The rows are RETURNED so the people waiting on them can be told. Sweeping
    # them terminal is right and not claiming they failed is right, but before
    # 2026-08-14 that combination also meant nobody was told ANYTHING: the
    # requester heard «I'm working on it» and then nothing, ever. Delivered
    # below, once `fire_path` exists.
    lost_jobs = await compute_poller_recover(
        store=compute_jobs, metrics=metrics, now=datetime.now(UTC),
    )
    # The liveness gauge is seeded HERE, not in `HeartbeatLoop.__init__` alone —
    # otherwise every deployment on the shipped default (`enabled = false`)
    # exports 0.0 forever, which reads identically to "ticked a moment ago".
    publish_idle_gauge(metrics)
    # THREE occasions need a fire turn now (DEC-97, extended by Nora Compute
    # spec §4): a reminder coming due, a version nobody has been told about,
    # and — added here — a compute job that has finished and needs
    # delivering. `announcing` is deliberately a TWO-part gate — the operator
    # left it on, AND the build carries a readable stamp. An unstamped build
    # (a source checkout, or an image not built through
    # `scripts/build-image.sh`) constructs no announcer at all rather than
    # spawning one that would immediately answer «I can't check»: there is
    # nothing to announce, and the warning belongs at boot, once, where an
    # operator reads it.
    announcing = cfg.version.announce and version_manifest.current().status == VERSION_OK
    if not announcing and cfg.version.announce:
        log.warning(
            "version announcement is on but this build carries no readable "
            "version stamp (%s) — nothing will be announced. Build with "
            "scripts/build-image.sh.", version_manifest.path,
        )
    fire_path: FirePath | None = None
    # `or cfg.compute.enabled`: without this, a deployment that enables
    # `[compute]` alone (heartbeat off, no version announcement) would build
    # `compute_backend` and accept `make_artifact` slow-path jobs, but the
    # poller below — which needs `fire_path` to deliver a finished job —
    # would never spawn, and every such job would sit `pending` forever with
    # no path to ever reach the group it was asked into. Caught while wiring
    # the poller itself: this gate pre-dates Nora Compute and had no reason
    # to anticipate a third occasion needing it.
    if cfg.heartbeat.enabled or announcing or cfg.compute.enabled:
        # The real fire turn. It holds the SAME collaborators the tag path does
        # — one record store, one Honcho client, one runner, one tool registry —
        # because a fire turn is a turn in the same conversation and a second
        # set would be a second Nora with her own memory and her own budgets.
        # It reads doctrine at `applies_to="heartbeat"` itself.
        fire_path = FirePath(
            persons=persons, records=audit, memory=memory,
            doctrine_store=doctrine_store, metrics=metrics,
            runner=runner, adapters=dict(registry.adapters), registry=tool_registry,
            canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
                ai_peer=cfg.memory.ai_peer,
            context_window=cfg.store.context_window,
            limits=limits,
            # `[heartbeat]`, not `[llm]`: nobody is waiting on a fire turn, so
            # the 60s chosen for a person staring at a group chat is the wrong
            # bound (§5.4).
            timeout_seconds=cfg.heartbeat.timeout_seconds,
            clock=lambda: datetime.now(UTC),
            rosters=rosters,
            # The SAME preview source the tag path holds. A fire turn is a
            # message into the same group, so a link in it earns the same card.
            preview_source=preview_source,
            llm_state=llm_state,
            tool_call_log_store=tool_call_log_store,
        )
    if lost_jobs and fire_path is not None:
        # Spawned, never awaited — a boot must not wait on a model call, and
        # `BackgroundTasks` holds the strong reference, exactly as the
        # announcer and the poller do.
        background.spawn(_tell_about_lost_jobs(
            jobs=lost_jobs, speak=fire_path.speak, slots=slots, metrics=metrics,
        ))
    elif lost_jobs:
        # No fire path on this deployment, so there is no way to say it. Loud
        # rather than silent: somebody is still waiting on each of these.
        log.error(
            "%d compute job(s) lost across the restart and NOBODY CAN BE TOLD — "
            "this deployment wires no fire path (heartbeat, announcements and "
            "compute all off)", len(lost_jobs),
        )

    if cfg.heartbeat.enabled and fire_path is not None:
        heartbeat = HeartbeatLoop(
            store=reminders,
            # The SAME `slots` the engagement pipeline holds. Two instances
            # would be two locks and no serialisation at all.
            slots=slots,
            # `allowlist` — policy.toml — NOT `policy`, which is the
            # CompositePolicy deciding whether a message is a tag and has no
            # `is_allowed` at all. Re-checking the ALLOWLIST at fire time is the
            # whole point of this parameter (§5.1): the file is read once at
            # startup and a row may be thirty days old.
            policy=allowlist,
            clock=lambda: datetime.now(UTC),
            metrics=metrics,
            tick_seconds=cfg.heartbeat.tick_seconds,
            fire=fire_path.run,
        )
        background.spawn(heartbeat.run())
    if announcing and fire_path is not None:
        # WHERE she announces comes from `policy.toml` — the rooms the operator
        # already allowed her into — and never from a second list in config.toml
        # that could silently disagree with the first (DEC-8). `primary_id`, not
        # an arbitrary member of `ids`: `also_ids` holds the id forms a platform
        # RETIRED, and sending to one of those fails.
        #
        # Filtered to the platforms this process actually built an adapter for.
        # The reason is unchanged now that `FirePath` routes per platform: a
        # conversation on a platform with no adapter could be CLAIMED and then
        # never spoken into, which under R-42 burns the announcement for good.
        # What changed is that the answer is no longer the literal "signal" —
        # an allowlisted Discord channel is announced into whenever Discord is
        # enabled, and skipped when it is not.
        targets = tuple(
            AnnouncementTarget(platform=conv.platform, conversation_id=conv.primary_id)
            for conv in allowlist.conversations
            if conv.platform in registry and conv.primary_id
        )
        announcer = VersionAnnouncer(
            manifest=version_manifest,
            store=SqlAnnouncementLog(engine),
            targets=targets,
            # `speak`, NOT `run`: `run` is the heartbeat's reminder-shaped
            # adapter and would need rows this occasion does not have.
            speak=fire_path.speak,
            # The SAME `slots` the tag path and the tick hold. Two would be two
            # locks and no serialisation at all.
            slots=slots,
            metrics=metrics,
            clock=lambda: datetime.now(UTC),
        )
        # Spawned, never awaited: a boot must not wait on a model call, and this
        # is the least important thing the process does. `BackgroundTasks` holds
        # the strong reference, exactly as it does for the adoption backfill.
        background.spawn(announcer.run())

    # The compute-job poller (Nora Compute spec §4), matching
    # `VersionAnnouncer`'s own wiring shape immediately above: a background
    # loop, gated on BOTH the feature flag and a live `fire_path` (a compute
    # job is delivered as a fire turn into the same conversation, exactly as
    # a reminder or an announcement is). `compute_backend` is only non-`None`
    # when `cfg.compute.enabled` is true (see its construction above), so
    # this condition is really `cfg.compute.enabled and fire_path is not
    # None` restated — spelled out with the third check anyway so the
    # relationship holds even if `compute_backend`'s own gate ever moves.
    if cfg.compute.enabled and fire_path is not None and compute_backend is not None:
        compute_poller = ComputeJobPoller(
            store=compute_jobs, backend=compute_backend,
            # `speak`, NOT `run`: the same reason `VersionAnnouncer` above
            # takes `fire_path.speak` rather than the reminder-shaped `run`.
            speak=fire_path.speak,
            # The SAME `slots` every other path into a conversation holds.
            # Two would be two locks and no serialisation at all.
            slots=slots,
            metrics=metrics,
            tick_seconds=cfg.compute.poll_tick_seconds,
            clock=lambda: datetime.now(UTC),
        )
        # Spawned, never awaited — the same reason `heartbeat.run()`/
        # `announcer.run()` above are: a boot must not wait on a background
        # loop, and `BackgroundTasks` holds the strong reference.
        background.spawn(compute_poller.run())

    return Dependencies(
        audit=audit, memory=memory, persons=persons, metrics=metrics,
        policy=policy, assembler=assembler, ingest=ingest, engagement=engagement,
        dispatcher=dispatcher, adapter=adapter, registry=registry,
        account=account, doctrine=doctrine,
        doctrine_store=doctrine_store, debounce=debounce, background=background,
        llm_state=llm_state, client=client,
    )


async def _tell_about_lost_jobs(
    *, jobs: list[Any], speak: Any, slots: ConversationSlots, metrics: Metrics,
) -> None:
    """Close the promise each lost job left open, one room at a time.

    Never fatal and never raised: this runs detached at boot, and a room that
    cannot be spoken into must not take the process down with it. Each failure
    is logged and counted — the whole point of this function is that somebody
    is waiting, so a delivery that silently did not happen would be the very
    defect it exists to fix.
    """
    for job in jobs:
        key = (job.platform, job.conversation_id)
        # `try_acquire`, the heartbeat's rule: a room already mid-turn is not
        # one to interrupt, and this is the least urgent thing the process does.
        if not slots.try_acquire(key):
            log.warning(
                "could not tell %s about a lost job: the room is mid-turn",
                job.conversation_id,
            )
            metrics.inc("nora_compute_lost_told_total", outcome="busy")
            continue
        try:
            outcome = await speak(lost_job_occasion(job))
            metrics.inc("nora_compute_lost_told_total", outcome=outcome)
        except Exception:
            log.exception("failed to tell %s about a lost job", job.conversation_id)
            metrics.inc("nora_compute_lost_told_total", outcome="failed_error")
        finally:
            slots.release_and_dispatch(key)


async def _presence_preflight(
    *,
    allowlist: Policy,
    rosters: Mapping[str, RosterSource],
    persons: Persons,
    circles: tuple[Circle, ...],
    metrics: Metrics,
    self_ids: frozenset[str] = frozenset(),
) -> None:
    """The R-21/R-31 disclosure pre-flight (design/19 §7.7): for every policy
    conversation, is its resolved membership a SUBSET of each circle?

    `check_disclosure_invariant` publishes `nora_presence_circle_subset_ok` and
    logs an ERROR on the day the answer is no — the operator's tell that a
    stranger joined the group (or someone left the circle) and `whereis` is now
    refusing there. Belt-and-braces over the KNOWN person_ids only; the per-call
    guard in `Whereis.locate` is authoritative and sees unmapped accounts
    (`view.unnamed`) that a person_id subset cannot — so this logs those as a
    warning rather than pretending the gauge covers them.

    **In a location-TRUSTED room that per-call guard no longer refuses on
    `view.unnamed` (DEC-79), so do not read the warning below as a block.** What
    still holds there: the roster must READ (a degraded room answers
    `unavailable`), the target must resolve inside it, and every waiver is
    counted on `nora_presence_disclosure_waived_total` — which is the runtime
    tell this once-at-boot warning cannot be, since an account that joins after
    startup never reaches this code again.

    Never fatal, never raises: an unreadable roster leaves the invariant
    UNVERIFIED (warned, not alarmed), and the per-call guard refuses on exactly
    that unverifiability."""
    for conv in allowlist.conversations:
        # Every platform that HAS a roster, not just Signal. Skipping a
        # platform here does not make it safe — the authoritative check is
        # per-call in `Whereis.locate` — but it does mean the gauge and the
        # operator's boot-time warning silently covered only half the rooms
        # once a second platform existed.
        roster_source = rosters.get(conv.platform)
        if roster_source is None:
            continue
        try:
            view = None
            # `ids` is the id plus its aliases; the first readable one is the room.
            for conversation_id in sorted(conv.ids):
                candidate = await Room(
                    platform=conv.platform, conversation_id=conversation_id,
                    roster_source=roster_source, persons=persons, self_ids=self_ids,
                ).members()
                if candidate.available:
                    view = candidate
                    break
            if view is None:
                log.warning(
                    "presence pre-flight: roster unreadable for %s conversation "
                    "%r (%s) — subset invariant unverified there; whereis's "
                    "per-call guard still refuses anything unverifiable",
                    conv.platform, conv.label or conv.primary_id, conv.primary_id,
                )
                continue
            if view.unnamed:
                log.warning(
                    "presence pre-flight: %d unmapped account(s) in %s "
                    "conversation %r (%s) — whereis refuses there until "
                    "identity.toml names them (`nora persons pending`)",
                    view.unnamed, conv.platform,
                    conv.label or conv.primary_id, conv.primary_id,
                )
            if conv.location:
                # DEC-74: this room is location-TRUSTED, so its group is
                # DELIBERATELY not a subset of the circle. Running the subset
                # invariant here would log a permanent false «R-21 FAILED …
                # whereis must refuse» and pin `nora_presence_circle_subset_ok=0`
                # for the circle — masking a REAL subset failure in another,
                # non-trusted room mapped to the same circle. Skip it: `where_is`
                # discloses here on purpose.
                #
                # This used to add «and an unmapped account still blocks
                # disclosure, trusted or not» as the second half of the
                # justification. DEC-79 made that false, and the fail-closed
                # audit was right to call it out: what actually remains in a
                # trusted room is the roster READ (degraded → `unavailable`), the
                # target resolving inside it, and the waiver counter. Skipping
                # this gauge is still correct — it would be a permanent false
                # alarm — but it is now the only pre-flight this room gets.
                log.info(
                    "presence pre-flight: %s conversation %r is location-trusted "
                    "(DEC-74) — the circle-subset invariant is intentionally "
                    "skipped here",
                    conv.platform, conv.label or conv.primary_id,
                )
                continue
            group_person_ids = frozenset(p.person_id for p in view.people)
            for circle in circles:
                check_disclosure_invariant(
                    circle=circle, group_person_ids=group_person_ids, metrics=metrics,
                    # WHICH room, so the gauge is per-conversation. Circle alone
                    # was last-writer-wins across every allowlisted room, so a
                    # failing one could be overwritten by a passing one and the
                    # alarm lost — and widening this loop to a second platform
                    # is exactly what made that reachable.
                    platform=conv.platform,
                    conversation=conv.primary_id or "",
                )
        except Exception:
            log.exception("presence pre-flight failed for a policy conversation")


def _build_media_custody(cfg: Config) -> tuple[MediaSource | None, BlobStore | None]:
    """The `(source, blobs)` pair the ingest pipeline takes for byte custody, or
    `(None, None)` when custody is OFF (DEC-43 wiring, DEC-47 encryption).

    Custody turns ON only when `[media] enabled` is true AND a valid encryption
    key is present — the bytes on disk are ciphertext, and there is deliberately
    no plaintext-on-disk mode. Every way it can be off returns `(None, None)`,
    which is the one wiring state `nora.ingest.stages.media` reads as
    `not_configured`: it records every attachment exactly as before (placeholder,
    no handle) and counts each one, so custody being off is visible in the
    metrics rather than silent.

    A MISSING or MALFORMED key is off, not fatal — warned and degraded, never a
    crash. This is the deliberate asymmetry with the LLM key (`config.load`
    refuses to start without that one): media is a bonus laid down beside the
    record, and losing it must never take the daemon down or, worse, write
    plaintext by accident. `config.load` therefore does not check this variable.

    The blob root is `store.data_dir / "media"`, with no media-specific config key
    (see `MediaConfig`): the row and the blob it names are one custody boundary,
    backed up and deleted together.
    """
    media = cfg.media
    if not media.enabled:
        return None, None
    if not media.encryption_key_env:
        log.warning(
            "media.enabled is true but media.encryption_key_env is unset — media "
            "custody is OFF (attachments recorded without custody). Set it to the "
            "name of an env var holding a base64 32-byte key to turn custody on."
        )
        return None, None
    raw = os.environ.get(media.encryption_key_env, "")
    if not raw:
        log.warning(
            "media custody is OFF: %s (media.encryption_key_env) is not set in the "
            "environment. Attachments are recorded without custody until it is.",
            media.encryption_key_env,
        )
        return None, None
    try:
        cipher = BlobCipher.from_base64(raw)
    except ValueError as exc:
        log.warning(
            "media custody is OFF: the key in %s is not a valid base64 32-byte "
            "AES-256 key (%s). Attachments are recorded without custody until it is.",
            media.encryption_key_env, exc,
        )
        return None, None
    source = SignalMediaSource(
        base_url=cfg.platforms.signal.base_url,
        timeout_seconds=media.timeout_seconds,
        max_bytes=media.max_bytes,
    )
    blobs = FilesystemBlobStore(cfg.store.data_dir / "media", cipher=cipher)
    return source, blobs
