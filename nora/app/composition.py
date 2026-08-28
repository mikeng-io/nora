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

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import boto3
from openai import AsyncOpenAI
from openai.types.shared.reasoning_effort import ReasoningEffort

from nora.agent import factory, tool_choice
from nora.agent.dreamer import (
    AdaptorBudget,
    DreamerRuntime,
    RegistryToolSource,
    verify_tool_subsets,
)
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
    DreamAdaptor,
    EngagePolicy,
    MemoryStore,
    PlatformAdapter,
    RecordStore,
    ReverseGeocoder,
    RoomConsolidator,
    RoomPortraitSynthesizer,
    RosterSource,
)
from nora.domain.reminders import ReminderCaps, ReminderStore
from nora.domain.tool_call_log import ToolCallLogStore
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.recognition import Recogniser
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages.debrief import Completion as DebriefCompletion
from nora.engagement.stages.debrief import Wiring as DebriefWiring
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.compute.docker_backend import LocalDockerBackend
from nora.infra.honcho import HonchoClient, WorkspaceRouter
from nora.infra.music import YtDlpSource
from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations
from nora.infra.sql.announcements import SqlAnnouncementLog
from nora.infra.sql.compute_jobs import SqlComputeJobStore
from nora.infra.sql.entities import SqlEntityStore
from nora.infra.sql.exchange import SqlExchangeStore
from nora.infra.sql.fact_candidates import SqlFactCandidateStore
from nora.infra.sql.facts import SqlFactStore, oversized_terms
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.media_descriptions import SqlMediaDescriptionStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.infra.sql.music_session import SqlMusicSessionStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.rendered_claims import SqlRenderedClaimStore
from nora.infra.sql.room_portrait import SqlRoomPortraitStore
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.infra.sql.turn_verdicts import SqlTurnVerdictStore
from nora.infra.version import OK as VERSION_OK
from nora.infra.version import VersionManifest
from nora.infra.veto_list import load_veto_list
from nora.infra.vision import StickerInterpreter, VisionClient, derive_vision_timeout
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import (
    CompositePolicy,
    MentionPolicy,
    ReplyToNoraPolicy,
    TriggerTextPolicy,
    VoiceNotePolicy,
)
from nora.ingest.stickers import StickerReader
from nora.instructions.dreamer import DreamerInstructions
from nora.instructions.frontmatter import InstructionError
from nora.language.consolidator import LanguageConsolidator
from nora.language.exchange_consolidator import ExchangeConsolidator
from nora.language.exchange_observer import ExchangePatternObserver
from nora.language.fact_adaptor import (
    FactAdaptor,
    FactCandidateAdaptor,
    FactEntityResolutionAdaptor,
)
from nora.language.note_curator import NoteCuratorAdaptor
from nora.language.note_revisitor import NoteRevisitor
from nora.language.observer import LexemeObserver
from nora.language.portrait_synthesizer import PortraitSynthesizer
from nora.obs.health import LLMState
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError, load_persons
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_tool_bridge import DiscordMusicToolBridge
from nora.platform.registry import AdapterRegistry, AdapterRegistryError, MediaSourceRegistry
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.media import SignalMediaSource
from nora.platform.signal.roster import SignalRoster
from nora.policy import Policy, PolicyError, load_policy
from nora.tag import Debounce
from nora.web.games import PulseGamesClient

from .agent_runner import LiveAgentRunner
from .announce import AnnouncementTarget, VersionAnnouncer
from .compute_poller import ComputeJobPoller
from .compute_poller import recover as compute_poller_recover
from .dispatch import Dispatcher
from .fire import (
    FirePath,
    gathering_occasion,
    lost_job_occasion,
    relay_occasion,
    reminder_occasion,
)
from .gatherings import GatheringService
from .heartbeat import HeartbeatLoop, publish_idle_gauge, recover
from .runtime import BackgroundTasks

log = logging.getLogger("nora.app.composition")


class StartupError(Exception):
    """A fatal startup validation failure — refuse to start, with a readable
    message. Each site below wraps one operator-diagnosable condition; the
    messages are unchanged from `nora.__main__.build_app`'s pre-phase-3 shape,
    only relocated here (`migration error` was added deliberately in Phase 2
    — see `design/09-decision-index.md`)."""


class _LanguageModelClient:
    """Satisfies `nora.language.observer.LexemeObserver`'s structural
    `_ModelClient` shape (`async def complete(self, *, prompt: str) -> str`)
    — a raw one-shot text completion over the process-wide OmniRoute
    `client`, the `VisionClient._create` shape without the image half.

    Lives HERE rather than in `nora.language` because that package may not
    import `strands`, `nora.agent.factory`, or `nora.config` at all (see
    `LexemeObserver`'s own module docstring) — the model client is INJECTED,
    and this is the one place already holding both the process-wide client
    and `[language].model`.

    Two things every OTHER raw `chat.completions.create` in this repo already
    has, that this one was missing until the final whole-branch review:

    * `response_format={"type": "json_object"}` — the ask `observer.py`'s
      prompt already makes ("Respond with ONLY a JSON object... no prose, no
      markdown fence") was carried entirely by the PROMPT, with nothing on
      the wire forcing the provider to honour it. The commit that introduced
      this call was titled "schema-forced lexeme extraction"; without this
      parameter that title was aspirational, not actual. This does not make
      `_parse_observations`/`_extract`'s malformed-response handling
      unnecessary — a provider can still return well-formed JSON of the
      WRONG shape, or ignore the parameter outright — but it is the one
      concrete lever this call has to ask the PROVIDER to hold up its side,
      the same way every other structured call in this codebase does.
    * **A bounded timeout.** Every other model call in the codebase is
      explicitly bounded and says why (`nora.engagement.stages.invoke`'s
      `asyncio.wait_for` around the whole agent loop;
      `nora.infra.vision.client.derive_vision_timeout`, "so a slow or hung
      MiMo becomes a bounded failure"). This one ran on the bare
      `AsyncOpenAI` default (600s x retries) until now — and unlike a tag
      turn's timeout, a hung Dreamer call is QUIET: `dream_loop` marks the
      scope `running` and skips it every subsequent tick
      (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising,
      so the exponential backoff non-negotiable 4 exists for — which only
      triggers on an EXCEPTION — never engages. A hung provider would be
      skipped forever, not backed off. `asyncio.TimeoutError` propagating
      from here reaches `LexemeObserver.observe` uncaught, then
      `LanguageConsolidator.consolidate` uncaught, then `_dream_once`'s
      per-scope call site inside `dream_loop`'s own try/except — which is
      exactly the already-proven "raising consolidator" path
      (`tests/unit/test_runtime.py::
      test_the_watermark_advances_only_after_observations_are_committed`'s
      crash-shaped half): counted, backed off, and the watermark left
      untouched, never advanced over a batch a hung call never actually read.
    """

    def __init__(self, *, client: AsyncOpenAI, model: str, timeout_seconds: float) -> None:
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def model(self) -> str:
        """The model name every `complete` below actually calls — the second
        half of `_ModelClient`'s shape, read by the observers so each row they
        write records what produced it (`nora.domain.models.LexemeCandidate
        .model`). It returns THIS object's `_model`, the same attribute the
        request is built from, so the stamp and the wire cannot name
        different models; handing the name to the observer separately would
        make that possible."""
        return self._model

    async def complete(self, *, prompt: str) -> str:
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            ),
            timeout=self._timeout_seconds,
        )
        return (response.choices[0].message.content or "").strip()


_REASONING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def _reasoning_effort(value: str, *, source: str) -> ReasoningEffort:
    """Narrow a config string to the provider's closed set, or refuse to start.

    `[standing] reasoning_effort` is already typed as this set, so it cannot
    arrive wrong. `[llm] reasoning_effort` — which it INHERITS when empty — is
    a bare `str` and can: a typo there reaches the provider and comes back a
    400 during a fold, inside the conversation's slot, on a turn that had
    already replied.

    Narrowed HERE rather than by retyping `LLMConfig`, which is the tag path's
    own field with its own callers — a wider change should not ride on this
    one. That retype is the better fix and is worth doing separately; this
    gives the boot-failure property for the debrief's use of it today.
    """
    if value not in _REASONING_EFFORTS:
        raise StartupError(
            f"{source} = {value!r} is not a reasoning effort the provider "
            f"accepts ({', '.join(sorted(_REASONING_EFFORTS))}) — refusing to "
            f"start rather than failing on the first call that uses it"
        )
    return cast(ReasoningEffort, value)


class _DebriefModelClient:
    """Satisfies `nora.engagement.stages.debrief.ModelClient` — the same raw
    one-shot completion `_LanguageModelClient` above makes, with ONE field
    added and it is the reason this is a separate class rather than a
    parameter on that one.

    **`finish_reason` comes back with the content.** A raw
    `chat.completions.create` does not raise on a cut-off the way a Strands
    agent's `MaxTokensReachedException` does — it returns
    `finish_reason="length"` and a **silently truncated body**, which can still
    parse as JSON into something shorter and wrong. That is the R-27
    substitution (an ERROR reported as a PASS) one layer down from where this
    repo usually enforces it, and the only way to keep it out is to hand the
    caller the field it must check. `_LanguageModelClient` returns a bare `str`
    and therefore cannot; widening it would change what four existing callers
    receive for a check none of them make.

    Everything else is `_LanguageModelClient`'s, for its stated reasons:
    `response_format={"type": "json_object"}` because prompt-only JSON
    enforcement was proven insufficient here once already, and an explicit
    `asyncio.wait_for` because the bare `AsyncOpenAI` default is 600s x
    retries — and this call HOLDS THE CONVERSATION'S SLOT, so an unbounded one
    is not a slow background job, it is a room that stops answering.
    """

    def __init__(
        self, *, client: AsyncOpenAI, model: str, timeout_seconds: float,
        reasoning_effort: ReasoningEffort,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
        # DECLARED, not inherited from the provider's default. This class did
        # not pass it at all, so the fold ran at whatever the route happened to
        # do — an unchosen setting on a call that spends the conversation's
        # slot, while the tag path passes `[llm] reasoning_effort` explicitly.
        # The VALUE is not argued anywhere: `18-flows.md` calls the fold's
        # effort "a measurement, not a setting" and specifies a spike over the
        # retained traces. This makes that spike runnable.
        self._reasoning_effort = reasoning_effort

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, prompt: str) -> DebriefCompletion:
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                reasoning_effort=self._reasoning_effort,
            ),
            timeout=self._timeout_seconds,
        )
        choice = response.choices[0]
        return DebriefCompletion(
            content=(choice.message.content or "").strip(),
            # `or ""` rather than a default of `"stop"`: a provider that omits
            # the field has told us nothing, and inventing `"stop"` would be
            # asserting the call completed. An empty string is not `"length"`,
            # so the body is parsed — which is the right call, because the
            # parse itself is the second gate and a well-formed body is
            # evidence the generation finished.
            finish_reason=choice.finish_reason or "",
        )


# The one subdirectory name a compute job's workspace lives under, appended to
# BOTH the container-side root (`store.data_dir`) and the host-side root, so the
# two paths handed to `LocalDockerBackend` cannot name different directories.
COMPUTE_WORKSPACE_DIRNAME = "compute-workspaces"


def _describe_models(cfg: Config) -> dict[str, str]:
    """Modality → the model that describes it, for every modality that CAN.

    The two config states an operator can reach — «switched off» and «no model
    named» — are collapsed to one thing the tool can read: absence. A modality
    appears here only when it is enabled AND names a model, so `media_fetch`
    needs no second flag and cannot accidentally call a describer that was only
    half-configured.

    This is also where the resolution of `vision_model`'s legacy alias lands,
    via `describer_for`, so `nora.agent` never learns the alias existed.
    """
    resolved = {}
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if describer is not None and describer.enabled and describer.model:
            resolved[modality] = describer.model
    return resolved


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
    # The reminders store `build_dependencies` already constructs (see
    # `reminders = SqlReminderStore(engine)` below) — exposed here so
    # `nora/web/status.py` can read pending rows without a second store.
    reminders: ReminderStore
    # `nora.web.games` reads Pulse's own REST API directly (Bearer-authed
    # with `PULSE_API_TOKEN`) — a separate credential and connection from
    # the LLM tool-calling path's `[tools.mcp.pulse]` MCP mount
    # (`PULSE_MCP_TOKEN`, folded into `ToolRegistry(mcp_tools=...)` below).
    # `None` when `PULSE_API_TOKEN` is unset, matching `giphy_client`'s own
    # optional-integration pattern just below — the web route degrades to
    # 503, never a guessed 200.
    games_client: PulseGamesClient | None
    # The SAME `presence_store`/`presence_circles` `Whereis` already reads
    # (below) — exposed here so `nora.web.presence` can serve a map without a
    # second `PresenceStore` over a second engine. `presence` is `None` when
    # `[presence] enabled = false` or identity.toml yields no circle (the
    # collector never wrote anything), matching `Whereis`'s own optionality.
    # UNLIKE `Whereis.locate`, `nora.web.presence` does NOT apply
    # `DisclosureGuard`/R-21 — that guard is a PER-ROOM, per-conversation
    # check with no web equivalent (no "conversation" exists on a portal
    # page); the web map's own access control is `checkSession()`'s
    # identity.toml allowlist, the same gate every other portal page uses
    # (design decision: web presence visibility = portal-wide, matching
    # status/memory, not per-room disclosure).
    presence: PresenceStore | None
    presence_circles: tuple[Circle, ...]
    # The SAME directory `factory.build_agent`'s `skills_dir` argument
    # already resolves (`cfg.doctrine.dir.parent / "skills"`) — exposed here
    # so `nora.web.skills` can read the same on-disk metadata (name,
    # description) for the portal, using its OWN `SkillStore` instance
    # rather than the live agent's Strands `AgentSkills` plugin (that one
    # is built per-tag inside `factory.create_agent` and holds no
    # web-reachable listing method).
    skills_dir: Path
    # The Dreamer's own pieces (Task 11), all `None`/empty when `[language]
    # enabled = false` — `nora.__main__.App.run` reads these to spawn (or log
    # skipping) `runtime.dream_loop`. `language_store` doubles as the
    # `RoomLanguageReader` `assembler` above already holds; exposed again
    # here because `dream_loop` needs the CONCRETE `SqlLanguageStore` (its
    # cursor/measurement surface, not just the read-only port).
    language_store: SqlLanguageStore | None
    language_consolidators: tuple[RoomConsolidator, ...]
    language_scopes: Callable[[], Sequence[str]]
    # Dreamer Plan 3 (2026-08-20 room-portrait design) — `None` under the same
    # `[language].enabled` gate as `language_store`/`language_consolidators`
    # above. Deliberately NOT a `RoomConsolidator`; `nora.app.runtime
    # .dream_loop` takes it as its own `portrait=` keyword, not a member of
    # `language_consolidators` — see that module's docstring for why.
    portrait_synthesizer: RoomPortraitSynthesizer | None
    # The Dreamer's instruction corpus. Built unconditionally (see its
    # construction below), so unlike its neighbours here it is never None —
    # `dream_loop` reads it for the per-tick `instruction_versions=` line, and
    # both observers hold it as their objective source.
    dreamer_instructions: DreamerInstructions
    # The Dreamer RUNTIME and what it schedules — `None`/empty under
    # `[dreamer] enabled = false`, the shipped default. Separate from the
    # `[language]` gate above on purpose: `[language]` decides whether the
    # CLOCK runs at all, `[dreamer]` decides whether the clock has a second,
    # adaptor-shaped schedule on it. A deployment can run today's consolidators
    # with no runtime, which is exactly what every deployment does today.
    dreamer_runtime: DreamerRuntime | None
    dream_adaptors: tuple[DreamAdaptor, ...]
    # `language_scopes`' workspace-keyed sibling. `Conversation.workspace`
    # (DEC-105) — several rooms of one workspace are ONE unit of work here, not
    # several, which is the whole reason a workspace-keyed adaptor could not
    # ride the per-scope schedule.
    workspace_scopes: Callable[[], Sequence[str]]
    # The SAME `GatheringService` built above alongside `event_store`/
    # `reminder_caps` and already threaded to `ToolRegistry(events=events)` —
    # exposed here too so `nora.web.api.register_web_routes`'s read-only
    # `/app/v1/events(/{id})` pair serves from the identical instance the
    # agent's event tools use, rather than a second `GatheringService` over the
    # same store.
    events: GatheringService
    # The render ledger, or `None` while `[facts]` is off. Exposed here
    # only so the retention sweep can prune it — 31 §3.1's trigger, which
    # is the one that already exists rather than a new loop. LAST and
    # defaulted, so every existing construction site keeps working.
    rendered_claims: SqlRenderedClaimStore | None = None


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
    # The reasoning-continuity retention store (Task 6, spec §6/§7.4),
    # REUSING `engine` for the same reason `reminders`/`compute_jobs`/
    # `tool_call_log_store` do above. UNCONDITIONAL, unlike the Dreamer's own
    # stores just below: Phase 2's `[debrief]` flag gates the FOLD, not the
    # raw retention write — retention must exist and be provably working
    # BEFORE that flag has anything to gate (spec §7.4, task-6-brief.md).
    tag_trace_store = SqlTagTraceStore(engine)
    # Standing (26-standing). The STORE is built unconditionally, REUSING
    # `engine` for the same reason its four neighbours above do — the read half
    # feeds `TranscriptInjection`, whose own `_standing` returns `None` for a
    # `None` store, so an always-built store costs one attribute on a
    # deployment with the flag off and removes a whole class of "wired the
    # writer, forgot the reader" mistake. What the flag gates is the FOLD,
    # which is the part that spends a model call inside the reply slot.
    standing_store = SqlStandingStore(engine)
    # The per-turn verdict's store (26 §3). Built unconditionally beside
    # `standing_store` for the same reason: it costs one attribute with
    # the tier off, and the debrief's own `store_verdict is None` guard
    # is what makes it inert rather than a second flag to forget.
    turn_verdict_store = SqlTurnVerdictStore(engine)
    # The Dreamer's store (Task 11), REUSING `engine` for the same reason
    # `reminders`/`compute_jobs`/`tool_call_log_store` do above. UNLIKE those,
    # this one IS gated on the feature flag — `[route]`/`[search]`-shaped,
    # not `[compute]`-shaped: `TranscriptInjection`'s `language` reader has
    # exactly one consumer (the tag/fire prefill), and `[language].enabled`
    # is that consumer's own off switch (`LanguageConfig`'s docstring), so
    # there is no second caller left stranded the way `remind`/`make_artifact`
    # would be by gating `reminders`/`compute_jobs`. Off, `TranscriptInjection`
    # gets `language=None` — the exact "not yet wired" state Task 9 shipped.
    #
    # **AND under `[dreamer]`, which is not a widening of the language tier.**
    # `SqlLanguageStore` owns `consolidation_watermark` — the DREAMER's cursor,
    # `(consolidator_name, scope_id) -> how far this adaptor has read` — so the
    # Dreamer's clock cannot persist a watermark for ANY adaptor without this
    # object, including adaptors that have nothing to do with language. That is
    # why `[dreamer] enabled = true` was inert on its own: `__main__` spawns the
    # loop only where this store exists, so a tier flag was deciding whether the
    # RUNTIME ran (2026-08-27; the runtime shipped "on" and had not ticked in
    # five days, and only `nora.log` said so).
    #
    # Building it here starts NOTHING language-shaped. The consolidators stay
    # gated on `cfg.language.enabled` below and are `()` when it is off, so the
    # loop ticks with no language adaptor registered — which is the state the
    # note and fact adaptors need and could not have.
    #
    # **The naming is the debt, and it is deliberate to leave it.** The
    # watermark table belongs to the Dreamer and lives in a module called
    # `language` for historical reasons; extracting it is its own port change
    # with its own migration, and doing it inside a fix for an inert flag would
    # put two unrelated risks in one deploy. Recorded rather than done.
    language_store: SqlLanguageStore | None = (
        SqlLanguageStore(engine, metrics=metrics)
        if (cfg.language.enabled or cfg.dreamer.enabled)
        else None
    )
    # Dreamer Plan 2's store (2026-08-20 exchange-patterns design) — same
    # gate, same reasoning as `language_store` immediately above: exactly one
    # consumer (this same tag/fire prefill), and `[language].enabled` is its
    # off switch too (`LanguageConfig`'s own docstring on why Plan 2 has no
    # independent flag).
    # The fact tier's read half (2026-08-20 fact-tier design §6). Its OWN
    # gate, unlike the three room stores above: `[facts] enabled` is a
    # different question from `[language] enabled`, because the fact tier
    # renders a block whose content is not derived from the room it renders
    # into — the only one — and that is a decision an operator makes
    # separately from whether the room-language tier runs.
    #
    # Ships OFF, and flipping it before an adaptor writes anything would make
    # `config.toml` assert a subsystem is running when none is — the defect
    # class this tier exists to prevent, committed by its own switch.
    fact_store: SqlFactStore | None = (
        SqlFactStore(
            engine,
            max_term_chars=cfg.facts.max_term_chars,
            max_predicate_chars=cfg.facts.max_predicate_chars,
            metrics=metrics,
        ) if cfg.facts.enabled else None
    )
    # The render ledger (31 §3), wired only when the tier is on: with `[facts]`
    # off nothing is rendered, so there is nothing to record and a store here
    # would be a table that only ever holds zero rows. Named rather than
    # inlined because the retention sweep needs it too.
    rendered_claims: SqlRenderedClaimStore | None = (
        SqlRenderedClaimStore(
            engine,
            window_hours=cfg.facts.recognition_window_hours,
            metrics=metrics,
        )
        if cfg.facts.enabled else None
    )
    if fact_store is not None:
        # 30 §5: a cap that only refuses a CONFIG above its ceiling leaves
        # oversized rows satisfying the database CHECK while defeating the
        # prompt budget the cap exists to protect. Lowering the knob below rows
        # that already exist must be visible at startup, naming them — not
        # discovered later as a tier that quietly kept rendering what the
        # operator just said was too big.
        # Inside `fact_store is not None`, so a lowered cap with `[facts]
        # enabled = false` is accepted silently — deliberate, because a dark
        # tier renders nothing and its rows defeat no prompt budget, and stated
        # here because it is a real gap in the guarantee otherwise.
        try:
            oversized = await oversized_terms(
                engine,
                max_term_chars=cfg.facts.max_term_chars,
                max_predicate_chars=cfg.facts.max_predicate_chars,
            )
        except Exception as exc:
            # Every neighbouring step converts to `StartupError`; a raw
            # `OperationalError` escaping from here would be the one boot
            # failure in this function with no operator-readable message.
            raise StartupError(f"[facts] cap check failed: {exc}") from exc
        if oversized:
            raise StartupError(
                "[facts] caps are below rows that already exist "
                f"(max_term_chars={cfg.facts.max_term_chars}, "
                f"max_predicate_chars={cfg.facts.max_predicate_chars}). "
                "Raise the caps or remove these rows; they will not be "
                "truncated, because silently shortening a recorded claim to "
                "satisfy a knob is worse than not starting:\n  "
                + "\n  ".join(oversized)
            )
    exchange_store: SqlExchangeStore | None = (
        SqlExchangeStore(engine, metrics=metrics) if cfg.language.enabled else None
    )
    # Dreamer Plan 3's store (2026-08-20 room-portrait design) — same gate,
    # same reasoning: exactly one consumer (this same tag/fire prefill), and
    # `[language].enabled` is its off switch too (no independent flag, same
    # as Plan 2).
    portrait_store: SqlRoomPortraitStore | None = (
        SqlRoomPortraitStore(engine, metrics=metrics) if cfg.language.enabled else None
    )
    # The note tier's store (2026-08-21 note-tier design), REUSING `engine`
    # like every store above. Gated on its OWN flag rather than `[language]`'s,
    # unlike the three Dreamer stores: this tier is not the Dreamer's — its
    # reader is the tag prefill and its future writers are the agent's
    # `note_create`/`note_update` and the curator, so `[language] enabled`
    # would be somebody else's switch. Off, `TranscriptInjection` gets
    # `notes=None` and `_notes` answers `()` without a query — the dormant
    # state the tier shipped in. No `metrics=`: `SqlNoteStore.__init__` takes
    # an engine and nothing else on purpose (its own docstring), and the one
    # read on the critical path is counted by `_notes` instead.
    note_store: SqlNoteStore | None = SqlNoteStore(engine) if cfg.notes.enabled else None

    # Resolved here, not inside AdapterRegistry — the same convention every
    # other `*_env` secret (the LLM key, the route key) follows: composition.py
    # reads the environment once and hands the already-resolved value to
    # whatever constructs the client that needs it (design/discord-integration/).
    discord_token = os.environ.get(cfg.platforms.discord.token_env, "")
    # design/discord-music/00-overview.md. `spawn=asyncio.ensure_future`
    # rather than the `BackgroundTasks` instance built further down this
    # function (out of scope here) — safe because `MusicPlayerManager`
    # already holds its own strong reference to every task it spawns
    # (`self._idle_tasks`), the exact GC-mid-flight bug `BackgroundTasks`
    # exists to prevent for tasks nobody else references.
    music_manager = (
        MusicPlayerManager(
            queue_store=SqlMusicQueueStore(engine), session_store=SqlMusicSessionStore(engine),
            track_source=YtDlpSource(), idle_timeout_seconds=cfg.music.idle_timeout_seconds,
            spawn=asyncio.ensure_future, autoplay_enabled=cfg.music.autoplay_enabled,
            autoplay_prewarm_seconds=cfg.music.autoplay_prewarm_seconds,
        )
        if cfg.music.enabled
        else None
    )
    try:
        registry = await AdapterRegistry.from_config(
            cfg.platforms, metrics=metrics, discord_token=discord_token,
            music_manager=music_manager, music_crossfade_seconds=cfg.music.crossfade_seconds,
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

    # The Dreamer's own instruction corpus — its doctrine plus one objective
    # per adaptor (instruction-substrate spec §4.1). Bind-mounted read-only the
    # way `nora/doctrine` is, so an operator edit is live on the next Dreamer
    # tick with no rebuild. Built unconditionally rather than under
    # `[language].enabled` so a malformed objective is a STARTUP failure with a
    # filename in the message, not a silent Dreamer that stops writing rows
    # three hours later.
    dreamer_instructions = DreamerInstructions(
        cfg.dreamer.dir,
        reload=cfg.dreamer.reload, strict=cfg.dreamer.strict, metrics=metrics,
    )
    try:
        dreamer_instructions.objective_names()
    except InstructionError as exc:
        raise StartupError(f"Dreamer instruction error: {exc}") from exc

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
    # `[engagement.trigger].enabled` (default True — see TriggerConfig) gates
    # ONLY the spoken/typed trigger phrase out of the composite: a real
    # `@mention` or a reply to one of her own messages still engage her when
    # this is off, so turning it off narrows the ways IN, it does not silence
    # her outright the way `policy.toml` denying a room does.
    engage_policies: list[EngagePolicy] = []
    if cfg.engagement.trigger.enabled:
        engage_policies.append(TriggerTextPolicy(cfg.engagement.trigger.text))
    engage_policies.append(MentionPolicy(self_aliases))
    engage_policies.append(ReplyToNoraPolicy(self_aliases))
    # Any voice note engages her, independent of trigger text (operator
    # directive 2026-08-26) — the transcription is conversation content in the
    # body by the time the gate runs. Last so the deterministic text paths
    # keep their reasons, but it fires on any note regardless.
    engage_policies.append(VoiceNotePolicy())
    policy = CompositePolicy(tuple(engage_policies))
    assembler = TranscriptInjection(
        audit=audit, memory=memory, persons=persons,
        context_window=cfg.store.context_window, metrics=metrics,
        ai_peer=cfg.memory.ai_peer,
        self_card_conclusions=cfg.memory.self_card_conclusions,
        tagger_card_conclusions=cfg.memory.tagger_card_conclusions,
        # `None` while `[language] enabled = false` — Task 9's "not yet
        # wired" default, unchanged. The STORE limit stays this class's own
        # default (`DEFAULT_ROOM_LANGUAGE_STORE_LIMIT`); `[language]` has no
        # knob for it — see `LanguageConfig`'s docstring for why only the
        # RENDER cap (`max_rendered_lexemes`, threaded below into
        # `EngagementPipeline`/`FirePath`) is configurable, and why store
        # staying wider than render is what the store limit not moving here
        # depends on.
        language=language_store,
        exchange=exchange_store,
        portrait=portrait_store,
        # `None` while `[notes] enabled = false`. `policy=allowlist` is the
        # SAME policy.toml instance the ingest pipeline and `EngagementPipeline`
        # are given below — the assembler needs it for one thing only, naming
        # the workspace a room belongs to (`Conversation.workspace`, DEC-105),
        # which is what the note index is scoped by. Handing it a different
        # Policy would scope the read to a workspace ingest never admitted.
        notes=note_store,
        policy=allowlist,
        # The fact tier reads its workspace through the SAME `policy=allowlist`
        # the note tier above uses — two tiers keyed on `Conversation.workspace`
        # must not each carry their own idea of what a room's workspace is.
        facts=fact_store,
        # The render ledger, wired only when the tier is on: with `[facts]`
        # off nothing is rendered, so there is nothing to record and a store
        # here would be a table that only ever holds zero rows.
        rendered=rendered_claims,
        max_room_facts=cfg.facts.max_room_facts,
        max_sender_facts=cfg.facts.max_sender_facts,
        # Standing's READ half. `None` while `[standing] enabled = false`, the
        # shipped default, so `<standing>` renders nothing. Unlike `notes`/
        # `facts` beside it this needs no `policy=` at all: a position is keyed
        # `(platform, conversation_id)` (S4) and is NOT shared across a
        # workspace's rooms — two rooms in one social unit share what the
        # workspace knows, not where a conversation had got to.
        #
        # It is the WHOLE store rather than a read-only narrowing, and the same
        # instance the debrief's `Wiring` gets below: the fold's non-negotiable
        # is that the read and the write are the same row seen twice, and two
        # objects for one row is how that stops being structurally true.
        standing=standing_store if cfg.standing.enabled else None,
    )

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:
        # IngestPipeline's `spawn` is `Callable[[Coroutine], None]` — it never
        # needs the Task back. `BackgroundTasks.spawn` returns one (callers that
        # DO want it, like Dispatcher, call it directly), so this drops the
        # return value rather than widening the pipeline's port to match.
        background.spawn(coro)

    media_sources, blobs = await _build_media_custody(cfg)

    tier = cfg.llm
    api_key = os.environ.get(cfg.llm.api_key_env, "")  # presence already checked by config.load
    client = factory.create_client(base_url=cfg.llm.base_url, api_key=api_key)
    # `dream_loop`'s `scopes` callable — the SAME policy.toml conversations
    # `VersionAnnouncer`'s `targets` reads below, re-evaluated fresh each
    # tick (cheap: `allowlist` is loaded once at startup and immutable) so a
    # future policy-reload does not need this closure to change. No
    # platform filter, unlike the announcer's `targets`: this loop only
    # READS `RecordStore.since`, which needs no adapter for the platform.
    language_scopes: Callable[[], Sequence[str]] = lambda: tuple(  # noqa: E731
        conv.primary_id for conv in allowlist.conversations if conv.primary_id
    )
    # The same closure over the OTHER key. Sorted and de-duplicated: two rooms
    # of one workspace are one unit of work, and a set would reorder the tick
    # between runs of the same build — which turns "did adaptor X run for
    # workspace Y this tick" into a question you cannot answer from a log.
    workspace_scopes: Callable[[], Sequence[str]] = lambda: tuple(sorted(  # noqa: E731
        {conv.workspace for conv in allowlist.conversations if conv.workspace}
    ))
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
        # The process-wide registry, not a private one — `nora_tagtrace_
        # truncations_total` (spec §11) has to land where `/metrics` reads it.
        metrics=metrics,
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
    # §9's caps as the DOMAIN value, extracted to a variable (not built inline
    # at the `ToolRegistry(...)` call below the way it was before Events) so
    # `GatheringService` and `ToolRegistry` share the exact SAME `ReminderCaps` —
    # an event's linked reminder must respect the identical anti-runaway caps
    # a manually requested one does, and two separately constructed instances
    # read as "the same value" only by coincidence of both reading `cfg.reminders`.
    reminder_caps = ReminderCaps(**cfg.reminders.model_dump())
    # `GatheringService` (Gathering module design spec): the ONE use-case layer for
    # Gathering business rules. `event_store` REUSES `engine` for the same reason
    # `reminders`/`compute_jobs`/`tool_call_log_store` do above — a second
    # `create_sql_engine` would fork the pool. `route_source=route_client` is
    # the SAME `RouteClient` (or `None`) `place_info`/`route` already use —
    # a second instance would be a second, unrelated key check and a second
    # client the composition root has no other reason to own. `honcho=memory`
    # and `ai_peer=cfg.memory.ai_peer` are the SAME Honcho client and id
    # already threaded to `TranscriptInjection`/`ToolRegistry` below.
    event_store = SqlGatheringStore(engine)
    events = GatheringService(
        store=event_store,
        reminders=reminders,
        reminder_caps=reminder_caps,
        route_source=route_client,
        honcho=memory,
        ai_peer=cfg.memory.ai_peer,
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
    # The durable half of media dedup (sticker spec §4.1, Task 10): `VisionCache`
    # becomes a read-through in front of THIS store rather than a second,
    # disagreeing copy — REUSING `engine` for the reason `reminders`/
    # `compute_jobs`/`tool_call_log_store` all do above (one pool, one
    # lifecycle; a second `create_sql_engine` would fork the pool and its own
    # `aclose()` would dispose the engine everything else is still using).
    media_descriptions = SqlMediaDescriptionStore(engine)
    media_objects = SqlMediaObjectStore(engine)
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
    # A SECOND VisionClient, not a second connection: same process-wide
    # OmniRoute client, the SAME token budget as `media_fetch`'s vision call
    # (the 2026-08-07 probe refuted a smaller one — see `StickerInterpreter`'s
    # docstring) and a sticker-specific timeout.
    # `derive_vision_timeout` is deliberately not used — see MediaConfig.
    sticker_reader = None
    if cfg.media.sticker_descriptions and blobs is not None:
        sticker_reader = StickerReader(
            blobs=blobs,
            descriptions=media_descriptions,
            interpreter=StickerInterpreter(
                VisionClient(
                    client=client,
                    model=cfg.media.vision_model,
                    max_tokens=cfg.media.sticker_prompt_max_tokens,
                    reasoning_effort=cfg.media.vision_reasoning_effort,
                    max_bytes=cfg.media.vision_max_bytes,
                    timeout_seconds=cfg.media.sticker_timeout_seconds,
                ),
                model=cfg.media.vision_model,
            ),
            model=cfg.media.vision_model,
            metrics=metrics,
        )
    # `[speech.stt]`'s transcriber: wired ONLY when enabled — the same
    # inert-ship rule as `stickers`. Off leaves the ingest stage without a
    # transcriber and a voice note is recorded with a bare handle, exactly as
    # before speech existed. On, the local `stt` service transcribes voice
    # notes inline during custody (audio stays on the box — the endpoint is
    # reachable only on the private `nora` network).
    transcriber = None
    if cfg.speech.stt.enabled:
        from nora.infra.speech import SenseVoiceTranscriber
        transcriber = SenseVoiceTranscriber(
            endpoint=cfg.speech.stt.endpoint,
            language=cfg.speech.stt.language,
            timeout_seconds=cfg.speech.stt.timeout_seconds,
        )
    ingest = IngestPipeline(
        audit=audit, memory=memory, persons=persons,
        policy=policy, allowlist=allowlist, metrics=metrics, spawn=_spawn,
        media_sources=media_sources, blobs=blobs,
        media_store=media_objects,
        stickers=sticker_reader, transcriber=transcriber,
        language=cfg.speech.stt.language,
        max_description_chars=cfg.media.sticker_description_max_chars,
    )
    # `web_search`/`web_fetch` (Tavily, DEC-73/DEC-116) are wired ONLY when
    # `[search] enabled` AND a key is present — the `route`-keyless gate. Off (or
    # keyless) leaves the registry without a provider and both tools answer
    # `unavailable` (R-5), never a silent empty. The key is resolved from the
    # environment here (DEC-8), never read inside the adapter. One provider, one
    # key, both verbs — no SearXNG/Firecrawl and no model-chosen-URL fetch over
    # Nora's own network.
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
                extract_url=cfg.search.extract_url,
                extract_depth=cfg.search.extract_depth,
                extract_max_chars=cfg.search.extract_max_chars,
            )
        else:
            log.warning(
                "search enabled but %s is unset — web_search stays unavailable",
                cfg.search.api_key_env,
            )
    # The Dreamer's own consolidator (Task 11) — built ONLY when
    # `[language] enabled`, mirroring `language_store` above (there is no
    # second consumer to leave stranded by gating it, unlike `reminders`/
    # `compute_jobs`). `LexemeObserver` gets its OWN `_LanguageModelClient`
    # over the SAME process-wide `client` — one connection, `[language]
    # .model` rather than `[llm].model`, because the Dreamer is a background
    # job nobody is waiting on and may reasonably run a different tier.
    #
    # `search=search_client` is wired ONLY behind a THIRD gate,
    # `[language].verify_known_entities` (2026-08-20 design doc) — moved to
    # live AFTER `search_client` is built (it used to sit right after `client`
    # above) because it now depends on it. `search_client` staying `None`
    # (search off, or on but keyless) leaves `LexemeObserver`'s known-entity
    # check unwired exactly like every other optional collaborator here —
    # `verify_known_entities=true` with no working search client is silently
    # a no-op, not a startup error, matching `[search]`'s own "unavailable,
    # never a hard failure" shape.
    language_consolidators: tuple[RoomConsolidator, ...] = ()
    if cfg.language.enabled and language_store is not None:
        language_consolidators = (
            LanguageConsolidator(
                observer=LexemeObserver(
                    _LanguageModelClient(
                        client=client, model=cfg.language.model,
                        timeout_seconds=cfg.language.timeout_seconds,
                    ),
                    objective=dreamer_instructions,
                    metrics=metrics,
                    search=(
                        search_client
                        if cfg.language.verify_known_entities else None
                    ),
                    verify_max_candidates=cfg.language.verify_max_candidates,
                ),
                store=language_store,
            ),
        )
        # Dreamer Plan 2 (DEC-120) — `nora.app.runtime._dream_once` hands
        # every `RoomConsolidator` in this tuple the SAME batch over the ONE
        # shared watermark, so this needs no cursor of its own (see
        # `nora.infra.sql.exchange`'s module docstring). Gated on
        # `exchange_store is not None` too, since that store is itself built
        # only under `cfg.language.enabled` (see its own construction
        # above) — the check is redundant today but documents the real
        # dependency rather than assuming the two flags can never diverge.
        # `exchange_model` defaults to `[language].model` one level down,
        # the same way that field itself defaults to `[llm].model`.
        if exchange_store is not None:
            language_consolidators = (
                *language_consolidators,
                ExchangeConsolidator(
                    observer=ExchangePatternObserver(
                        _LanguageModelClient(
                            client=client,
                            model=cfg.language.exchange_model or cfg.language.model,
                            timeout_seconds=cfg.language.timeout_seconds,
                        ),
                        objective=dreamer_instructions,
                        metrics=metrics,
                    ),
                    store=exchange_store,
                ),
            )
    # Dreamer Plan 3 (2026-08-20 room-portrait design, DEC-121) — built only
    # when its THREE inputs are ready (language/exchange READERS, portrait
    # WRITER), same gate as `language_consolidators` above. Deliberately not
    # added to `language_consolidators`: `nora.app.runtime.dream_loop` takes
    # it as its own `portrait=` keyword (see that module's docstring for why
    # this is not a `RoomConsolidator`). No new model client — no model call
    # in this module at all (see `nora.language.portrait_synthesizer`'s own
    # docstring). Reuses `[language].max_rendered_lexemes`/
    # `max_rendered_exchanges` — the SAME limits the live prefill already
    # uses for `<room_language>`/`<room_exchange>` — rather than a new knob.
    portrait_synthesizer: RoomPortraitSynthesizer | None = None
    if (
        cfg.language.enabled and language_store is not None
        and exchange_store is not None and portrait_store is not None
    ):
        portrait_synthesizer = PortraitSynthesizer(
            language=language_store, exchange=exchange_store, store=portrait_store,
            max_rendered_lexemes=cfg.language.max_rendered_lexemes,
            max_rendered_exchanges=cfg.language.max_rendered_exchanges,
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
    # Image generation ([image_gen], DEC-137): enabled AND refresh token
    # present → build the lane (same `route`-keyless gate the shortener uses).
    # Off (or tokenless) leaves the `generate_image` tool answering
    # `unavailable` — never a crash, never `nothing_found` (R-5/R-27). The
    # access-token state file lives in the same repo-tree state dir
    # `./state/signal-cli` already uses (container-writable, host-immutable
    # `.env` holds only the refresh token).
    image_generator = None
    image_refs_allowed = False
    # Image custody (Miles 2026-08-26): generated images are stored in Nora's
    # OWN blob_store (rustfs) and sent as platform attachments — the
    # `drop`/shortener path is NOT involved (drop's own rustfs backend serves
    # HTML artifacts, a different concern).
    if cfg.image_gen.enabled:
        image_refs_allowed = cfg.image_gen.allow_reference_images
        xai_cfg = cfg.image_gen.xai
        refresh_token = os.environ.get(xai_cfg.refresh_token_env, "")
        if refresh_token:
            from nora.infra.image_gen import build_image_generator
            state_file = Path("/app/state/xai-oauth.json")
            image_generator = build_image_generator(
                provider=cfg.image_gen.provider,
                refresh_token=refresh_token,
                state_file=state_file,
                model=xai_cfg.model,
                quality_model=xai_cfg.quality_model,
                timeout_seconds=xai_cfg.timeout_seconds,
            )
            if image_generator is None:
                # M6: unknown provider (or unconstructable lane) degrades
                # the tool, never crashes the daemon.
                log.warning(
                    "image_gen enabled but provider %r could not be built — "
                    "generate_image stays unavailable",
                    cfg.image_gen.provider,
                )
        else:
            log.warning(
                "image_gen enabled but %s is unset — generate_image stays unavailable",
                xai_cfg.refresh_token_env,
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
    # The durable half of the cache (2026-08-19): survives a restart, and is
    # what `nora.analytics` reads (READ-ONLY — it never calls a geocoder
    # itself) to label a derived place. REUSES `engine` for the same reason
    # `presence_store` above does — a second `create_sql_engine` would fork
    # the pool.
    from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
    geocode_cache_store = SqlGeocodeCacheStore(engine)
    geocoder: ReverseGeocoder | None = None
    if cfg.presence.geocode_provider == "hkgov":
        hkgov_token = os.environ.get(cfg.presence.hkgov_token_env, "")
        if hkgov_token and cfg.presence.hkgov_geocode_url:
            from nora.infra.geocode_hkgov import HKGovGeocoder
            geocoder = HKGovGeocoder(
                service_url=cfg.presence.hkgov_geocode_url, token=hkgov_token,
                lang_zh=cfg.presence.hkgov_lang_zh, lang_en=cfg.presence.hkgov_lang_en,
                cache_store=geocode_cache_store,
            )
    else:
        maps_key = os.environ.get(cfg.presence.geocode_api_key_env, "")
        if maps_key:
            from nora.infra.geocode import GoogleGeocoder
            geocoder = GoogleGeocoder(api_key=maps_key, cache_store=geocode_cache_store)
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
    games_client = None
    if pulse_api_token := os.environ.get("PULSE_API_TOKEN"):
        games_client = PulseGamesClient(token=pulse_api_token)
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
    # `DiscordMusicToolBridge` (design/discord-music/07-agent-tool.md) — the
    # four music agent tools' own collaborator. Built ONLY when BOTH a
    # Discord adapter and a music manager exist: `music_manager` is already
    # `None` under `cfg.music.enabled = false` (its own construction above),
    # and `"discord" in registry` is the same narrowing `rosters["discord"]`
    # below uses. Shares the SAME `music_manager` and Discord adapter every
    # slash command already uses — never a second mutation path.
    music_bridge: DiscordMusicToolBridge | None = None
    if music_manager is not None and "discord" in registry:
        discord_adapter = registry.get("discord")
        # The same defensive narrowing `signal_adapter` above needs, for the
        # identical reason: `AdapterRegistry.get` returns the port type, and
        # the bridge needs `DiscordAdapter.current_client()`, which the port
        # does not declare.
        if isinstance(discord_adapter, DiscordAdapter):
            music_bridge = DiscordMusicToolBridge(
                adapter=discord_adapter, manager=music_manager,
                crossfade_seconds=cfg.music.crossfade_seconds,
            )
        else:
            log.error(
                "internal error: registry's 'discord' entry is not a DiscordAdapter "
                "— music agent tools stay unavailable"
            )
    tool_registry = ToolRegistry(
        route=route_client, vision=vision_client, describe_models=_describe_models(cfg),
        blobs=blobs, descriptions=media_descriptions, search=search_client,
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
        # The unified media index store — same engine, same lifecycle.
        media_store=media_objects,
        # the six event tools (`create_event`/`update_event`/`rsvp_event`/
        # `cancel_event`/`list_events`/`suggest_venue`) answer `unavailable`
        # (R-5) whenever a composition has not wired one; every deployment
        # here has, since it needs nothing this file does not already build.
        events=events,
        # `make_artifact`'s own pair (Nora Compute spec §4): `compute_jobs` is
        # ALWAYS the store built above, `compute_backend` is `None` whenever
        # `[compute] enabled` is false — the tool answers `unavailable` in
        # that case (R-5), never a silent no-op.
        compute_backend=compute_backend, compute_jobs=compute_jobs,
        # The third consumer of `tool_call_log_store` (Task 6 Step 8 wired the
        # first two: EngagementPipeline/FirePath, via `deliver.build`) —
        # `recall_lookups`'s own collaborator, tool-recall-log spec §4.
        tool_call_log_store=tool_call_log_store,
        # §9's caps as the DOMAIN value, the SAME `reminder_caps` instance
        # `GatheringService` was built with above — see that variable's own
        # comment for why they must not be two separately constructed copies.
        # `nora.agent` may not import `nora.config` (import-linter's "only app
        # and __main__ read config"), so THAT construction is the conversion
        # point: `RemindersConfig`'s fields are `ReminderCaps`'s fields, and
        # `model_dump()` keeps them one edit rather than three. A field added
        # to one and not the other is a `TypeError` there, at startup, which
        # is the failure worth having.
        reminder_caps=reminder_caps,
        # The same wall clock the tick runs on. `remind` compares `when` against
        # it for three of its six refusals, and a registry clock that disagreed
        # with the tick's would let a row be accepted as "two minutes out" and
        # then be found already due.
        clock=lambda: datetime.now(UTC),
        # The note tier's four tools. `None` while `[notes] enabled = false`,
        # so all four answer `unavailable` (R-5) rather than a guessed empty
        # result — the same optional-collaborator shape `events` and
        # `reminders` above use. `note_workspace` resolves a room to its
        # `Conversation.workspace` (DEC-105), because a note is workspace-keyed
        # while every tool argument the model can name is room-scoped; without
        # it the tools would have no way to reach the right notebook. The
        # SAME `allowlist` the assembler is given, for the reason stated there.
        notes=note_store,
        # RESOLVER, not a constant — see `build_tools`' own note. This
        # deployment has two workspaces (`ridgeline`, `friends`) that
        # `policy.toml` says are never paired, so a fixed string would hand one
        # room the other's notebook. `None` for a room policy cannot name, which
        # the note verbs report as `unavailable` rather than guessing a
        # notebook. Same `allowlist` instance the assembler and ingest hold.
        note_workspace=_note_workspace_of(allowlist),
        versions=version_manifest,
        giphy=giphy_client,
        music=music_bridge,
        image_generator=image_generator,
        image_refs_allowed=image_refs_allowed,
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
    # The debrief's collaborators, as ONE object that is either present or
    # absent (`debrief.Wiring` — the shape is deliberate: four optional
    # parameters would let a deployment wire the store and forget the client,
    # which runs the stage, finds nothing to do, and reports nothing).
    #
    # `[standing] model` empty means `[llm] model`, the same defaulting
    # `[language] exchange_model` uses one section over. A separate name is
    # available because the fold is a different job from replying — one
    # structured output, no tools, no audience — and may want a cheaper or a
    # more literal model; empty is the honest default rather than a guess.
    #
    # `objective=dreamer_instructions` is the SAME hot-reloading instruction
    # substrate the language observers hold, read through `objective_body` and
    # NOT `for_objective`: the latter prepends the Dreamer's own doctrine,
    # which is research-framed, and «a research-framed doctrine on a task with
    # no research» is one of the two named causes of the 4096-token runaway
    # that made this a plain completion in the first place. The file living
    # under `nora/dreamer/objectives/` says nothing about Standing being a
    # Dreamer adaptor — it is a tag-path stage; that directory is simply where
    # the bind-mounted, hot-reloading instruction text lives.
    debrief_wiring: DebriefWiring | None = None
    if cfg.standing.enabled:
        debrief_wiring = DebriefWiring(
            store=standing_store,
            store_verdict=turn_verdict_store,
            # The note tier's sink. `None` while `[notes] enabled = false`,
            # so the debrief parses no proposals and raises none — the
            # same inert shape the other optional collaborators use.
            notes_sink=note_store if cfg.notes.enabled else None,
            # The SAME workspace resolver the note tools and the assembler
            # hold. `propose`'s key is the workspace (DEC-105), and two
            # tiers keyed on it must not each carry their own idea of
            # which workspace a room belongs to.
            workspace_of=_note_workspace_of(allowlist),
            client=_DebriefModelClient(
                client=client,
                model=cfg.standing.model or cfg.llm.model,
                timeout_seconds=cfg.standing.timeout_seconds,
                # Empty means inherit `[llm] reasoning_effort` — the same
                # defaulting `model` above uses, and the honest default while
                # the spike that would choose a value has not been run.
                reasoning_effort=_reasoning_effort(
                    cfg.standing.reasoning_effort or cfg.llm.reasoning_effort,
                    source=(
                        "[standing] reasoning_effort"
                        if cfg.standing.reasoning_effort
                        else "[llm] reasoning_effort (inherited)"
                    ),
                ),
            ),
            objective=dreamer_instructions,
            max_chars=cfg.standing.max_chars,
            window=cfg.standing.window,
            timeout_seconds=cfg.standing.timeout_seconds,
            store_timeout_seconds=cfg.standing.store_timeout_seconds,
            rebuild_after_folds=cfg.standing.rebuild_after_folds,
        )
    # Step 3 of the correction loop (31 §3). Built only when the fact tier is
    # on: with `[facts]` off nothing is rendered, so there is nothing a member
    # could be contradicting and the ledger it reads is empty by construction.
    #
    # `adjudicator_alive` is 31 §7.2's gate. Clearing a dispute happens ONLY
    # inside adjudication, and adjudication is a Dreamer adaptor — so with the
    # Dreamer off, marking would accumulate and never resolve, and the loop
    # would be a ratchet rather than a loop. It degrades to proposing instead,
    # which is the mechanism latent scope already has.
    recogniser = (
        Recogniser(
            ledger=rendered_claims,
            facts=fact_store,
            lexemes=language_store,
            classifier=_LanguageModelClient(
                client=client, model=cfg.language.model,
                timeout_seconds=cfg.language.timeout_seconds,
            ),
            instruction=dreamer_instructions.objective_body(Recogniser.objective),
            metrics=metrics,
            window_hours=cfg.facts.recognition_window_hours,
            adjudicator_alive=cfg.dreamer.enabled,
        )
        if cfg.facts.enabled
        and fact_store is not None
        and rendered_claims is not None
        else None
    )
    engagement = EngagementPipeline(
        # EVERY built adapter, not just Signal's: a tag is answered on the
        # platform it arrived from, and `registry.adapters` is exactly the set
        # of platforms this process can both hear and speak on.
        adapters=dict(registry.adapters),
        audit=audit, memory=memory, persons=persons,
        doctrine_store=doctrine_store, context_assembler=assembler, runner=runner,
        recogniser=recogniser,
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
        tag_trace_store=tag_trace_store,
        debrief_wiring=debrief_wiring,
        # `[language].max_rendered_lexemes` — harmless to pass even when
        # `[language] enabled = false`: `language_store` is `None` there, so
        # `TranscriptInjection` never produces a non-empty `RoomLanguage` for
        # this cap to bound in the first place.
        room_language_max_entries=cfg.language.max_rendered_lexemes,
        room_exchange_max_entries=cfg.language.max_rendered_exchanges,
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
            room_language_max_entries=cfg.language.max_rendered_lexemes,
            room_exchange_max_entries=cfg.language.max_rendered_exchanges,
            # The reminder-derived Event registry (DEC-139,
            # design/event-abstraction/04): kind -> factory, wired here at
            # the composition root so the domain type stays free of app
            # wiring. Today the registry has exactly the two kinds whose
            # Events are PRODUCED BY a due Reminder row; compute/relay/
            # lost-job call speak directly and are not registry entries.
            event_registry={
                "reminder": reminder_occasion,
                "gathering": gathering_occasion,
            },
            # S5 (15-roadmap §6.3) — `<standing>` renders on the fire path
            # too. NOT "D5": that number is taken twice already (the identity
            # config decision, and Phase A's tool grant), and this one was
            # read off the PLAN's local operator-question table, which the
            # roadmap relabelled S1-S6 when it closed them. A fire turn
            # speaks into a room UNPROMPTED, so «where had this conversation
            # got to» is the context she is least likely to have any other
            # way: there is no tagging message to infer it from. Reads only;
            # the fire path writes no position, because S1 is blocked on it
            # retaining a `TagTrace` at all (see `FirePath.__init__`).
            standing=standing_store if cfg.standing.enabled else None,
            standing_store_timeout_seconds=cfg.standing.store_timeout_seconds,
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

    # ---- the Dreamer runtime (substrate plan Phase 2) --------------------
    #
    # Constructed only when `[dreamer] enabled` is true, the shape
    # `language_store` and `portrait_synthesizer` above already use: a
    # subsystem that is off builds NOTHING, so a wiring mistake in it cannot
    # cost a deployment that never turned it on.
    #
    # `dream_adaptors` is EMPTY today and that is the honest state: the runtime
    # exists, is bounded, screens, and has a sink contract — and nothing has
    # been migrated onto it yet (Phase 3) and the fact adaptors are not built
    # (Phase 4). `dream_loop` with an empty tuple is byte-for-byte the loop
    # that has been carrying live traffic, which is what makes the cutover
    # per-adaptor rather than a big-bang switch.
    dream_adaptors: tuple[DreamAdaptor, ...] = ()
    dreamer_runtime: DreamerRuntime | None = None
    if cfg.dreamer.enabled:
        # The fact tier's two adaptors, built only when BOTH gates are on.
        # `[dreamer]` decides whether the runtime exists; `[facts]` decides
        # whether this tier is one of the things it runs — two questions, and
        # an operator turning on the research loop has not thereby asked for a
        # fact store to start filling.
        if cfg.facts.enabled and fact_store is not None:
            # The roster is `identity.toml`'s person ids. It is what lets the
            # sink REFUSE a claim whose subject is somebody in the room (25
            # §2.4) — the objective's first exclusion, measured broken at five
            # person-claims in six edges with the roster supplied to the model.
            fact_candidates = SqlFactCandidateStore(
                engine,
                roster=frozenset(persons.by_person),
                # Every name the group calls them. `PersonRef.aliases` is
                # operator-authored (阿Miles, 米高, a handle) and its own note
                # says it must never resolve who a message is FROM — refusing
                # on one is the opposite direction and the safe one.
                roster_names=frozenset(
                    name
                    for ref in persons.by_person.values()
                    for name in (ref.name, ref.first_name, ref.last_name,
                                 *ref.aliases)
                    if name
                ),
                max_term_chars=cfg.facts.max_term_chars,
                max_predicate_chars=cfg.facts.max_predicate_chars,
                metrics=metrics,
            )
            fact_entities = SqlEntityStore(
                engine, veto_extra=load_veto_list(cfg.facts.veto_file),
                metrics=metrics,
            )
            dream_adaptors = (
                # PHASE A, scope-keyed: it reads one room's message batch.
                # `workspace_for` is not a convenience — the extractor's KEY is
                # a scope and the candidate table's key is a WORKSPACE, so
                # without the translation every room's candidates would land
                # under a workspace named after the room and the adjudicator
                # would never see one. The tier would be wired end to end and
                # silently inert. An unresolvable scope RAISES rather than
                # guessing: within one workspace a fact crosses rooms by design
                # (R-49/DEC-105), so a guessed workspace is one social unit's
                # claims entering another's.
                FactCandidateAdaptor(
                    candidates=fact_candidates,
                    workspace_for={
                        conv.primary_id: conv.workspace
                        for conv in allowlist.conversations
                        if conv.primary_id and conv.workspace
                    }.get,
                    max_term_chars=cfg.facts.max_term_chars,
                    max_predicate_chars=cfg.facts.max_predicate_chars,
                ),
                # PHASE B, workspace-keyed and watermark-free: its input is the
                # candidate table, not a message batch.
                FactAdaptor(
                    facts=fact_store,
                    candidates=fact_candidates,
                    max_adjudications_per_tick=cfg.facts.max_adjudications_per_tick,
                    max_term_chars=cfg.facts.max_term_chars,
                    max_predicate_chars=cfg.facts.max_predicate_chars,
                    # Pass 3 rides the adjudication tick: degree changes only
                    # when a fact lands, so this is exactly when there is
                    # something new to promote.
                    entities=fact_entities,
                    metrics=metrics,
                ),
                # PASS 6, workspace-keyed and watermark-free like the
                # adjudicator: its input is the entity table and the candidate
                # queue, not a message batch. It is the half of resolution that
                # needs judgement (21 §5); the deterministic half already ran
                # in the sink on every write.
                FactEntityResolutionAdaptor(
                    entities=fact_entities, metrics=metrics,
                ),
            )
        # The note tier's REVISITOR, on the same two-gate rule the fact tier
        # above is held to: `[dreamer]` decides whether the runtime exists,
        # `[notes]` decides whether this tier is one of the things it runs.
        # ADDITIVE to whatever the fact block built rather than replacing it —
        # the two tiers are independent and an operator may run either alone.
        #
        # It takes `audit` for its evidence read, and that is the whole reason
        # this adaptor needs no room-scoped TOOL: the evidence is selected in
        # code, from the note's own `established_in`, where it can be capped and
        # counted (§4.2's argument). A workspace-keyed adaptor handed a
        # room-scoped tool would get a workspace where a conversation id is
        # expected and be answered EMPTY — «I could not check» reported as
        # «there was nothing», which for this adaptor would mean retiring a note
        # against evidence that was never read.
        if cfg.notes.enabled and note_store is not None:
            dream_adaptors = (*dream_adaptors, NoteRevisitor(
                notes=note_store,
                record=audit,
                max_revisits_per_tick=cfg.notes.max_revisits_per_tick,
                evidence_messages_per_note=cfg.notes.revisit_evidence_messages,
                metrics=metrics,
            ))
        # The note tier's curator, on its OWN gate and behind the tier's.
        # `[notes] enabled` decides whether the index is injected and the four
        # tools reachable; `curator_enabled` decides whether a background pass
        # MERGES AND RETIRES. Two switches for two risks: the tag path only
        # reads, and this is the one thing in the tier that removes something
        # from her index.
        #
        # THE GROUP'S calendar day, not UTC and not the store's clock. The
        # curator compares against a YYYYMMDD day, and "has the 9th passed" is
        # a question about the room's day rather than the process's: a UTC
        # boundary retires the 9th's note during the evening of the 9th, local,
        # while people are still deciding where to eat.
        #
        # Read from `[reminders] timezone` rather than written here. That value
        # is operator-owned and already carries the argument for being an IANA
        # NAME rather than an offset; a second copy in this file is the
        # unguarded-copy class — it stays plausible after the original moves and
        # fails somewhere else later. Reminders and note expiry are the same
        # question («what day is it, for these people»), so they should not be
        # able to disagree.
        if cfg.notes.enabled and cfg.notes.curator_enabled and note_store is not None:
            def _today_hk() -> int:
                now = datetime.now(ZoneInfo(cfg.reminders.timezone))
                return now.year * 10000 + now.month * 100 + now.day

            dream_adaptors = (
                *dream_adaptors,
                NoteCuratorAdaptor(
                    notes=note_store,
                    today=_today_hk,
                    max_groups_per_tick=cfg.notes.max_groups_per_tick,
                    max_expired_per_tick=cfg.notes.max_expired_per_tick,
                    metrics=metrics,
                ),
            )
        # A SECOND `ModelSource`, not the tag path's. `tool_choice` lives on
        # `ModelSpec`, and the Dreamer needs `"auto"`: under `"required"` the
        # model must call something on every pass, and the only thing that ends
        # a turn is a guard observing a TERMINAL call (DEC-21/DEC-32) — which
        # the Dreamer has none of by construction. Its loop ends when the model
        # declines to call anything, inside a wall clock.
        dream_model = cfg.dreamer_model()
        dream_models = factory.ModelSource(
            client=client,
            spec=factory.ModelSpec(
                model=dream_model, max_tokens=tier.max_tokens,
                temperature=tier.temperature,
                reasoning_effort=tier.reasoning_effort,
                tool_choice=tool_choice.AFTER_SPEAKING,
            ),
        )
        # ONE tool source, shared by the runtime and the startup check below —
        # two would be two ceilings, and the check would be measuring something
        # other than what the runtime enforces.
        dream_tools = RegistryToolSource(
            registry=tool_registry, store=audit, persons=persons,
            honcho=memory, metrics=metrics, ai_peer=cfg.memory.ai_peer,
        )
        # `DreamBudgetConfig` -> `AdaptorBudget`: the same four numbers, across
        # the `nora.agent` may-not-import-`nora.config` boundary. This file is
        # one of only two allowed to read config, so THIS is the conversion
        # point — a field added to one and not the other is a `TypeError` here,
        # at startup, which is the failure worth having.
        dream_budgets = {
            name: AdaptorBudget(
                turns=b.turns, total_tokens=b.total_tokens,
                output_tokens=b.output_tokens, timeout_seconds=b.timeout_seconds,
            )
            for name, b in cfg.dreamer.budgets.items()
        }
        # An adaptor with no block of its own. `budget_for` returns a freshly
        # constructed default rather than a shared constant (DEC-13 forbids a
        # module-level instance), so an adaptor added later is BOUNDED by
        # omission rather than unbounded by it.
        fallback = cfg.dreamer.budget_for("")
        dreamer_runtime = DreamerRuntime(
            models=dream_models,
            instructions=dreamer_instructions,
            tools=dream_tools,
            budgets=dream_budgets,
            default_budget=AdaptorBudget(
                turns=fallback.turns, total_tokens=fallback.total_tokens,
                output_tokens=fallback.output_tokens,
                timeout_seconds=fallback.timeout_seconds,
            ),
            # The SAME derivation the tag path uses, so a throttle can never be
            # misreported as a timeout (`factory.build_retry_policy`).
            retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
            metrics=metrics,
            model_name=dream_model,
        )
        # R-5's refuse-to-start shape, and the one check that must run at
        # STARTUP rather than at the first tick: an adaptor holding an act verb
        # is not a condition to discover at 3am from a metric. Empty today, so
        # it refuses nothing — which is exactly why it has to be wired now,
        # while adding the first adaptor is the moment it starts mattering.
        verify_tool_subsets(dream_adaptors, tools=dream_tools)

    return Dependencies(
        rendered_claims=rendered_claims,
        audit=audit, memory=memory, persons=persons, metrics=metrics,
        policy=policy, assembler=assembler, ingest=ingest, engagement=engagement,
        dispatcher=dispatcher, adapter=adapter, registry=registry,
        account=account, doctrine=doctrine,
        doctrine_store=doctrine_store, debounce=debounce, background=background,
        llm_state=llm_state, client=client, reminders=reminders,
        games_client=games_client,
        presence=presence_store, presence_circles=presence_circles,
        skills_dir=cfg.doctrine.dir.parent / "skills",
        language_store=language_store, language_consolidators=language_consolidators,
        language_scopes=language_scopes,
        portrait_synthesizer=portrait_synthesizer,
        dreamer_instructions=dreamer_instructions,
        dreamer_runtime=dreamer_runtime,
        dream_adaptors=dream_adaptors,
        workspace_scopes=workspace_scopes,
        events=events,
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


async def _build_media_custody(
    cfg: Config,
) -> tuple[MediaSourceRegistry | None, BlobStore | None]:
    """The `(sources, blobs)` pair the ingest pipeline takes for byte custody, or
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
    sources = MediaSourceRegistry([
        SignalMediaSource(
            base_url=cfg.platforms.signal.base_url,
            timeout_seconds=media.timeout_seconds,
            max_bytes=media.max_bytes,
        ),
        DiscordMediaSource(
            timeout_seconds=media.timeout_seconds,
            max_bytes=media.max_bytes,
        ),
    ])
    blobs = await _build_blob_store(cfg, cipher=cipher)
    return sources, blobs


async def _build_blob_store(cfg: Config, *, cipher: BlobCipher) -> BlobStore:
    """The filesystem default, or RustFS when `[media.rustfs]` is on and its
    credentials resolve (DEC-136).

    A misconfigured RustFS backend — `enabled = true` with a blank endpoint,
    or an `access_key_env`/`secret_key_env` that names an unset variable —
    degrades to the filesystem default rather than to custody OFF, the same
    posture `_build_media_custody`'s own encryption-key handling takes: the
    existing, always-working behaviour is the fallback for a bonus feature,
    never a startup failure and never a silent capability loss beyond the one
    that was already being configured.
    """
    rustfs = cfg.media.rustfs
    fallback = FilesystemBlobStore(cfg.store.data_dir / "media", cipher=cipher)
    if not rustfs.enabled:
        return fallback
    if not rustfs.endpoint:
        log.warning(
            "media.rustfs.enabled is true but media.rustfs.endpoint is unset — "
            "falling back to the filesystem blob store."
        )
        return fallback
    access_key = os.environ.get(rustfs.access_key_env, "")
    secret_key = os.environ.get(rustfs.secret_key_env, "")
    if not access_key or not secret_key:
        log.warning(
            "media.rustfs is on but %s/%s did not both resolve in the "
            "environment — falling back to the filesystem blob store.",
            rustfs.access_key_env, rustfs.secret_key_env,
        )
        return fallback
    s3 = boto3.client(
        "s3", endpoint_url=rustfs.endpoint,
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )
    store = RustfsBlobStore(s3_client=s3, bucket=rustfs.bucket, cipher=cipher)
    try:
        await asyncio.to_thread(store.ensure_bucket)
    except Exception:
        # RustFS unreachable, or misconfigured beyond what the checks above
        # catch (wrong credentials, wrong bucket permissions). This runs at
        # STARTUP, before build_dependencies' own per-message protection
        # (nora.ingest.stages.media._take already degrades a `put` failure to
        # `store_failed` per message) exists — an unhandled exception HERE
        # would crash the whole daemon over a bonus feature, exactly what
        # this section's own "must never take the daemon down" rule forbids.
        log.exception(
            "media.rustfs is configured but the bucket could not be reached "
            "or created — falling back to the filesystem blob store."
        )
        return fallback
    return store


def _note_workspace_of(policy: Policy) -> Callable[[str, str], str | None]:
    """`(platform, conversation_id)` → the room's workspace, or `None`.

    A free function rather than a lambda so the `None` branch is nameable: a
    room `policy.toml` does not list has no workspace, and the note verbs must
    answer `unavailable` for it. Guessing one — defaulting to "nora", or to
    whichever workspace happens to be first — would write a note into a
    notebook the room is not part of, and `standing` would then render it to
    people who never saw the conversation it came from.
    """

    def _resolve(platform: str, conversation_id: str) -> str | None:
        convo = policy.conversation(platform, conversation_id)
        return convo.workspace if convo is not None else None

    return _resolve
