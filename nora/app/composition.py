"""The ONE place that reads Settings. Everything else receives dependencies.

A stage never sees Config; it receives a RecordStore, a MemoryStore, a Persons.
That is what makes stages testable in isolation and what stops the god object
from reforming. Enforced by import-linter (DEC-12).

`build_dependencies` is the whole composition root: it owns the ordered, fatal
startup validation (`StartupError`, one `except` clause per fallible step) and
builds the full object graph: `audit`/`memory`/`persons`/`metrics`, the
`EngagePolicy`, the `ContextAssembler`, both pipelines, the `Dispatcher` that
glues them, and the runtime pieces (`BackgroundTasks`, `Debounce`).
`nora.__main__.build_app` has exactly three steps: load config, await this
function, construct `App`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from collections.abc import Awaitable, Callable, Collection, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from zoneinfo import ZoneInfo

import boto3
from openai import AsyncOpenAI
from openai.types.shared.reasoning_effort import ReasoningEffort
from opentelemetry.util.types import AttributeValue

from nora.agent import factory, tool_choice
from nora.agent.browse import BrowseAgentRunner, BrowseSettings
from nora.agent.dream_screen import offence as dream_offence
from nora.agent.dream_screen import text_offence
from nora.agent.dreamer import (
    TOOL_RESULT_CAPS,
    AdaptorBudget,
    AdaptorGrant,
    AdaptorSchedule,
    DreamerRuntime,
    HomeRoom,
    RegistryToolSource,
    culture_grant,
    verify_tool_subsets,
)
from nora.agent.egress import scan_identifiers, scan_reply
from nora.agent.mcp import McpMount, build_mcp_tools
from nora.agent.memory_search import MemorySearch
from nora.agent.registry import ToolRegistry
from nora.agent.relay import Relay
from nora.agent.room import Room
from nora.agent.structured import ask_text, build_structured_model
from nora.agent.subagent_desk import SubagentDesk
from nora.agent.telemetry import (
    end_raw_model_span,
    end_raw_model_span_with_error,
    start_raw_model_span,
)
from nora.agent.telemetry import timed as telemetry_timed
from nora.agent.tools import COMPUTER_TOOLS, LOAD_TRANSCRIPT_MAX
from nora.agent.video_desk import VideoDesk
from nora.agent.whereis import Circle, check_disclosure_invariant, circles_from_persons
from nora.app.cognition_grounding import (
    build_grounder,
    place_release,
    stay_context_reader,
    unit_self_persona_reader,
    unit_self_reader,
)
from nora.app.cognition_session import CognitionSessionRunner
from nora.app.cognition_wiring import (
    COGNITION_ADAPTOR_NAME,
    SESSION_SHUTDOWN_GRACE_SECONDS,
    CognitionShutdown,
    PreludeMissingError,
    SkillNotInstalledError,
    build_cognition_runner,
    cognition_budget,
    open_events_reader,
    read_surface,
    require_session_hook,
    units_with_home_rooms,
    upcoming_reader,
)
from nora.app.cognition_wiring import (
    agenda_skills as cognition_agenda_skills,
)
from nora.app.computer_metrics import ComputerMetrics
from nora.app.computer_sweeper import ComputerSessionSweeper, SweepRules
from nora.app.documents_service import DocumentService, room_label, rooms_named
from nora.app.memory_routing import (
    MemoryRoutingError,
    build_workspace_router,
    workspaces_of_person,
)
from nora.app.oauth_keepalive import KeepaliveTiming, KeptLane, OAuthKeepalive
from nora.app.otp import OtpService
from nora.app.social_expiry import StoryMediaExpiry
from nora.app.social_perception import SocialPerception
from nora.app.social_poller import SocialPoller
from nora.app.source_health import AdapterHealthRecorder
from nora.app.subagent_backend import SubagentBackend
from nora.app.subagent_wiring import build_subagent_backend
from nora.app.summons_inbox import SummonsInbox
from nora.app.voice_events import VoiceEventConsumer
from nora.app.wake import Floor, WakeEvaluator
from nora.config import Config, DreamBudgetConfig
from nora.doctrine.loader import Doctrine, DoctrineError, DoctrineStore
from nora.domain.compute import ComputeJobStore
from nora.domain.computer import ComputerToolSettings
from nora.domain.computer_context import ComputerContextCaps
from nora.domain.computer_events import ComputerEventStore
from nora.domain.computer_exports import ComputerExportStore
from nora.domain.computer_leases import ComputerLeases
from nora.domain.computer_sessions import ComputerSessionStore
from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.decision_log import DecisionLogRecorder
from nora.domain.documents import DocumentStore, RoomKey
from nora.domain.media_join import MediaJoin
from nora.domain.ports import (
    BlobStore,
    ComputeBackend,
    ContextAssembler,
    CultureReader,
    DiscordPresenceSource,
    DreamAdaptor,
    EngagePolicy,
    ExternalFilesSource,
    ImageGenerator,
    MemoryStore,
    PlatformAdapter,
    RecordStore,
    ReverseGeocoder,
    RosterSource,
    SandboxRuntime,
    SearchProvider,
    SocialProvider,
    Speaker,
    SteamPresenceProvider,
    VoicePresenceSource,
)
from nora.domain.quote_window import QuoteWindow
from nora.domain.reminders import ReminderCaps, ReminderStore
from nora.domain.speech_markup import normalise as normalise_for_speech
from nora.domain.stimuli import WakeHook, WakePolicy
from nora.domain.subagent import SUBAGENT_KIND
from nora.domain.tool_call_log import ToolCallLogStore
from nora.domain.video import VIDEO_KIND
from nora.engagement.computer_context import ComputerContextReader
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.self_presence import PresenceReader
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages.debrief import Completion as DebriefCompletion
from nora.engagement.stages.debrief import Wiring as DebriefWiring
from nora.engagement.subagents_context import SubagentsContextReader
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.computer.probe import ComputerdProbe
from nora.infra.embedder import QUERY_TIMEOUT_SECONDS, LlamaEmbedder, fingerprint, model_file
from nora.infra.google.auth import GoogleCredentials
from nora.infra.honcho import HonchoClient
from nora.infra.music import YtDlpSource
from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.cognition_store import SqlCognitionStore
from nora.infra.sql.compute_jobs import SqlComputeJobStore
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.computer_exports import SqlComputerExportStore
from nora.infra.sql.computer_sessions import SqlComputerSessionStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.decision_log import SqlDecisionLogStore
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.dream_schedule import SqlDreamScheduleClock
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.media_descriptions import SqlMediaDescriptionStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_embed import MemoryEmbedWorker
from nora.infra.sql.memory_read import SqlMemoryIndex
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.infra.sql.music_session import SqlMusicSessionStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.otp import SqlOtpStore
from nora.infra.sql.participation_state import SqlParticipationStateStore
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.reasoning_strip import SqlReasoningStripStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.revisions import SqlRevisionStore
from nora.infra.sql.rollup_read import SqlRollupRead
from nora.infra.sql.social_stream import SqlSocialStreamStore, readable_social_store
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.infra.sql.standing import SqlStandingStore
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.infra.sql.tag_debounce import SqlTagDebounceStore
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.infra.sql.tension import SqlTensionStore
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.infra.sql.video_readings import SqlVideoReadingStore
from nora.infra.tts import TextFormatter as SpeechTextFormatter
from nora.infra.version import VersionManifest
from nora.infra.vision import MediaComprehender, VisionClient
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.ingest.comprehension import MediaComprehension
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import (
    CompositePolicy,
    MentionPolicy,
    RelevanceGatePolicy,
    ReplyToNoraPolicy,
    TriggerTextPolicy,
    VoiceNotePolicy,
    VoiceWakePolicy,
)
from nora.ingest.relevance_gate import Completion as GateCompletion
from nora.ingest.relevance_gate import RelevanceGate
from nora.ingest.stages import stimulus as stimulus_stage
from nora.ingest.stickers import StickerReader
from nora.instructions.dreamer import DreamerInstructions
from nora.instructions.frontmatter import InstructionError
from nora.language.character import (
    CHARACTER_NORA_ADAPTOR_NAME,
    CHARACTER_PERSON_ADAPTOR_NAME,
    CHARACTER_UNIT_ADAPTOR_NAME,
    CharacterNoraAdaptor,
    CharacterPersonAdaptor,
    CharacterRoom,
    CharacterUnitAdaptor,
    NoraEvidence,
    NoraKey,
    PersonEvidence,
    PersonKey,
    UnitEvidence,
    UnitKey,
)
from nora.language.cognition_render import RenderBudget
from nora.language.culture import CULTURE_ADAPTOR_NAME, CultureAdaptor
from nora.language.doctrine_check import CHECK_OBJECTIVE, DoctrineCheck
from nora.language.lesson_book import LessonBook
from nora.language.lesson_render import ActiveLessons
from nora.language.lessons import (
    LESSONS_ADAPTOR_NAME,
    LessonEvidence,
    LessonsAdaptor,
    LessonsKey,
)
from nora.language.persona import (
    PERSONA_PERSON_ADAPTOR_NAME,
    PersonaEvidence,
    PersonaPersonAdaptor,
)
from nora.language.persona_collective import (
    PERSONA_COLLECTIVE_ADAPTOR_NAMES,
    PERSONA_NORA_ADAPTOR_NAME,
    PERSONA_ROOM_ADAPTOR_NAME,
    PERSONA_UNIT_ADAPTOR_NAME,
    CollectiveEvidence,
    CollectiveKey,
    PersonaCollectiveAdaptor,
)
from nora.language.persona_render import PersonaHeads
from nora.language.persona_verdict import (
    PERSONA_VERDICT_ADAPTOR_NAME,
    DocketAccount,
    PersonaVerdictAdaptor,
)
from nora.obs.health import LLMState
from nora.obs.metrics import Metrics
from nora.persons import DIRECT_PLATFORMS, Persons, PersonsError, load_persons
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_tool_bridge import DiscordMusicToolBridge
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.instagram.media import InstagramMediaSource
from nora.platform.registry import AdapterRegistry, AdapterRegistryError, MediaSourceRegistry
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.media import SignalMediaSource
from nora.platform.signal.roster import SignalRoster
from nora.policy import Policy, PolicyError, load_policy
from nora.tag import Debounce
from nora.web.games import PULSE_API_BASE, PulseGamesClient

from .agent_runner import LiveAgentRunner
from .assembler import build_assembler, lineage_policy
from .compute_poller import ComputeJobPoller
from .compute_poller import recover as compute_poller_recover
from .dispatch import Dispatcher
from .fire import (
    FirePath,
    lost_job_occasion,
    proactive_occasion,
    relay_occasion,
    reminder_occasion,
)
from .gatherings import GatheringService, room_event_shown, screened_area
from .heartbeat import HeartbeatLoop, publish_idle_gauge, recover
from .participation import ParticipationTrigger
from .proactive_screen import ProactiveScreen
from .runtime import BackgroundTasks

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("nora.app.composition")


class StartupError(Exception):
    """A fatal startup validation failure — refuse to start, with a readable
    message. Each site below wraps one operator-diagnosable condition."""


_REASONING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def _unit_room_ids(allowlist: Policy) -> dict[str, tuple[str, ...]]:
    """Unit -> its rooms' primary ids, from `policy.toml`."""
    out: dict[str, tuple[str, ...]] = {}
    for conv in allowlist.conversations:
        out[conv.unit] = (*out.get(conv.unit, ()), conv.primary_id)
    return out


def _presence_reader(
    cfg: Config, allowlist: Policy, registry: AdapterRegistry,
    rosters: Mapping[str, RosterSource] | None = None,
) -> PresenceReader:
    """The one `PresenceReader`.

    Her handles come from two places and the order matters: `[agent.handles]`
    is the DECLARED fallback, and any adapter that can report its own handle
    overrides it. Instagram's `own_handle` reads its bridge's `/health` live,
    per call. Discord's reuses the ONE `/users/@me` response
    `resolve_identity()` fetched at startup instead of a second call per turn
    (`DiscordAdapter.own_handle` answers from that cache). Signal derives an id
    and drops the username by design, and WhatsApp's bridge exposes none, so
    theirs stay declared until an adapter can say.
    """
    live: dict[str, Callable[[], Awaitable[str | None]]] = {}
    instagram = registry.adapters.get("instagram")
    if isinstance(instagram, InstagramAdapter):
        live["instagram"] = instagram.own_handle
    discord_adapter = registry.adapters.get("discord")
    if isinstance(discord_adapter, DiscordAdapter):
        live["discord"] = discord_adapter.own_handle
    return PresenceReader(
        policy=allowlist, adapters=dict(registry.adapters),
        handles=cfg.agent.handles, live_handles=live,
        # The room tools' member counts: the SAME
        # roster map `Room` reads, and her own ids so she is never counted as
        # a member of a room she is in. `<presence>` reads neither.
        rosters=rosters, self_ids=registry.self_aliases(),
    )


def _reasoning_effort(value: str, *, source: str) -> ReasoningEffort:
    """Narrow a config string to the provider's closed set, or refuse to start.

    `[standing] reasoning_effort` is already typed as this set, so it cannot
    arrive wrong. `[llm] reasoning_effort` — which it INHERITS when empty — is
    a bare `str` and can: a typo there reaches the provider and comes back a
    400 during a fold, inside the conversation's slot, on a turn that had
    already replied.

    Narrowed HERE rather than by retyping `LLMConfig`, which is the tag path's
    own field with its own callers. Gives the boot-failure property for the
    debrief's use of it.
    """
    if value not in _REASONING_EFFORTS:
        raise StartupError(
            f"{source} = {value!r} is not a reasoning effort the provider "
            f"accepts ({', '.join(sorted(_REASONING_EFFORTS))}) — refusing to "
            f"start rather than failing on the first call that uses it"
        )
    return cast(ReasoningEffort, value)


def _require_a_sendable_public_url(public_url: str) -> None:
    """Refuse to start when egress would block the link she is told to send.

    `[documents] public_url` is validated as an origin, but an origin can still be one the
    identifier scan blocks whole (a UUID-shaped, `+digits` or key-shaped host): she would be
    told to paste the link verbatim and every such reply would be swallowed. The scan used
    is egress's own, on the longest link the handle can make, so nothing is duplicated."""
    if not public_url:
        return
    link = f"{public_url}/documents/{'0' * 12}"
    kind = scan_reply(link)
    if kind is not None:
        raise StartupError(
            f"[documents] public_url = {public_url!r}: egress would block the link she "
            f"sends ({kind}) — refusing to start rather than silently dropping every reply "
            f"that carries a document link"
        )


def _build_wake_evaluator(
    cfg: Config, *, store: SqlStimulusStore, units: Callable[[], Sequence[str]],
    metrics: Metrics, on_wake: WakeHook | None,
    session_units: Callable[[], Collection[str]] | None = None,
    floors_ready: bool = True,
    upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None,
) -> WakeEvaluator:
    """The cognition loop's wake evaluator: every
    wake starts a session through `on_wake`."""
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


def _adaptor_budget(b: DreamBudgetConfig) -> AdaptorBudget:
    """`DreamBudgetConfig` -> `AdaptorBudget`: the one place the two shapes meet.

    They are two types carrying the same numbers because `nora.agent` may not
    import `nora.config` (the layer contract), and this file is one of only two
    allowed to read config — so the conversion has to happen here. Some fields
    (`max_memory_calls`) are DEFAULTED on both sides, so a site that forgot one
    would not raise a `TypeError`; it would quietly hand the runtime a number
    the operator did not choose.

    One function serves every site, and `tests/unit/test_dream_memory_budget.py`
    walks `DreamBudgetConfig`'s own field list to assert every one of them
    arrives.
    """
    return AdaptorBudget(
        turns=b.turns,
        total_tokens=b.total_tokens,
        output_tokens=b.output_tokens,
        timeout_seconds=b.timeout_seconds,
        max_memory_calls=b.max_memory_calls,
        # Copied, not aliased: `DreamBudgetConfig` is frozen but its `dict`
        # field is not, and an `AdaptorBudget` handed the same object would
        # share it with the config tree for the process's life.
        tool_calls=dict(b.tool_calls),
        max_tool_calls=b.max_tool_calls,
    )




class _RelevanceGateModelClient:
    """Satisfies `nora.ingest.relevance_gate.ModelClient` — ONE RAW
    `json_object` COMPLETION.

    **The gate is the exception `nora/agent/structured.py` warns about.** Its
    verdict decides whether Nora may speak into a room she was not invited
    into, so it does not go through the SDK's `structured_output`: Strands
    converts the schema into a TOOL SPECIFICATION and feeds the result back, so
    one logical call becomes three messages, and against OmniRoute's Chat
    Completions transport the SDK drops the assistant turn's reasoning before
    the verdict is formed. A member who can forge Nora's side of a transcript
    (a `[assistant]:` row inside a message) would then steer the admission
    decision, which is an unauthenticated turn. The gate is
    `tests/architecture/test_the_gate_does_not_form_its_verdict_through_the_sdk.py`.

    The instruction goes in the SYSTEM role and the room in the USER role.

    **`tool_choice` is not declared here and must not be.** A raw `json_object`
    completion sends no tools at all, so there is nothing for a tool_choice to
    select and the field never reaches the wire. The shape is
    `tests/offline_eval_support.py::_MeasuringGateClient`'s, argument for
    argument, with one difference: the eval's client sends `NO_CACHE_HEADERS`
    and this one sends no such header. Eval readings are therefore
    cache-bypassed; a comparison between eval arms holds, but an absolute eval
    reading does not transfer to production unqualified.

    What a raw client owes
    (`tests/architecture/test_raw_model_calls_carry_their_own_machinery.py` is
    the gate): an explicit `asyncio.wait_for`, because the bare `AsyncOpenAI`
    default is 600s x retries and this call runs while the conversation's slot
    is HELD, so an unbounded one is a room that stops answering; a
    `finish_reason` read, because a raw completion does not raise on a cut-off
    the way `MaxTokensReachedException` does, it returns `"length"` and a
    silently truncated body that can still parse as JSON into something shorter
    and wrong; and a usage annotation, because Strands emits `gen_ai.usage.*`
    itself and a raw call emits nothing, so without it the call exports to
    Langfuse costing zero.

    It PINS `reasoning_effort` rather than leaving it to the route: a setting
    left to the route is a setting nobody chose. `max_tokens` and
    `temperature` are the gate's own — a gate with room to write paragraphs
    has room to start drafting the reply it must never draft — and its model
    is its OWN combo (`nora-gate`), never the tag lane's.
    """

    def __init__(
        self, *, client: AsyncOpenAI, model: str, max_tokens: int,
        temperature: float, timeout_seconds: float,
        reasoning_effort: ReasoningEffort,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._reasoning_effort = reasoning_effort

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, *, system: str, prompt: str) -> GateCompletion:
        # A span of its own — `start_raw_model_span`/`end_raw_model_span`
        # (`nora.agent.telemetry`, thin wrappers around Strands'
        # `Tracer.start_model_invoke_span`/`end_model_invoke_span`) — nested
        # under whatever `nora.ingest.relevance_gate.decide` has open
        # (a plain `span()`, not a `generation()`: the decision procedure
        # itself is not one model call). The docstring's constraints bind
        # `messages`/`response_format`/`reasoning_effort` below.
        span = start_raw_model_span(
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            model_id=self._model, system_prompt=system,
        )
        with telemetry_timed() as elapsed:
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            # The instruction in the SYSTEM role, the room in
                            # the USER role. Concatenated into one user
                            # string, a member typing `[assistant]:` would sit
                            # in exactly the role the operator's rules
                            # occupy, and nothing downstream could tell them
                            # apart.
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=self._max_tokens,
                        temperature=self._temperature,
                        reasoning_effort=self._reasoning_effort,
                    ),
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:
                end_raw_model_span_with_error(
                    span=span, error_message=str(exc) or type(exc).__name__,
                    exception=exc,
                )
                raise
            latency_seconds = elapsed()
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        # `or ""` and not a `"stop"` default: a provider that omits the
        # field has told us nothing, and inventing `"stop"` would assert
        # the call completed.
        finish_reason = choice.finish_reason or ""
        usage = getattr(response, "usage", None)
        end_raw_model_span(
            span=span, content=content, finish_reason=finish_reason,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            latency_seconds=latency_seconds,
        )
        return GateCompletion(content=content, finish_reason=finish_reason)


class _DebriefModelClient:
    """Satisfies `nora.engagement.stages.debrief.ModelClient` — a raw one-shot
    text completion over the process-wide OmniRoute client, with three things
    on it that every other raw `chat.completions.create` in this repo needs.

    **`finish_reason` comes back with the content.** A raw
    `chat.completions.create` does not raise on a cut-off the way a Strands
    agent's `MaxTokensReachedException` does — it returns
    `finish_reason="length"` and a **silently truncated body**, which can still
    parse as JSON into something shorter and wrong. That is the R-27
    substitution (an ERROR reported as a PASS) one layer down from where this
    repo usually enforces it, and the only way to keep it out is to hand the
    caller the field it must check.

    **`response_format={"type": "json_object"}`**, because prompt-only JSON
    enforcement is insufficient: nothing on the wire would ask the provider to
    honour it.

    **An explicit `asyncio.wait_for`**, because the bare `AsyncOpenAI` default
    is 600s x retries — and this call HOLDS THE CONVERSATION'S SLOT, so an
    unbounded one is not a slow background job, it is a room that stops
    answering.
    """

    def __init__(
        self, *, base_url: str, api_key: str, model: str, timeout_seconds: float,
        reasoning_effort: ReasoningEffort, max_tokens: int, temperature: float,
        agent_name: str = "debrief",
    ) -> None:
        self._model_id = model
        self._timeout_seconds = timeout_seconds
        # `agent_name` becomes the Strands agent span (`invoke_agent <name>`)
        # inside `ask_text` — without it every debrief/standing-fold Strands
        # agent falls back to the SDK default "Strands Agents", so the call
        # under `engagement.debrief.fold` would be indistinguishable from any
        # other unnamed agent.
        self._agent_name = agent_name
        self._provider = build_structured_model(
            base_url=base_url, api_key=api_key, model_id=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=str(reasoning_effort),
        )

    @property
    def model(self) -> str:
        return self._model_id

    async def complete(
        self, *, system: str, prompt: str,
        trace_attributes: Mapping[str, AttributeValue] | None = None,
    ) -> DebriefCompletion:
        got = await ask_text(
            model=self._provider, prompt=prompt,
            timeout_seconds=self._timeout_seconds, system_prompt=system,
            agent_name=self._agent_name, trace_attributes=trace_attributes,
        )
        # No usage annotation here: this is a real Strands `Agent`
        # invocation (`ask_text`), and Strands emits `gen_ai.usage.*` on its
        # own model-invoke span itself.
        return DebriefCompletion(
            content=got.content, finish_reason=got.finish_reason,
        )


def _speech_formatter(
    cfg: Config, prompts: DoctrineStore, metrics: Metrics,
) -> SpeechTextFormatter | None:
    """`[speech.tts.format]`'s formatter, or None.

    `off` is None, so the speaker sends her reply exactly as written. The
    `model` mode's client is the reaction picker's shape, `_DebriefModelClient`
    over the same OmniRoute base URL: one `ask_text` call, its own Strands
    agent span (`invoke_agent speech-format`) under the turn that is speaking.
    """
    fmt = cfg.speech.tts.format
    if fmt.mode == "off":
        return None
    if fmt.mode == "normalise":
        return _NormaliseOnly()
    from nora.agent.speech_format import SpeechFormatter
    return SpeechFormatter(
        client=_DebriefModelClient(
            base_url=cfg.llm.base_url,
            api_key=os.environ.get(cfg.llm.api_key_env, ""),
            max_tokens=fmt.max_tokens,
            temperature=0.0,
            model=fmt.model or cfg.llm.model,
            # The client's own bound sits above the formatter's so the
            # formatter's `wait_for` is the one that fires and is counted.
            timeout_seconds=fmt.timeout_seconds + 1.0,
            reasoning_effort=_reasoning_effort(
                fmt.reasoning_effort or cfg.llm.reasoning_effort,
                source="[speech.tts.format] reasoning_effort",
            ),
            agent_name="speech-format",
        ),
        prompts=prompts, metrics=metrics,
        timeout_seconds=fmt.timeout_seconds,
        min_chars=fmt.min_chars, max_chars=fmt.max_chars,
    )


class _NormaliseOnly:
    """`[speech.tts.format] mode = "normalise"`: the deterministic half only,
    no model call (`nora.domain.speech_markup.normalise`)."""

    async def format(self, *, text: str) -> str:
        return normalise_for_speech(text)


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


def _describe_providers(cfg: Config) -> dict[str, str]:
    """Modality → the provider its describer travels through.

    The same enable+model gate as `_describe_models` — a modality only appears
    when it can actually describe — so the two maps cannot disagree about which
    modalities are live. The provider DEFAULT is `"omniroute"` everywhere: an
    unset provider describes through the process-wide gateway client. Only a
    modality whose config names a DIRECT provider (`"kimi"`, `"opencode-go"`)
    routes direct.
    """
    resolved: dict[str, str] = {}
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if describer is not None and describer.enabled and describer.model:
            resolved[modality] = describer.provider
    return resolved


@dataclass(frozen=True)
class _DirectLane:
    """One DIRECT describer transport: where it goes, and whose key it reads.

    `session_header` is OpenCode Go's mandatory `x-opencode-session` (DEC-197):
    its endpoint answers `400 MissingSessionID` without one, so its client is
    built on `opencode_go_http_client`, which stamps a fresh id per request.
    """

    base_url: str
    default_key_env: str
    session_header: bool = False


# Provider name (`DescriberConfig.provider`) → its direct lane. `"omniroute"`
# is NOT here: it is the process-wide gateway client, built once above for
# every model call, not a describer lane.
_DIRECT_LANES = {
    # OmniRoute drops `video_url` content blocks, so video
    # needs a lane of its own; Kimi's endpoint serves K3 with base64 video.
    "kimi": _DirectLane(
        base_url="https://api.kimi.com/coding/v1",
        default_key_env="KIMI_CODE_API_KEY",
    ),
    # MiMo v2.6 Flash, which Kimi's endpoint does not serve and OmniRoute
    # cannot carry video to.
    "opencode-go": _DirectLane(
        base_url="https://opencode.ai/zen/go/v1",
        default_key_env="OPENCODE_GO_API_KEY",
        session_header=True,
    ),
}


def _direct_lane_key_env(cfg: Config, provider: str) -> str:
    """The env var name `provider`'s direct lane reads its key from.

    `DescriberConfig.api_key_env` NAMES the variable (DEC-8 — the value never
    lands in config.toml); empty falls back to the lane's own default exactly as
    the field's docstring promises. When several modalities select the same
    provider, the first enabled one's name wins — one direct client per lane,
    one key source.
    """
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if (
            describer is not None
            and describer.enabled
            and describer.model
            and describer.provider == provider
        ):
            return describer.api_key_env or _DIRECT_LANES[provider].default_key_env
    return _DIRECT_LANES[provider].default_key_env


def _direct_describer_clients(cfg: Config) -> dict[str, AsyncOpenAI]:
    """Provider → its direct client, for every lane some live describer selects.

    Built only for a lane something USES, so a deployment that stays on
    omniroute builds nothing new and needs no extra key. A selected lane whose
    key is unset is left OUT, and `VisionClient._create` refuses a provider it
    has no client for — the modality answers `unavailable`, never a silent
    OmniRoute fallback that would drop the video and answer anyway.
    """
    from nora.agent.factory import create_client
    from nora.infra.vision.client import opencode_go_http_client

    clients: dict[str, AsyncOpenAI] = {}
    for provider in sorted(set(_describe_providers(cfg).values()) & set(_DIRECT_LANES)):
        lane = _DIRECT_LANES[provider]
        key_env = _direct_lane_key_env(cfg, provider)
        api_key = os.environ.get(key_env, "")
        if not api_key:
            log.warning(
                "media describer provider %r selected but %s is unset — "
                "its modalities answer unavailable",
                provider, key_env,
            )
            continue
        clients[provider] = create_client(
            base_url=lane.base_url,
            api_key=api_key,
            http_client=opencode_go_http_client() if lane.session_header else None,
        )
    return clients


def _describe_max_bytes(cfg: Config) -> dict[str, int]:
    """Modality → the byte ceiling ITS describer is allowed to send.

    Present only when the modality's `DescriberConfig.max_bytes` is non-zero —
    absence (0) means "inherit the VisionClient's shared `vision_max_bytes`".
    Same enable+model gate as `_describe_models`/`_describe_providers`, so the
    three maps cannot disagree about which modalities are live; the ceiling is
    the one field that is allowed to vary per modality because video bytes are
    routinely larger than any still image (an image-sized ceiling would turn
    every real clip into `too_large`).
    """
    resolved: dict[str, int] = {}
    for modality in ("image", "audio", "video"):
        describer = cfg.media.describer_for(modality)
        if (
            describer is not None
            and describer.enabled
            and describer.model
            and describer.max_bytes
        ):
            resolved[modality] = describer.max_bytes
    return resolved


def _otp_secrets(cfg: Config) -> tuple[bytes, str]:
    """The HMAC key and the API bearer token, checked at BOOT when `[otp]
    enabled`, for `_computer_token`'s reason: an empty key would hash every
    code under nothing, and an empty token would leave the sign-in endpoint
    open to anything on the network. Refuse to start instead."""
    values = []
    for name in (cfg.otp.secret_env, cfg.otp.api_token_env):
        value = os.environ.get(name, "").strip()
        if not value:
            raise StartupError(
                f"[otp] enabled = true but {name} is unset or empty (the value "
                "lives in .env, DEC-8); refusing to start"
            )
        values.append(value)
    return values[0].encode(), values[1]


def _computer_token(cfg: Config) -> str:
    """computerd's bearer token, checked at BOOT when `[computer] enabled`:
    the R-5 discipline (refuse to start rather than
    run in a state that looks like working). Without it an empty token would
    start the daemon cleanly and then answer every computer call
    `unavailable` (computerd's 401), in front of the group, for a fault only
    the operator can fix."""
    token = os.environ.get(cfg.computer.token_env, "")
    if not token.strip():
        raise StartupError(
            f"[computer] enabled = true but {cfg.computer.token_env} is unset or "
            "empty: it is computerd's bearer token (the value lives in .env, "
            "DEC-8). Starting without it would fail every computer call; "
            "refusing to start"
        )
    return token


def google_credentials(cfg: Config, *, metrics: Any) -> GoogleCredentials | None:
    """Nora's ONE Google identity (`[google]`), or
    None. Built when a Google service is switched on (shared files today) and
    all three values `[google]` names are in the environment (DEC-8). A value
    missing is None and a warning naming the variable, never its value: the
    Google services then answer `unavailable`, never the boot. Every service
    gets THIS object and asks it for a token for its own scope; none sees the
    secrets."""
    if not cfg.external_files.enabled:
        return None
    names = (cfg.google.client_id_env, cfg.google.client_secret_env,
             cfg.google.refresh_token_env)
    values = [os.environ.get(name, "").strip() for name in names]
    missing = [name for name, value in zip(names, values, strict=True) if not value]
    if missing:
        log.warning(
            "[google] %s unset; every Google service answers unavailable (run "
            "scripts/google_auth.py, put the values in .env)", ", ".join(missing),
        )
        return None
    client_id, client_secret, refresh_token = values
    return GoogleCredentials(
        client_id=client_id, client_secret=client_secret,
        refresh_token=refresh_token, metrics=metrics,
    )


def external_files_source(
    cfg: Config, *, google: GoogleCredentials | None,
) -> ExternalFilesSource | None:
    """The shared-file source the open tool reads through, or None:
    `[external_files]` off, or no Google identity (`google_credentials`). None answers the open
    `unavailable`; recording a link as a handle needs none of this."""
    if not cfg.external_files.enabled or google is None:
        return None
    from nora.infra.external_files.google_drive import GoogleDriveSource

    return GoogleDriveSource(credentials=google)


#: Every Character adaptor that is not the room fold, by name (budgets, grants).
CHARACTER_ADAPTOR_NAMES: tuple[str, ...] = (
    CHARACTER_PERSON_ADAPTOR_NAME, CHARACTER_UNIT_ADAPTOR_NAME,
    CHARACTER_NORA_ADAPTOR_NAME,
)


def _now_ms() -> int:
    """Wall-clock epoch milliseconds, the Lessons book's and evidence's clock."""
    return int(datetime.now(UTC).timestamp() * 1000)


def character_unit_keys(allowlist: Any) -> dict[str, UnitKey]:
    """One key per unit, with its rooms in policy order."""
    rooms_of: dict[str, list[CharacterRoom]] = {}
    for conv in allowlist.conversations:
        if conv.primary_id and conv.unit:
            rooms_of.setdefault(conv.unit, []).append(CharacterRoom(
                key=conv.primary_id, platform=conv.platform, label=conv.label,
            ))
    return {
        unit_culture_key(unit): UnitKey(unit=unit, rooms=tuple(rooms))
        for unit, rooms in rooms_of.items()
    }


def social_person_units(allowlist: Any) -> dict[str, tuple[str, ...]]:
    """Each person LISTED by id in a room, and the units of the rooms that list
    them, in policy order: the units a social item of theirs is charged to and
    announced in. A `*` room lists nobody, the rule `SocialSubjectIndex.members`
    applies to who is polled."""
    out: dict[str, list[str]] = {}
    for conv in allowlist.conversations:
        if conv.persons is None or not conv.unit:
            continue
        for person in sorted(conv.persons):
            units = out.setdefault(person, [])
            if conv.unit not in units:
                units.append(conv.unit)
    return {person: tuple(units) for person, units in out.items()}


def social_person_rooms(allowlist: Any) -> dict[tuple[str, str], tuple[str, str]]:
    """`(person, unit) -> (platform, conversation_id)`: the room a social
    stimulus of theirs binds in that unit, the unit's home room when it lists
    them and otherwise the first room of the unit that does, in policy order.
    A social item is shown only in a room that lists its author (the `social`
    lineage predicate), so the session reads it through a room that may be
    shown it."""
    homes = allowlist.home_rooms()
    out: dict[tuple[str, str], tuple[str, str]] = {}
    listing = [
        c for c in allowlist.conversations if c.persons is not None and c.unit
    ]
    for conv in sorted(listing, key=lambda c: homes.get(c.unit) is not c):
        for person in conv.persons:
            out.setdefault((person, conv.unit), (conv.platform, conv.primary_id))
    return out


def character_person_keys(allowlist: Any, persons: Any) -> dict[str, PersonKey]:
    """One key per (unit, person listed in any of that unit's rooms), from
    `policy.toml` and `identity.toml`. A
    person's evidence and room cultures are the unit's rooms, in policy order;
    their read tools stand in the first of those that lists them. A `*` room
    lists nobody by name and adds no person."""
    rooms_of: dict[str, list[CharacterRoom]] = {}
    listing: dict[tuple[str, str], list[str]] = {}
    refs: dict[str, Any] = {}
    for conv in allowlist.conversations:
        if not conv.primary_id or not conv.unit:
            continue
        rooms_of.setdefault(conv.unit, []).append(CharacterRoom(
            key=conv.primary_id, platform=conv.platform, label=conv.label,
        ))
        for ref in persons.room_members(conv.persons):
            refs[ref.person_id] = ref
            listing.setdefault((conv.unit, ref.person_id), []).append(conv.primary_id)
    return {
        character_key(unit, person_id): PersonKey(
            unit=unit, person=refs[person_id], rooms=tuple(rooms_of[unit]),
            home=homes[0], listed=tuple(homes),
        )
        for (unit, person_id), homes in listing.items()
    }


@dataclass(frozen=True)
class Dependencies:
    """The wired collaborators the app receives instead of `Config`.

    Deliberately **no** `config`/`settings` field. A stage that can reach the
    whole config is a stage that eventually will — that is how a god object
    grows. Add a named collaborator here instead of widening this type's reach.

    `audit`, `memory`, `policy`, `assembler` are annotated with their ports
    (`RecordStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`), not
    their concrete classes — all four live inside mypy's strict override
    (`nora.app.*`), so each is a permanent, statically-checked proof that its
    concrete class conforms to the port, not just a one-time `isinstance`
    name-check. `adapter` stays concrete (`SignalAdapter`, not
    `PlatformAdapter`) because the few things reading it are Signal-specific BY
    CONSTRUCTION and should say so out loud rather than pretend to be
    platform-neutral: `nora_sender_id`, the signal-cli roster, and the `sse`
    block `/health` pins for the watchdog. Everything that must work across
    platforms reads `registry` instead (DEC-102) — so widening this field would
    hide the split rather than close it. `persons`, `metrics`, `doctrine`,
    `doctrine_store`, `debounce`, `background`, `llm_state`, `ingest`,
    `engagement`, `dispatcher` stay concrete too: none has a port at all.

    `client` (DEC-13): the process-wide `AsyncOpenAI` instance, constructed
    exactly once by `build_dependencies` below. It is an ordinary collaborator,
    and `nora.__main__.App.shutdown` closes it directly.
    """

    audit: RecordStore
    memory: MemoryStore
    persons: Persons
    # The ONE media join, so `__main__` can hand the same
    # instance to the dream loop and to lazy adoption — the two transcript
    # builders this module does not construct itself.
    media_join: MediaJoin
    metrics: Metrics
    policy: EngagePolicy
    # A Discord guild id → where its SPOKEN utterances are recorded, or `None`.
    # `__main__` hands
    # this to `register_voice`; it is a callable rather than the room `Policy`
    # because `nora.web` must not learn about policy loading, and because
    # `policy` above is already taken by the ENGAGE policy, which is a
    # different object answering a different question.
    resolve_voice_room: Callable[[str], str | None]
    # A person_id -> the Nora workspaces they belong to (DEC-214), for the
    # portal's own-memory page. A callable for `resolve_voice_room`'s reason:
    # `nora.web` never sees a `Policy`.
    workspaces_of: Callable[[str], tuple[str, ...]]
    # Documents (ADR 0020): the store the portal reads, the service that publishes into
    # it (the `document_publish` tool's collaborator), and person_id -> the rooms whose
    # `persons` name them, from the CURRENT policy, for the same reason as
    # `workspaces_of`: `nora.web` never sees a `Policy`.
    documents: DocumentStore
    document_service: DocumentService
    rooms_of: Callable[[str], frozenset[RoomKey]]
    # Whether a member of a room may see an event of it they neither made nor
    # answered on the portal's Plans (`nora.app.gatherings.room_event_shown`,
    # bound to the process's lineage policy).
    room_event_shown: Callable[[Any, str, str, str], bool]
    # (platform, conversation_id) -> the room's policy label, for the portal's document
    # rows; "" for a room the policy does not name.
    room_label: Callable[[str, str], str]
    # The content-addressed byte store, for the portal's picture route (a document names
    # its pictures by sha256). `None` when media custody is off: the route then answers
    # `unavailable`, never a picture.
    blobs: BlobStore | None
    # Artifacts (ADR 0023): `computer_export`'s record, which the portal lists for the
    # rooms that name the viewer (`rooms_of`), with the bytes from `blobs`.
    artifacts: ComputerExportStore
    assembler: ContextAssembler
    ingest: IngestPipeline
    engagement: EngagementPipeline
    dispatcher: Dispatcher
    # The participation occasion's quiescence trigger, None unless
    # `[participation] enabled` AND a fire path was
    # built. `nora/__main__.py` spawns `runtime.participation_loop` on it —
    # the same "dependencies, not config" split every other loop uses.
    participation_trigger: ParticipationTrigger | None
    #: The deployed gate, named so it can be reached without a private.
    #:
    #: It is BUILT below beside `participation_trigger` and held by it
    #: (`ParticipationTrigger._gate`); one branch constructs both, so the two
    #: cannot come apart. This field is not a second instance.
    #:
    #: The gate is an ENTRY POINT into Nora and, like every other entry point
    #: (`dispatcher`, `engagement`, `participation_trigger`,
    #: `dreamer_runtime`), is an attribute here, so a harness can reach the
    #: deployed one rather than building a second.
    relevance_gate: RelevanceGate | None
    #: The deployed fire path, named for the same reason as `relevance_gate`
    #: above: it is an ENTRY POINT.
    #:
    #: `FirePath.speak` is what the trigger calls once the gate admits a
    #: window, what the heartbeat calls when a reminder comes due, and what
    #: every proactive occasion runs through.
    fire_path: FirePath | None
    #: The ONE `<presence>` reader the tag path's
    #: assembler and the fire path both hold. Named for the
    #: same reason as the entry points above: reachable without a private.
    #: NOT `presence` below,
    #: which is the Life360 store — where the PEOPLE are; this is where SHE is.
    self_presence: PresenceReader
    #: The clock this graph runs on, named so a harness can ask for it.
    clock: Callable[[], datetime]

    #: The fire-time screen a `kind="proactive"` row passes before it speaks.
    #: Named for the same reason as the entry points above — it decides whether
    #: an act turn happens at all.
    proactive_screen: ProactiveScreen
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
    # Read-only analytics dashboard consumer over the SAME engine as audit and
    # presence. It has no independent lifecycle; audit closes the shared pool.
    mobility: SqlMobilityStore
    # The SAME directory `factory.build_agent`'s `skills_dir` argument
    # already resolves (`cfg.doctrine.dir.parent / "skills"`) — exposed here
    # so `nora.web.skills` can read the same on-disk metadata (name,
    # description) for the portal, using its OWN `SkillStore` instance
    # rather than the live agent's Strands `AgentSkills` plugin (that one
    # is built per-tag inside `factory.create_agent` and holds no
    # web-reachable listing method).
    skills_dir: Path
    # The Dreamer's consolidation cursor and the scopes it ticks over.
    # `language_store` is the CONCRETE `SqlLanguageStore`, which is
    # the cursor and nothing else (see
    # `nora.infra.sql.language`'s own docstring). `None` when `[dreamer]
    # enabled = false`; `nora.__main__.App.run` reads these to spawn (or log
    # skipping) `runtime.dream_loop`.
    language_store: SqlLanguageStore | None
    language_scopes: Callable[[], Sequence[str]]
    # The Dreamer's instruction corpus. Built unconditionally (see its
    # construction below), so unlike its neighbours here it is never None —
    # `dream_loop` reads it for the per-tick `instruction_versions=` line, and
    # both observers hold it as their objective source.
    dreamer_instructions: DreamerInstructions
    # The Dreamer RUNTIME and what it schedules — `None`/empty under
    # `[dreamer] enabled = false`, the shipped default.
    dreamer_runtime: DreamerRuntime | None
    dream_adaptors: tuple[DreamAdaptor, ...]
    # Per-adaptor clocks for the dream clock,
    # keyed by adaptor name and EMPTY under `[dreamer] enabled = false`.
    # An adaptor with no entry keeps the shared lag/quiet/age shape; an entry
    # is what moves one onto its own interval, batch and envelope cap.
    dream_schedules: Mapping[str, AdaptorSchedule]
    # Where a watermark-free scheduled adaptor's last fire is remembered.
    # ALWAYS built, even under `[dreamer] enabled = false`: it is one small
    # store over an engine that already exists, and making it conditional
    # would put a `None` on the one path whose whole purpose is to survive
    # process boundaries. `dream_loop` ignores it when `schedules` is empty.
    dream_schedule_clock: SqlDreamScheduleClock
    # What instructions, config and code were in force when. The store only:
    # the daemon's `App.run` builds the recorder that writes through it, so
    # an eval subject composed from this graph records no loads of its own.
    revisions: SqlRevisionStore
    # `language_scopes`' unit-keyed sibling. `Conversation.unit`
    # (DEC-105) — several rooms of one unit are ONE unit of work here, not
    # several, which is the whole reason a unit-keyed adaptor could not
    # ride the per-scope schedule.
    unit_scopes: Callable[[], Sequence[str]]
    # The SAME `GatheringService` built above alongside `event_store` and
    # already threaded to `ToolRegistry(events=events)` —
    # exposed here too so `nora.web.api.register_web_routes`'s read-only
    # `/app/v1/events(/{id})` pair serves from the identical instance the
    # agent's event tools use, rather than a second `GatheringService` over the
    # same store.
    events: GatheringService
    # `[speech.tts]`'s speaker, annotated with the
    # port like `audit`/`memory` above. `None` unless `enabled` AND a provider
    # AND its key are all present; callers must behave correctly with `None`.
    speaker: Speaker | None
    # The audio-controller seam: the per-guild
    # voice-session registry every voice path shares, and the consumer for
    # the controller's `/app/v1/voice/event` callbacks. Both `None` when
    # `[speech.tts] sidecars` is empty (the consumer additionally needs
    # `[music] enabled`) — a deployment without a controller boots unchanged
    # and every voice verb fails closed.
    voice_hub: VoiceControllerHub | None
    voice_event_consumer: VoiceEventConsumer | None
    # The cognition loop's wake evaluator and session:
    # `None` under `[cognition] enabled = false`, the shipped default. The
    # shutdown cancels the sessions in flight, once (`nora.app.cognition_wiring`).
    wake_evaluator: WakeEvaluator | None = None
    cognition_runner: CognitionSessionRunner | None = None
    cognition_shutdown: CognitionShutdown | None = None
    # The sub-agent worker: `None` under `[subagent] enabled = false`, the
    # shipped default. The shutdown cancels the jobs in flight.
    subagent_backend: SubagentBackend | None = None
    wake_tick_seconds: float = 30.0
    # The computer session sweeper: `None`
    # unless `[computer] enabled`. `nora/__main__.py` spawns
    # `runtime.computer_sweep_loop` on it every `sweep_seconds`.
    computer_sweeper: ComputerSessionSweeper | None = None
    computer_sweep_seconds: float = 300.0
    # The timed probe of computerd (`nora.infra.computer.probe`): `None`
    # unless `[computer] enabled`. `nora/__main__.py` spawns
    # `runtime.computer_probe_loop` on it.
    computer_probe: ComputerdProbe | None = None
    # Platform adapter health transitions as Record. ALWAYS built:
    # it is Record, not a subsystem that acts, and «was the source healthy
    # for the span» must be answerable for the whole history, not only from
    # the day an operator flipped a flag.
    health_recorder: AdapterHealthRecorder | None = None
    # One-time codes (`nora.app.otp`): `None` unless `[otp] enabled`, and then
    # `nora/__main__.py` mounts `/app/v1/otp/*` behind `otp_api_token`.
    otp: OtpService | None = None
    otp_api_token: str = ""
    # The OAuth keepalive (DEC-308, `nora.app.oauth_keepalive`): `None` unless
    # `[image_gen] enabled` and at least one lane's token is set with
    # `keepalive = true`. `nora/__main__.py` starts it; the eval subject,
    # which composes the same graph, does not.
    oauth_keepalive: OAuthKeepalive | None = None
    # The memory index's embedding worker: `None` under `[memory_index] embed =
    # false`, the default. `__main__` hands it to the memory drain loop.
    memory_embed: MemoryEmbedWorker | None = None
    # The social stream's poller: `None` unless `[social.poll] enabled`.
    # `nora/__main__.py` starts it; the eval subject, which composes the same
    # graph, does not.
    social_poller: SocialPoller | None = None
    # Its perception pass: `None` whenever the poller is. `nora/__main__.py`
    # starts it beside the poller.
    social_perception: SocialPerception | None = None
    # A story frame's bytes deleted at their bound: `None` unless `[social]
    # story_expiry_enabled` and media custody is on; built whether or not the
    # poller runs, so bytes it kept still go once it is switched off.
    # `nora/__main__.py` starts it.
    story_expiry: StoryMediaExpiry | None = None


def _image_gen_wiring(
    cfg: Config,
    metrics: Metrics,
    environ: Mapping[str, str],
    *,
    state_dir: Path,
) -> tuple[ImageGenerator | None, OAuthKeepalive | None]:
    """The `generate_image` provider and the OAuth keepalive ([image_gen]).

    Off: `(None, None)`, and the tool answers `unavailable` (R-5/R-27). On,
    EVERY OAuth lane whose bootstrap token is set gets one lane object
    (DEC-308), whichever `provider` is selected; the selected one is also
    the adapter's, the SAME object, so the keeper and a call share one
    refresh lock and never both spend a rotating token. Each lane with
    `keepalive = true` is registered with the one keeper, which
    `nora/__main__.py` starts (the eval subject composes this graph too and
    must not refresh the deployment's tokens). The state files live in the
    repo-tree state dir `./state/signal-cli` already uses (container-
    writable; the host-immutable `.env` holds only the bootstrap tokens).
    """
    if not cfg.image_gen.enabled:
        return None, None
    from nora.infra.image_gen import (
        OpenAICodexOAuth,
        XaiOAuth,
        build_image_generator,
    )

    image_generator: ImageGenerator | None
    xai_cfg = cfg.image_gen.xai
    codex_cfg = cfg.image_gen.openai_codex
    xai_token = environ.get(xai_cfg.refresh_token_env, "")
    codex_token = environ.get(codex_cfg.refresh_token_env, "")
    xai_lane = XaiOAuth(
        refresh_token=xai_token,
        state_file=state_dir / "xai-oauth.json",
        timeout_seconds=xai_cfg.timeout_seconds,
    ) if xai_token else None
    # DEC-304: the env var holds only the BOOTSTRAP token; the rotating
    # one lives in the state file beside xAI's.
    codex_lane = OpenAICodexOAuth(
        bootstrap_refresh_token=codex_token,
        state_file=state_dir / "openai-codex-oauth.json",
    ) if codex_token else None
    if cfg.image_gen.provider == "openai_codex":
        token_env = codex_cfg.refresh_token_env
        image_generator = build_image_generator(
            provider="openai_codex",
            oauth=codex_lane,
            model=codex_cfg.model,
            responses_model=codex_cfg.responses_model,
            standard_quality=codex_cfg.standard_quality,
            high_quality=codex_cfg.high_quality,
            timeout_seconds=codex_cfg.timeout_seconds,
            max_reference_images=codex_cfg.max_reference_images,
        ) if codex_lane is not None else None
        selected_token = codex_token
    else:
        token_env = xai_cfg.refresh_token_env
        image_generator = build_image_generator(
            provider=cfg.image_gen.provider,
            oauth=xai_lane,
            model=xai_cfg.model,
            quality_model=xai_cfg.quality_model,
            timeout_seconds=xai_cfg.timeout_seconds,
            max_reference_images=xai_cfg.max_reference_images,
        ) if xai_lane is not None else None
        selected_token = xai_token
    if not selected_token:
        log.warning(
            "image_gen enabled but %s is unset — generate_image stays unavailable",
            token_env,
        )
    elif image_generator is None:
        # An unknown provider (or unconstructable lane) degrades
        # the tool, never crashes the daemon.
        log.warning(
            "image_gen enabled but provider %r could not be built — "
            "generate_image stays unavailable",
            cfg.image_gen.provider,
        )
    keeper = OAuthKeepalive(metrics)
    kept: tuple[tuple[KeptLane | None, bool, float], ...] = (
        (xai_lane, xai_cfg.keepalive, xai_cfg.refresh_interval_hours),
        (codex_lane, codex_cfg.keepalive, codex_cfg.refresh_interval_hours),
    )
    for lane, wanted, hours in kept:
        if lane is not None and wanted:
            keeper.register(lane, KeepaliveTiming(interval_seconds=hours * 3600.0))
    return image_generator, (keeper if keeper.lanes else None)


def wall_clock() -> datetime:
    """The deployment's clock. THE default, and the only one production uses."""
    return datetime.now(UTC)


def _story_frames_embedded(
    engine: AsyncEngine, model: str,
) -> Callable[[Sequence[tuple[str, str]]], Awaitable[set[tuple[str, str]]]]:
    """The memory index's answer to which story frames are embedded under the
    deployed embedder, for the story expiry pass."""
    from nora.infra.sql.memory_index import story_frame_embedded

    async def embedded(frames: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:
        async with engine.connect() as conn:
            return await story_frame_embedded(conn, frames, model=model)

    return embedded


async def build_dependencies(
    cfg: Config, *, clock: Callable[[], datetime] = wall_clock,
) -> Dependencies:
    """Build the object graph from `cfg`.

    This function is the only place in the codebase allowed to know `Config`'s
    shape (besides `nora.__main__` itself, which only ever passes `cfg`
    straight through here). Everything downstream takes a `Dependencies`.

    **`clock` names a dependency the graph shares.** Many components take
    `clock: Callable[[], datetime]` — `FirePath`, `HeartbeatLoop`,
    `ComputePoller` and the rest — each for the reason `FirePath`'s own
    docstring gives: *"a module that read the wall clock itself could not be
    tested at a fixed instant."* This function states the clock once and hands
    it to all of them; the default is `wall_clock`.

    A caller can therefore say WHAT TIME IT IS. The proactive adaptor's whole
    judgement is about time (is this note due, has this position gone stale,
    is now the moment), and a harness asks it a question at a fixed instant by
    passing a clock.
    """
    try:
        persons = load_persons(cfg.identity_file)
    except PersonsError as exc:
        raise StartupError(f"person mapping error: {exc}") from exc

    # The Nora aiPeer id is RESERVED — no human may hold it. If an operator
    # gave a person the id `nora`, that human's words would feed under the
    # aiPeer and merge IRREVERSIBLY into Nora's self-model card (Honcho keeps
    # conclusions, not text). Fail closed at startup rather than pollute the
    # corpus.
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
    # The dashboard consumer reads the derived analytics tables through the
    # same pool as every other Nora store. A second engine would fork the pool
    # and violate the one-pool, one-lifecycle composition rule.
    mobility = SqlMobilityStore(engine)
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
    # does above. Built HERE, unconditionally, beside the other stores: boot
    # recovery a few hundred lines down (beside `reminders`' own unconditional
    # sweep) must find a store to sweep even when the poller itself is off. It
    # is the generic deferred-delivery outbox. Only the poller's tick loop
    # (`compute_backends`, further down) is behind the flag.
    compute_jobs: ComputeJobStore = SqlComputeJobStore(engine)
    tool_call_log_store: ToolCallLogStore = SqlToolCallLogStore(engine)
    # The computer audit log (DEC-243). Built
    # UNCONDITIONALLY, like `tool_call_log_store`: the computer tools write a
    # row on every call, `unavailable` included, so a call made while
    # `[computer]` is off is audited too. Only the runtime is behind the flag.
    computer_events: ComputerEventStore = SqlComputerEventStore(engine)
    # Computer sessions, derived by code on
    # every computer call, and the process's ONE lease table: one
    # instance shared by every turn and the sweeper, which is what makes an
    # in-process lock a lock (one Nora process; `ComputerLeases` says so).
    computer_sessions: ComputerSessionStore = SqlComputerSessionStore(engine)
    computer_leases = ComputerLeases()
    # The runtime: `ComputerdRuntime`, built ONLY when `[computer] enabled`
    # (see the settings below for the rest of `[computer]`). Built here,
    # before the assembler, because `<computer>` reads it on every turn.
    computer: SandboxRuntime | None = None
    computer_probe: ComputerdProbe | None = None
    if cfg.computer.enabled:
        from nora.infra.computer.client import ComputerdRuntime

        computer_token = _computer_token(cfg)
        # The computer as a metric (DEC-261): built ONLY here, so with
        # `[computer] enabled = false` there is no `nora_computer_up` series at
        # all (not applicable, which is not down) and no zero-valued calls
        # series either. `prime()` creates them at zero, so a rare `unavailable`
        # after a restart is not a series whose first sample is already 1.
        computer_metrics = ComputerMetrics(metrics, audit_tools=COMPUTER_TOOLS)
        computer_metrics.prime()
        computer = ComputerdRuntime(
            base_url=cfg.computer.base_url, token=computer_token,
            timeout_seconds=cfg.computer.request_timeout_seconds,
            max_output_chars=cfg.computer.max_output_chars,
            observer=computer_metrics,
        )
        computer_probe = ComputerdProbe(
            base_url=cfg.computer.base_url, token=computer_token,
            observer=computer_metrics,
        )
    # `<computer>`: the one reader the tag
    # path and the fire path share. `None` while `[computer]` is off, so no
    # room renders the block.
    computer_context: ComputerContextReader | None = None
    if computer is not None:
        computer_context = ComputerContextReader(
            sessions=computer_sessions, events=computer_events, runtime=computer,
            caps=ComputerContextCaps(
                **cfg.computer.context.model_dump(),
                idle_after=timedelta(minutes=cfg.computer.sessions.idle_after_minutes),
                lapsed_visible=timedelta(hours=cfg.computer.sessions.lapsed_visible_hours),
            ),
            clock=lambda: datetime.now(UTC), metrics=metrics,
        )
    # The durable half of the reasoning-strip counter (`reasoning_strip_log`,
    # ADR 0013), REUSING `engine` for the same reason the three stores above do.
    # UNCONDITIONAL and behind no flag: an instrument that is off by default
    # answers nothing, and the question it exists to answer -- how often the
    # transport throws a turn's reasoning away, per unit of OPPORTUNITY, and
    # whether that moved when the model or the effort did -- spans deploys,
    # which is exactly what a Prometheus counter in this process cannot hold.
    # All three consumers take it: the tag path and the participation occasion
    # through `deliver.build`, and the Dreamer through `DreamerRuntime`.
    reasoning_strip_store = SqlReasoningStripStore(engine)
    # The reasoning-continuity retention store,
    # REUSING `engine` for the same reason `reminders`/`compute_jobs`/
    # `tool_call_log_store` do above. UNCONDITIONAL, unlike the Dreamer's own
    # stores just below: the `[debrief]` flag gates the FOLD, not the
    # raw retention write.
    tag_trace_store = SqlTagTraceStore(engine)
    # Standing. The STORE is built unconditionally, REUSING
    # `engine` for the same reason its four neighbours above do — the read half
    # feeds `TranscriptInjection`, whose own `_standing` returns `None` for a
    # `None` store, so an always-built store costs one attribute on a
    # deployment with the flag off and removes a whole class of "wired the
    # writer, forgot the reader" mistake. What the flag gates is the FOLD,
    # which is the part that spends a model call inside the reply slot.
    standing_store = SqlStandingStore(engine)
    # The unified media index (DEC-138), built HERE rather than beside the
    # vision client below because THREE readers now hold it and the earliest is
    # the context assembler: ingest writes the custody row, the comprehension
    # job writes the read, the assembler joins it into the transcript and
    # `media_fetch` serves it. One instance, `engine` reused for the reason
    # every other store here reuses it (one pool, one lifecycle).
    media_objects = SqlMediaObjectStore(engine, metrics=metrics)
    documents_store: DocumentStore = SqlDocumentStore(engine)
    # The window scanned for a picture's marker code is the one `media_fetch` scans: the
    # prefill plus `load_transcript`'s reach, so any picture she can still see resolves
    # and an older one is honestly not found.
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
    _require_a_sendable_public_url(cfg.documents.public_url)
    document_service = DocumentService(
        store=documents_store, records=audit, media=media_objects,
        media_window=cfg.store.context_window + LOAD_TRANSCRIPT_MAX,
        room_ids=_room_ids_of(allowlist),
        is_person=persons.by_person.__contains__,
        public_url=cfg.documents.public_url,
        # A Map names its places and the service looks them up. The same
        # client `route` and `place_info` use, so `[route]` off means a Map is `unavailable`.
        place_locator=route_client,
    )
    # `<documents>`: the one reader the tag path and the fire path
    # share. `None` while `[documents]` is off, so no room renders the block (the same
    # flag that withholds the document tools).
    documents_context: DocumentsContextReader | None = None
    if cfg.documents.enabled:
        documents_context = DocumentsContextReader(
            store=documents_store, room_ids=_room_ids_of(allowlist),
            clock=lambda: datetime.now(UTC), metrics=metrics,
        )
    # `<subagents>`: this room's sub-agent jobs, tag turn only. `None`
    # while `[subagent]` is off. The worker is attached where the desk's is, once built.
    subagents_context: SubagentsContextReader | None = None
    if cfg.subagent.enabled:
        subagents_context = SubagentsContextReader(
            jobs=compute_jobs, clock=lambda: datetime.now(UTC), metrics=metrics,
            documents=document_service if cfg.documents.enabled else None,
        )
    # ONE media join for the whole process, handed to every transcript builder
    # there is: the tag assembler, the fire path, the tag-path debrief, the
    # Cognition session and lazy adoption, so none renders bare handles where
    # another renders the comprehension.
    #
    # `scan_identifiers` is INJECTED because `nora/language/` sits two layers
    # below `nora/agent/` in the import contract and could not otherwise reach
    # it — see `nora.domain.media_join`'s docstring.
    media_join = MediaJoin(
        store=media_objects,
        screen=scan_identifiers,
        metrics=metrics,
        render_chars=cfg.media.comprehension_render_chars,
        # The per-QUERY timeout, on every path. The two slot-held callers wrap
        # `enrich` in their own `wait_for` as well; this is what bounds the
        # store read for the six that do not, and it is deliberately shorter
        # than `[standing] store_timeout_seconds` so the inner bound is the one
        # that fires and the outer stays the backstop.
        timeout_seconds=cfg.media.join_timeout_seconds,
    )
    # ONE quote-aware window resolver for the whole process (DEC-195), handed
    # to every transcript
    # read that can grow past `context_window` to resolve a reply's quote:
    # the tag assembler AND the fire path's `build_context` — the same
    # `MediaJoin` precedent immediately above, for the same reason. Built
    # from `audit` (this process's ONE `RecordStore`) rather than each
    # caller constructing its own, so `quote_margin`/`quote_window_multiplier`
    # cannot drift between the two paths the way nothing else here is allowed
    # to.
    quote_window = QuoteWindow(
        audit=audit, metrics=metrics,
        quote_margin=cfg.store.quote_margin,
        quote_window_multiplier=cfg.store.quote_window_multiplier,
    )
    # The shared decision log. Built unconditionally beside
    # `standing_store` for its reason — it costs one
    # attribute where nothing reads it — and there is a second reason here: the
    # log has FOUR writers on three different paths, so a flag gating it would
    # be four places to forget rather than one, and a half-wired log is worse
    # than no log at all (a block that says «nothing happened in this room»
    # while the gate has been deciding about it all day).
    #
    # ONE recorder for the whole process, handed to all four writers. That is
    # not only tidiness: `DecisionLogRecorder._next_id` breaks a
    # same-millisecond tie with a counter, and the counter is per recorder, so
    # four recorders would be four independent sequences and one pass's own
    # rows could still order arbitrarily. It is also the single place the
    # fail-soft rule and the wall clock live, which is what makes «can a log
    # write fail a turn?» a question about one method.
    #
    # `unit_of` is the SAME `Policy` read `FirePath._unit_for` and
    # `TranscriptInjection._notes` make (DEC-105): two readers each carrying
    # their own idea of a room's unit disagree the first time a deployment
    # runs two social units. A room with no entry leaves the column empty
    # rather than guessing — the recorder's own refusal.
    decision_log = DecisionLogRecorder(
        store=SqlDecisionLogStore(engine),
        metrics=metrics,
        clock=clock,
        unit_of=lambda platform, conversation_id: (
            convo.unit
            if (convo := allowlist.conversation(platform, conversation_id))
            is not None else None
        ),
    )
    # The Dreamer's CURSOR, REUSING `engine` for the same reason
    # `reminders`/`compute_jobs`/`tool_call_log_store` do above. UNLIKE those,
    # this one IS gated on the feature flag: `SqlLanguageStore` owns
    # `consolidation_watermark` — `(consolidator_name, scope_id) -> how far
    # this adaptor has read` — so the Dreamer's clock cannot persist a
    # watermark for ANY adaptor without this object, and `__main__` spawns the
    # loop only where it exists. Off, `dream_loop` is never spawned.
    #
    # `SqlLanguageStore` keeps its name though it is the Dreamer's cursor: the
    # watermark table belongs to the Dreamer and lives in a module called
    # `language`; extracting it is its own port change with its own migration.
    language_store: SqlLanguageStore | None = (
        SqlLanguageStore(engine, metrics=metrics) if cfg.dreamer.enabled else None
    )
    # The note tier's store, REUSING `engine`
    # like every store above, and gated on its OWN flag. Off,
    # `TranscriptInjection` gets `notes=None` and `_notes` answers `()`
    # without a query. No `metrics=`:
    # `SqlNoteStore.__init__` takes an engine and nothing else on purpose (its
    # own docstring), and the one read on the critical path is counted by
    # `_notes` instead.
    note_store: SqlNoteStore | None = (
        SqlNoteStore(engine) if cfg.notes.enabled else None
    )
    # The memo tier (DEC-306): what a unit settled and keeps. No flag: only a
    # Cognition session writes a memo (`[cognition] enabled`), and with none
    # kept every read renders nothing. ONE instance for the tag and fire
    # `<memos>`, `memo_read` and the session's verbs.
    memo_store = SqlMemoStore(engine)

    # Resolved here, not inside AdapterRegistry — the same convention every
    # other `*_env` secret (the LLM key, the route key) follows: composition.py
    # reads the environment once and hands the already-resolved value to
    # whatever constructs the client that needs it.
    discord_token = os.environ.get(cfg.platforms.discord.token_env, "")
    # The audio-controllers: ONE PER GUILD, because
    # Discord's limit is one voice connection per bot token per guild and each
    # connection stays single. `[speech.tts] sidecars` lists a
    # controller endpoint per guild — usually the SAME one, since a single
    # sidecar serves them all — and IS the join allow-list: a guild with no
    # entry has no voice at all. Empty (the default) leaves the
    # whole seam unwired: no hub, no speech playout, and every `/music` or
    # `/discord join` failing CLOSED (the join-failed reply), never a local
    # connection attempt.
    #
    # Built BEFORE the music manager: the manager's staleness probes
    # (`playback_probe`, `generation_probe`) and its hub-session drop/exit
    # seams read and mutate the hub from construction.
    voice_hub: VoiceControllerHub | None = None
    if cfg.speech.tts.sidecars:
        voice_hub = VoiceControllerHub({
            guild_id: VoiceControllerClient(endpoint)
            for guild_id, endpoint in cfg.speech.tts.sidecars_by_guild.items()
        })
    # `spawn=asyncio.ensure_future`
    # rather than the `BackgroundTasks` instance built further down this
    # function — safe because `MusicPlayerManager`
    # already holds its own strong reference to every task it spawns
    # (`self._idle_tasks`), the exact GC-mid-flight bug `BackgroundTasks`
    # exists to prevent for tasks nobody else references.
    music_manager = (
        MusicPlayerManager(
            queue_store=SqlMusicQueueStore(engine), session_store=SqlMusicSessionStore(engine),
            track_source=YtDlpSource(), idle_timeout_seconds=cfg.music.idle_timeout_seconds,
            spawn=asyncio.ensure_future, autoplay_enabled=cfg.music.autoplay_enabled,
            autoplay_prewarm_seconds=cfg.music.autoplay_prewarm_seconds,
            playback_probe=voice_hub.expected_playback_id if voice_hub is not None else None,
            # The liveness watch: without it nora asks the controller for
            # status exactly twice in her life (boot, and after a version
            # conflict) and otherwise believes a track is playing until an
            # event says otherwise — which a wedged controller never sends.
            read_playback=voice_hub.read_playback if voice_hub is not None else None,
            metrics=metrics,
            generation_probe=voice_hub.generation_for if voice_hub is not None else None,
            drop_voice_session=voice_hub.drop if voice_hub is not None else None,
            exit_voice_session=voice_hub.exit if voice_hub is not None else None,
        )
        if cfg.music.enabled
        else None
    )
    # A room marked `voice = true` with NO controller for its guild is the one
    # combination that is neither useful nor refused: the join fails, so the
    # sidecar never connects, so no utterance is ever produced — and nothing
    # anywhere says why. The other three combinations are either a working
    # path or a loud 400 at the first wake.
    _joinable = voice_hub.guild_ids if voice_hub is not None else frozenset()
    for _convo in allowlist.conversations:
        if not _convo.voice:
            continue
        _guild = _convo.primary_id.partition("/")[0]
        if _guild not in _joinable:
            log.warning(
                "policy.toml marks %s (guild %s) `voice = true`, but no "
                "audio-controller is configured for that guild in "
                "[speech.tts] sidecars — nothing can join it, so no spoken "
                "utterance will ever be recorded there",
                _convo.label or _convo.primary_id, _guild,
            )
    if music_manager is not None and voice_hub is None:
        log.warning(
            "[music] enabled but [speech.tts] sidecars is empty — no "
            "audio-controller is wired for any guild, so every /music join "
            "will fail closed (the join-failed reply); music stays silent "
            "until a guild is mapped to its controller"
        )
    # `[speech.tts]`'s speaker: wired ONLY when
    # enabled AND a provider is named AND that provider's key is present —
    # the same inert-ship rule as the transcriber above, plus the keyless-
    # `route`/`search` shape. Off (or providerless, or keyless) leaves no
    # speaker and nothing about Nora's behaviour changes. Built BEFORE the
    # adapter registry (below) because DiscordAdapter's voice playout seam
    # receives this same instance — the registry's
    # `discord_speaker=` keyword is the one place that knows MiniMax is real;
    # everything downstream receives `Speaker | None`.
    # Built before the speaker (below) because the speech formatter reads its
    # instruction from it.
    doctrine_store = DoctrineStore(
        cfg.doctrine.dir, strict=cfg.doctrine.strict, reload=cfg.doctrine.reload,
        canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
        trigger=cfg.engagement.trigger.text,
        language_hint=cfg.doctrine.language_hint,
        tool_names=ToolRegistry().tool_names(),
        metrics=metrics,
    )
    try:
        doctrine = doctrine_store.get(applies_to="tag")
    except DoctrineError as exc:
        raise StartupError(f"doctrine error: {exc}") from exc

    speaker: Speaker | None = None
    if cfg.speech.tts.enabled:
        tts_key = os.environ.get(cfg.speech.tts.api_key_env, "")
        if cfg.speech.tts.provider == "minimax_cn" and tts_key:
            from nora.infra.tts import MiniMaxTtsSpeaker
            speaker = MiniMaxTtsSpeaker(
                api_key=tts_key,
                voice=cfg.speech.tts.voice or "Cantonese_CuteGirl",
                speed=cfg.speech.tts.speed,
                pitch=cfg.speech.tts.pitch,
                vol=cfg.speech.tts.vol,
                emotion=cfg.speech.tts.emotion or None,
                text_normalization=cfg.speech.tts.text_normalization,
                pronunciation=tuple(cfg.speech.tts.pronunciation),
                formatter=_speech_formatter(cfg, doctrine_store, metrics),
            )
        else:
            log.warning(
                "[speech.tts] enabled but provider=%r or %s unset — no speaker "
                "is wired; voice answers stay text-only",
                cfg.speech.tts.provider, cfg.speech.tts.api_key_env,
            )
    # The join allow-list IS the set of guilds a controller exists for — one
    # source of truth, so the gate and the client map can never disagree.
    voice_guild_ids = voice_hub.guild_ids if voice_hub is not None else frozenset()
    # The controller → nora callback consumer (`POST /app/v1/voice/event`) —
    # only when BOTH halves it glues exist:
    # the hub (whose event-id dedupe and expected-playback bookkeeping it
    # reads) and the music manager (whose per-guild-locked methods it
    # drives). `nora/__main__.py` registers the route only when this is
    # not None.
    voice_event_consumer = (
        VoiceEventConsumer(music_manager, voice_hub)
        if music_manager is not None and voice_hub is not None
        else None
    )
    # Where a guild's music ANNOUNCEMENTS go.
    # Built from the room policy, not from
    # `cfg.music`: the answer is about a room's people, so it belongs beside
    # the room's trust flags. The channel it names is a DESTINATION, not a
    # room — see `Conversation.music_channel` for why a room entry was wrong.
    resolve_music_channel = _music_channel_of(allowlist)
    # A `music_channel` named for a guild that has no audio-controller is DEAD
    # CONFIGURATION: `[speech.tts] sidecars` — not `policy.toml` — is what
    # gates `/music play` (`voice_gate`), so nothing will ever announce for
    # that guild. A warning rather than a startup
    # error: the two files legitimately change on different cadences, and an
    # operator who names the channel before wiring the sidecar has written
    # something correct-but-early, not something wrong.
    for convo in allowlist.conversations:
        if convo.music_channel is None:
            continue
        music_guild, _, _ = convo.primary_id.partition("/")
        if music_guild not in voice_guild_ids:
            log.warning(
                "%s names music_channel %s but guild %s has no audio-controller "
                "in [speech.tts] sidecars — music cannot play there, so the "
                "channel will never be announced into",
                convo.label or convo.primary_id, convo.music_channel, music_guild,
            )
    # The public Instagram summons' intake: the Instagram adapter hands every `summons` SSE event
    # here. ALWAYS built, so a card that arrives while the summons is off is
    # counted `disabled` rather than silently absent; `enabled` only under BOTH
    # `[social.instagram] enabled` and `[social.instagram.summons] enabled`,
    # exactly when the consumer below is spawned to drain it.
    summons_wired = bool(
        cfg.social.instagram.enabled and cfg.social.instagram.summons.enabled
    )
    summons_inbox = SummonsInbox(enabled=summons_wired, metrics=metrics)
    try:
        registry = await AdapterRegistry.from_config(
            cfg.platforms, metrics=metrics, discord_token=discord_token,
            music_manager=music_manager,
            discord_speaker=speaker,
            voice_hub=voice_hub,
            voice_guild_ids=voice_guild_ids,
            resolve_music_channel=resolve_music_channel,
            instagram_summons=summons_inbox,
            # A 1:1 message goes only to somebody in identity.toml; the
            # senders hold this set and refuse the rest.
            dm_recipients={p: persons.direct_addresses(p) for p in DIRECT_PLATFORMS},
        )
    except AdapterRegistryError as exc:
        raise StartupError(str(exc)) from exc
    if "signal" not in registry:
        # The rest of this function (the engagement pipeline, delivery) has no
        # code path that runs without an adapter — a disabled Signal with
        # nothing else enabled is not a valid startup state, even though
        # AdapterRegistry itself tolerates zero enabled platforms.
        raise StartupError("no platform adapter is enabled (platforms.signal.enabled = false)")
    signal_adapter = registry.get("signal")
    # `AdapterRegistry.get` returns the port type — narrowed back to the
    # concrete class here because `Dependencies.adapter` stays SignalAdapter
    # (nora.app only builds Signal-specific tool wiring around it;
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

    # The Dreamer's own instruction corpus — its doctrine plus one objective
    # per adaptor. Bind-mounted read-only the
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
    # invariant.
    if cfg.agent.name != cfg.memory.ai_peer:
        log.warning(
            "agent.name (%r) != memory.ai_peer (%r): traces and Honcho memory "
            "will use different agent identities — set both to the same value "
            "unless this split is intended",
            cfg.agent.name, cfg.memory.ai_peer,
        )
    # workspace > unit > room (DEC-214): every Honcho call is routed from its
    # room or unit to the Honcho workspace its Nora workspace is bound to, and
    # to the unit's named scope inside it. Resolved ONCE, here, from the three
    # files that each hold one part of it (`nora.app.memory_routing`); a
    # disagreement between them refuses startup rather than guessing where a
    # person's speech goes.
    try:
        router = build_workspace_router(
            policy=allowlist, bindings=persons.honcho_workspaces,
            default_workspace=cfg.memory.workspace_id,
        )
    except MemoryRoutingError as exc:
        raise StartupError(f"memory routing error: {exc}") from exc
    memory = HonchoClient(
        cfg.memory.base_url, cfg.memory.workspace_id, ai_peer=cfg.memory.ai_peer,
        workspace_router=router,
    )
    # Best-effort, never fatal — no try/except. Every bound Honcho workspace,
    # the Nora aiPeer in each (her replies feed under it to build her
    # self-model; peers are per Honcho workspace), and every unit's scope.
    await memory.ensure_workspace()
    # EXCEPT a bound Honcho workspace that belongs to another application:
    # that is a wrong line in identity.toml, and running on it would write
    # people's speech into someone else's peers. The client has already
    # refused every call to it; this makes the refusal loud (R-5). An
    # unreachable Honcho is not this — it is re-checked on the first call.
    foreign = memory.foreign_workspaces()
    if foreign:
        raise StartupError(
            f"memory routing error: Honcho workspace(s) {list(foreign)} hold "
            "sessions and are not marked as this Nora's. Another application "
            "uses them — bind the Nora workspace to a different "
            "honcho_workspace in identity.toml."
        )

    llm_state = LLMState()
    background = BackgroundTasks()
    # R-9's rate limiter, with its window persisted so a force-recreate does
    # not empty it (a duplicate tag arriving seconds after a restart would
    # otherwise run a second turn). `spawn` is REQUIRED alongside the store, not
    # optional: `Debounce.allow` is synchronous by design (an await between
    # admission's QUEUED verdict and the enqueue would break FIFO order), so
    # the row is written fire-and-forget on the loop.
    debounce = Debounce(
        cfg.engagement.trigger.min_interval_seconds,
        store=SqlTagDebounceStore(engine),
        spawn=background.spawn,
    )
    # ONCE, here, before any adapter is listening — `allow` cannot do it for
    # itself. It also prunes the rows whose window has already elapsed, which
    # is what bounds the table without a sweep loop of its own.
    await debounce.hydrate()

    # First match wins, deterministic text first. The mention policy uses the
    # same alias set R-12 uses to refuse self-tags — an @-mention arrives as
    # U+FFFC in the body plus a `mentions` array, so a substring trigger can
    # never see it.
    # EVERY platform's aliases, not just Signal's. These two policies match a
    # mention/quote against "is this Nora?", and on Discord an @mention carries
    # her SNOWFLAKE, which a match set of a phone number and a UUID never holds:
    # the obvious way to address her would produce ingest, attribution and
    # silence, the exact failure `MentionPolicy` exists to end.
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
    # `AdapterRegistry.self_aliases`.
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
    # Any voice note engages her, independent of trigger text — the
    # transcription is conversation content in the
    # body by the time the gate runs. Last so the deterministic text paths
    # keep their reasons, but it fires on any note regardless.
    engage_policies.append(VoiceNotePolicy())
    # Any transcript that arrived via the voice-wake channel engages her too:
    # the wake service only POSTs after the
    # operator said 「喂 Nora」, which is the voice channel's closest thing to
    # an explicit tag — without this, a transcript lands in the audit row and
    # the engage gate says `no_trigger`. Same position as VoiceNotePolicy: after
    # the text paths, fired by content-shape rather than words.
    engage_policies.append(VoiceWakePolicy())
    # The participation occasion's room gate:
    # LAST, and only when the occasion is enabled at all. It never
    # engages — it marks an untagged message in an opted-in room with
    # `EngageDecision.relevance_gate`, which the dispatcher NOTES with the
    # quiescence trigger instead of admitting it. With the flag off, no
    # room's untagged traffic is even considered.
    if cfg.relevance_gate.enabled:
        engage_policies.append(RelevanceGatePolicy(allowlist))
    policy = CompositePolicy(tuple(engage_policies))
    # The room-scoped tools' one platform dependency, built BEFORE the presence
    # reader because the room tools read member counts through it. Built here
    # rather than
    # taken off the adapter because membership is a read-only DIRECTORY query
    # (`nora.platform.signal.directory`'s two-method allowlist), not part of the
    # send/receive/react lifecycle — see `nora.domain.ports.RosterSource`.
    # ONE PER PLATFORM, not one for all of them: signal-cli has never heard of
    # a Discord conversation id and answers `available=False`, so `where_is`
    # and every other room-scoped tool would answer «I could not look» on
    # Discord. Fail-closed and therefore harmless, but invisible: nothing
    # errors and the honest refusal is indistinguishable from the guard doing
    # its job.
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
    if "instagram" in registry:
        # Built for a DEGRADED adapter too, and that is deliberate: the registry registers
        # an Instagram adapter that could not authenticate rather than refusing
        # to start (`nora.platform.registry`), and a checkpoint clears without a
        # restart. A roster withheld on the startup state would then stay
        # withheld for the life of the process — the room-scoped tools silently
        # unavailable long after the session came back. The read itself is what
        # fails closed: a checkpointed bridge answers 409 and the roster is
        # `available=False` for exactly as long as that is true.
        from nora.platform.instagram.roster import InstagramRoster
        rosters["instagram"] = InstagramRoster(cfg.platforms.instagram.base_url)
    # `<presence>`: ONE reader, handed to the tag
    # path's assembler and the fire path, so none of the
    # two holds its own idea of which rooms exist or which networks are up.
    # Over the SAME allowlist ingest admits against and the SAME adapter map
    # the tag path sends through.
    presence_reader = _presence_reader(cfg, allowlist, registry, rosters)
    # THE CULTURE TIER'S READ HALF (DEC-207): ONE
    # read-only reader, handed to the tag path's assembler, the fire path and
    # every Dreamer adaptor whose prompt carries `<culture>`. `None` while
    # `[culture] enabled = false`, the shipped default, so no occasion renders
    # the block — the flag gates the fold and every read together
    # (`CultureConfig`).
    culture_reader: CultureReader | None = (
        SqlCultureStore(engine) if cfg.culture.enabled else None
    )
    # `<lessons>` on tag and fire turns: read-only,
    # bodies of active lessons. `None` while `[lessons] render` is off.
    lessons_reader: ActiveLessons | None = (
        ActiveLessons(SqlLessonStore(engine)) if cfg.lessons.render else None
    )
    # THE SWITCH (design/unified-memory/45 §5, 00 «Sequence» step 8): the
    # Persona accounts' read half — one store, read-only by the Protocol the
    # readers are typed against (`PersonaHeads` has `account_heads` and nothing
    # else), handed to the tag assembler, the fire path, the Cognition
    # grounder and the debrief fold. Wired only when `[persona] enabled` AND
    # `[persona] render` are both on: `enabled` alone is the shadow (the
    # sessions write, nothing reads), and `render` without `enabled` has no
    # store to read. Flipping `render` back off is the whole rollback.
    persona_reader: PersonaHeads | None = (
        SqlPersonaStore(engine, screen=text_offence)
        if cfg.persona.enabled and cfg.persona.render else None
    )
    # The same gate, for the writers (the dream sessions and the graph
    # surface's `remember`): on, a persona write projects its lines into the
    # memory index in its own transaction (45 §1, 25 §7); off is the shadow,
    # and the shadow's lines join at the flip through `nora memory rebuild`.
    persona_index = cfg.persona.enabled and cfg.persona.render
    # The join from a note to its room's account (DEC-207):
    # EVERY id a room is known by — `Note.established_in` holds whichever one
    # the note was born under — within the note's UNIT, since ids are unique
    # per platform and a note carries no platform.
    note_rooms: dict[tuple[str, str], tuple[str, str | None]] = {
        (cid, conv.unit): (conv.primary_id, conv.label or None)
        for conv in allowlist.conversations
        if conv.primary_id and conv.unit
        for cid in conv.ids
    }

    def note_room_for(cid: str, unit: str) -> tuple[str, str | None] | None:
        return note_rooms.get((cid, unit))

    # THE SAME JOIN, for the name resolver: every
    # id a room is known by, within the note's unit, to the room's platform —
    # what `Persons.name_transcript` needs and `Note.established_in` alone
    # cannot supply, since a note carries no platform of its own.
    note_platforms: dict[tuple[str, str], str] = {
        (cid, conv.unit): conv.platform
        for conv in allowlist.conversations
        if conv.primary_id and conv.unit
        for cid in conv.ids
    }

    def note_platform_for(cid: str, unit: str) -> str | None:
        return note_platforms.get((cid, unit))

    # `[memory_index] enabled` (ships off): the one retrieval service, held
    # by the tag path's pre-fetch (`<recalled>`) and by the tool registry's
    # `memory_search` below, one instance for both; `None` keeps the Honcho
    # surface and no pre-fetch.
    memory_search = (
        MemorySearch(
            SqlMemoryIndex(engine), metrics,
            # `[memory_index] embed`: dense and exact seeds, the
            # lexical seed only as the fallback; off, every answer is
            # the fallback's `partial`.
            embedder=LlamaEmbedder(
                cfg.memory_index.embedder_url,
                timeout_seconds=QUERY_TIMEOUT_SECONDS,
                query_model_file=model_file(cfg.memory_index.embedder_model),
            ) if cfg.memory_index.embed else None,
            model=(
                fingerprint(cfg.memory_index.embedder_model)
                if cfg.memory_index.embed else None
            ),
            # The embedding worker's tick: a node waiting less than a
            # few of them is fresh, not a backlog that answers `partial`.
            drain_seconds=cfg.memory_index.drain_seconds,
        )
        if cfg.memory_index.enabled else None
    )
    assembler = build_assembler(
        cfg, audit=audit, memory=memory, persons=persons, metrics=metrics,
        memory_search=memory_search, clock=clock,
        # `None` while `[notes] enabled = false`. `policy=allowlist` is the
        # SAME policy.toml instance the ingest pipeline and `EngagementPipeline`
        # are given below — the assembler needs it for one thing only, naming
        # the unit a room belongs to (`Conversation.unit`, DEC-105),
        # which is what the note index is scoped by. Handing it a different
        # Policy would scope the read to a unit ingest never admitted.
        notes=note_store,
        memos=memo_store,
        policy=allowlist,
        # Standing's READ half. `None` while `[standing] enabled = false`, the
        # shipped default, so `<standing>` renders nothing. Unlike `notes`
        # beside it this needs no `policy=` at all: a position is keyed
        # `(platform, conversation_id)` and is NOT shared across a
        # unit's rooms — two rooms in one social unit share what the
        # unit knows, not where a conversation had got to.
        #
        # It is the WHOLE store rather than a read-only narrowing, and the same
        # instance the debrief's `Wiring` gets below: the fold's non-negotiable
        # is that the read and the write are the same row seen twice, and two
        # objects for one row is how that stops being structurally true.
        standing=standing_store if cfg.standing.enabled else None,
        # The media join. The same instance every other
        # transcript builder holds — the comprehension a spawned job wrote is
        # the one the transcript renders, the one the note tier reads and the
        # one `media_fetch` serves, and separate copies of that decision would
        # be chances to disagree about what a picture shows.
        media_join=media_join,
        # The shared quote-window resolver (DEC-195). Same instance
        # `FirePath` below is given — the point of building it once.
        quote_window=quote_window,
        presence=presence_reader,
        culture=culture_reader,
        # `<persona>`, the switch (45 §5): `None` until `[persona] render` is
        # on beside `[persona] enabled`, and the same reader the fire path,
        # the Cognition grounder and the debrief fold hold.
        persona=persona_reader,
        # `<lessons>`: `None` while `[lessons]
        # render` is off. The same reader the fire path gets.
        lessons=lessons_reader,
        # `<computer>`: `None` while
        # `[computer]` is off; the same reader the fire path gets.
        computer=computer_context,
        documents=documents_context,
        # `<subagents>`: `None` while `[subagent]` is off. The worker is
        # attached below, beside the desk's. Not given to the fire path.
        subagents=subagents_context,
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
    # `dream_loop`'s `scopes` callable — the policy.toml conversations,
    # re-evaluated fresh each tick (cheap: `allowlist` is loaded once at
    # startup and immutable) so a future policy-reload does not need this
    # closure to change. No platform filter: this loop only READS
    # `RecordStore.since`, which needs no adapter for the platform.
    language_scopes: Callable[[], Sequence[str]] = lambda: tuple(  # noqa: E731
        conv.primary_id for conv in allowlist.conversations if conv.primary_id
    )
    # The same closure over the OTHER key. Sorted and de-duplicated: two rooms
    # of one unit are one unit of work, and a set would reorder the tick
    # between runs of the same build — which turns "did adaptor X run for
    # unit Y this tick" into a question you cannot answer from a log.
    unit_scopes: Callable[[], Sequence[str]] = lambda: tuple(sorted(  # noqa: E731
        {conv.unit for conv in allowlist.conversations if conv.unit}
    ))
    # One CLIENT (DEC-13), MANY MODELS — one per tag, built by this
    # source. The model carries `tool_choice`, which `ReplyGuard` flips
    # mid-turn from `required` to `auto`, so it is per-turn
    # mutable state and two concurrent conversations must not share it.
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=tier.model, max_tokens=tier.max_tokens, temperature=tier.temperature,
            reasoning_effort=tier.reasoning_effort,
        ),
    )
    # `computer_browse` (ADR 0022): a sub-agent
    # in the room's own browser, with a model source of its own (one model per
    # task, the tag path's reason above). `None` unless `[computer]` AND
    # `[computer.browse]` are on, so the registry withholds the tool.
    browser: BrowseAgentRunner | None = None
    if computer is not None and cfg.computer.browse.enabled:
        browse_cfg = cfg.computer.browse
        browser = BrowseAgentRunner(
            model_source=factory.ModelSource(client=client, spec=factory.ModelSpec(
                model=browse_cfg.model or cfg.llm.model, max_tokens=browse_cfg.max_tokens,
                temperature=browse_cfg.temperature, tool_choice=tool_choice.AFTER_SPEAKING,
            )),
            retry=factory.build_retry_policy(timeout_seconds=browse_cfg.timeout_seconds),
            base_url=cfg.computer.base_url, token=_computer_token(cfg),
            settings=BrowseSettings(
                max_steps=browse_cfg.max_steps, timeout_seconds=browse_cfg.timeout_seconds,
                read_chars=browse_cfg.read_chars, turns=browse_cfg.turns,
                output_tokens=browse_cfg.output_tokens, total_tokens=browse_cfg.total_tokens,
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
        # truncations_total` has to land where `/metrics` reads it.
        metrics=metrics,
    )
    # The participation occasion's runner —
    # a SECOND `ModelSource`, the shape the Dreamer below uses, and for
    # the same reason: `tool_choice` lives on `ModelSpec`. The determination
    # must be able to end in SILENCE, and the tag runner's `FIRST_CALL`
    # (`required`) pin makes that inexpressible — under it the model must call
    # something on the first pass, and the only terminal call is `send_reply`.
    # So this runner composes `auto`: `ReplyGuard.relax` mid-turn then releases
    # BACK to auto (`tool_choice.require`'s initial-value release), never to
    # required. None while the occasion is disabled; `FirePath` refuses a
    # participation turn that arrives with no runner composed.
    #
    # The proactive occasion (DEC-162)
    # needs the SAME runner shape for the same reason — its act turn may end
    # in silence — so both are built by `_auto_runner` below, each with its own
    # reasoning effort. The act turn is wired when `policy.toml` names a home
    # room; its rows are written by the Cognition session's `remind`
    # (DEC-277).
    proactive_homes = allowlist.home_rooms()

    def _auto_runner(
        effort: str, source: str, *, max_tokens: int | None = None,
    ) -> LiveAgentRunner:
        return LiveAgentRunner(
            models=factory.ModelSource(
                client=client,
                spec=factory.ModelSpec(
                    model=tier.model, max_tokens=max_tokens or tier.max_tokens,
                    temperature=tier.temperature,
                    reasoning_effort=str(_reasoning_effort(effort, source=source)),
                    tool_choice=tool_choice.AFTER_SPEAKING,
                ),
            ),
            retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
            skills_dir=cfg.doctrine.dir.parent / "skills",
            agent_name=cfg.agent.name,
            metrics=metrics,
        )

    # `[participation]`'s own effort, never the tag lane's — see the field's
    # note: silence is this occasion's default, and more reasoning
    # produces more of it.
    participation_runner: LiveAgentRunner | None = None
    if cfg.participation.enabled:
        participation_runner = _auto_runner(
            cfg.participation.reasoning_effort, "[participation] reasoning_effort",
        )
    # The proactive act turn's runner: the same `auto` shape, its OWN effort
    # (`ProactiveConfig`). It is not shared with the participation runner
    # because the two fail in opposite directions: at `none` a proactive turn
    # spoke on every case where silence was right.
    proactive_runner: LiveAgentRunner | None = None
    if proactive_homes:
        proactive_runner = _auto_runner(
            cfg.proactive.reasoning_effort, "[proactive] reasoning_effort",
            max_tokens=cfg.proactive.max_tokens,
        )
    # The public Instagram summons' runner
    # (`nora.app.summons`): the SAME `AFTER_SPEAKING` shape as the
    # participation runner above, for the same reason — a summons may end in
    # silence, which the tag runner's `FIRST_CALL` pin makes inexpressible.
    # Its own, not the participation one, because it is wired on its own
    # flags and its reasoning effort is the tag lane's (she was asked).
    # `skills_dir=None`: runtime skills are a tool outside `PUBLIC_GRANT`.
    summons_runner: LiveAgentRunner | None = None
    if summons_wired:
        summons_runner = LiveAgentRunner(
            models=factory.ModelSource(
                client=client,
                spec=factory.ModelSpec(
                    model=tier.model, max_tokens=tier.max_tokens,
                    temperature=tier.temperature,
                    reasoning_effort=tier.reasoning_effort,
                    tool_choice=tool_choice.AFTER_SPEAKING,
                ),
            ),
            retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
            skills_dir=None,
            agent_name=cfg.agent.name,
            metrics=metrics,
        )
    # `output_tokens` comes from `[llm.limits]`, NOT from `tier.max_tokens`:
    # the two are separate values, and tying them would make the whole
    # loop's output budget equal to one call's ceiling — see
    # `factory.build_limits`.
    limits = factory.build_limits(
        output_tokens=tier.limits.output_tokens,
        turns=tier.limits.turns,
        total_tokens=tier.limits.total_tokens,
    )

    # The reminder caps as the DOMAIN value, built once so every reminder writer (the
    # tag path's `ToolRegistry` and the Cognition session) holds the exact SAME
    # `ReminderCaps`, not two instances equal only by both reading `cfg.reminders`.
    reminder_caps = ReminderCaps(**cfg.reminders.model_dump())
    # `GatheringService`: the ONE use-case layer for
    # Gathering business rules. `event_store` REUSES `engine` for the same reason
    # `reminders`/`compute_jobs`/`tool_call_log_store` do above — a second
    # `create_sql_engine` would fork the pool. `route_source=route_client` is
    # the SAME `RouteClient` (or `None`) `place_info`/`route` already use —
    # a second instance would be a second, unrelated key check and a second
    # client the composition root has no other reason to own. `honcho=memory`
    # and `ai_peer=cfg.memory.ai_peer` are the SAME Honcho client and id
    # already threaded to `TranscriptInjection`/`ToolRegistry` below.
    event_store = SqlGatheringStore(engine)
    # Events stored before units existed carry `unit_id=""` — units live in
    # `policy.toml`, not the DB, so the column cannot be filled — and
    # `search_events`'s unit-wide filter would never find one: it reads a
    # unit's rooms from policy instead.
    event_unit_rooms = _unit_room_ids(allowlist)

    def _event_unit_room_ids(unit_id: str) -> tuple[str, ...]:
        return event_unit_rooms.get(unit_id, ())

    # A venue's district and city come from the Geocoding API by the place id
    # `place_info` resolved it to, with the route key (a Places id is that
    # key's project's, and the key is restricted to Routes + Places +
    # Geocoding). No route client, no venue area.
    place_areas = None
    if route_client is not None:
        from nora.infra.geocode import GoogleGeocoder
        place_areas = GoogleGeocoder(api_key=os.environ.get(cfg.route.api_key_env, ""))
    events = GatheringService(
        store=event_store,
        route_source=route_client,
        honcho=memory,
        ai_peer=cfg.memory.ai_peer,
        unit_room_ids=_event_unit_room_ids,
        place_areas=place_areas,
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
    # The durable half of media dedup: `VisionCache`
    # becomes a read-through in front of THIS store rather than a second,
    # disagreeing copy — REUSING `engine` for the reason `reminders`/
    # `compute_jobs`/`tool_call_log_store` all do above (one pool, one
    # lifecycle; a second `create_sql_engine` would fork the pool and its own
    # `aclose()` would dispose the engine everything else is still using).
    media_descriptions = SqlMediaDescriptionStore(engine, metrics=metrics)
    # The per-modality DIRECT lanes (DEC-197): a describer configured with a direct
    # provider travels through its own client, NOT the process-wide OmniRoute
    # client — OmniRoute drops `video_url` content blocks, so a
    # video described through it would reach the model as text only.
    vision_client = VisionClient(
        client=client,
        direct_clients=_direct_describer_clients(cfg),
        model=cfg.media.vision_model,
        max_tokens=cfg.media.vision_max_tokens,
        reasoning_effort=cfg.media.vision_reasoning_effort,
        max_bytes=cfg.media.vision_max_bytes,
        # THE DERIVATION'S INPUTS, NOT ITS RESULT. The divisor is the SERIAL
        # factor — how many 90-second windows one invocation reads in
        # sequence — and `cut_video` only produces that per request, at read
        # time, a process after this line runs. The client derives per call
        # (`VisionClient._call_timeout`).
        #
        # No budget is passed: budgeted calls run CONCURRENTLY and occupy
        # `max(their durations)` of wall clock, never their sum, so a budget
        # divisor would be the wrong quantity. `budget` still bounds refusals
        # — R-7's ceiling comes from `budgets()`, at `tool_registry` below —
        # it does not bound elapsed time.
        timeout_seconds=cfg.media.vision_timeout_seconds,
        loop_timeout_seconds=tier.timeout_seconds,
    )
    # The sticker IDENTITY ladder — custody, not description. Its
    # model rung is the spawned comprehension job below, so this needs no
    # interpreter and no second `VisionClient`: what it buys is a repeat
    # sighting costing zero platform RPC, decided at parse time before any
    # bytes move.
    # Built whenever custody is on, and NOT gated on `sticker_descriptions`:
    # this ladder is pure custody and reads nothing, so gating it on the READ
    # switch would throw away the dedup. `sticker_descriptions` gates the
    # comprehension job's sticker subjects instead (`stickers_enabled` below).
    sticker_reader = None
    if blobs is not None:
        sticker_reader = StickerReader(
            blobs=blobs,
            descriptions=media_descriptions,
            metrics=metrics,
        )
    # THE COMPREHENSION JOB — one model pass per medium into
    # title/description/content, spawned off the ingest path. Wired only when
    # custody is on AND `[media] comprehension` is set: with either off, every
    # attachment keeps its bare handle and nothing anywhere claims a
    # description it does not have. It reuses `vision_client` — one connection,
    # one budget, the modality picking the model — for `derive_vision_timeout`'s
    # reason (three clients would be three budgets, and three hung calls could
    # outlast the loop).
    comprehension: MediaComprehension | None = None
    if cfg.media.comprehension and blobs is not None:
        comprehension = MediaComprehension(
            blobs=blobs,
            store=media_objects,
            interpreter=MediaComprehender(
                client=vision_client,
                describe_models=_describe_models(cfg),
                describe_providers=_describe_providers(cfg),
                describe_max_bytes=_describe_max_bytes(cfg),
                text_model=cfg.media.comprehension_text_model,
                chunk_seconds=cfg.media.describe.video.chunk_seconds,
                max_chunks=cfg.media.describe.video.chunk_max,
            ),
            memory=memory,
            metrics=metrics,
            spawn=_spawn,
            stickers_enabled=cfg.media.sticker_descriptions,
            max_concurrent=cfg.media.comprehension_max_concurrent,
        )
    # `[speech.stt]`'s transcriber: wired ONLY when enabled — the same
    # inert-ship rule as `stickers`. Off leaves the ingest stage without a
    # transcriber and a voice note is recorded with a bare handle. On, the
    # local `stt` service transcribes voice notes inline during custody (audio
    # stays on the box — the endpoint is reachable only on the private `nora`
    # network).
    transcriber = None
    if cfg.speech.stt.enabled:
        from nora.infra.speech import SenseVoiceTranscriber
        transcriber = SenseVoiceTranscriber(
            endpoint=cfg.speech.stt.endpoint,
            language=cfg.speech.stt.language,
            timeout_seconds=cfg.speech.stt.timeout_seconds,
            model=cfg.speech.stt.model,
        )
    # R-10's per-conversation lock and R-30's FIFO queue, built ONCE here.
    # Every path that may speak into a conversation holds this same instance —
    # two would be two locks and no serialisation at all. Built HERE, above the
    # registry, because the relay below needs it and the registry needs the
    # relay.
    slots = ConversationSlots(
        maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn,
    )

    # The cognition loop's stimuli: OFF
    # under `[cognition] enabled = false`, the shipped default. On, the ingest
    # pipeline appends one stimulus per recorded person-authored item, and the
    # evaluator reads them and starts sessions. The evaluator is built after the
    # Dreamer block below (`_build_wake_evaluator`): it is handed the session
    # runner's hook, and the runner needs the Dreamer's model and tool sources.
    stimulus_wiring: stimulus_stage.Wiring | None = None
    stimulus_store: SqlStimulusStore | None = None
    if cfg.cognition.enabled:
        stimulus_store = SqlStimulusStore(engine)
        stimulus_wiring = stimulus_stage.Wiring(
            store=stimulus_store,
            unit_of=_note_unit_of(allowlist),
            self_ids=registry.self_aliases(),
            reply_quiet_seconds=cfg.cognition.reply_quiet_seconds,
            timeout_seconds=cfg.cognition.store_timeout_seconds,
        )
    ingest = IngestPipeline(
        audit=audit, memory=memory, persons=persons,
        policy=policy, allowlist=allowlist, metrics=metrics, spawn=_spawn,
        media_sources=media_sources, blobs=blobs,
        media_store=media_objects,
        stickers=sticker_reader, transcriber=transcriber,
        comprehension=comprehension,
        language=cfg.speech.stt.language,
        stimulus_wiring=stimulus_wiring,
    )
    # `web_search`/`web_fetch` (DEC-73/DEC-116) are wired ONLY when `[search]
    # enabled` AND the SELECTED backend's key is present — the `route`-keyless
    # gate. Off (or keyless) leaves the registry without a provider and both
    # tools answer `unavailable` (R-5), never a silent empty. The key is
    # resolved from the environment here (DEC-8), never read inside an adapter.
    #
    # DEC-157 makes the backend an operator choice (`provider`), which is the
    # port doing what DEC-73 (4) said it was for. The branch is HERE and only
    # here: one provider, one key, both verbs, and everything downstream —
    # the tools, their egress screen, their budgets, their tier — is handed a
    # `SearchProvider` and cannot tell which vendor it got. Neither backend
    # needs an SSRF gate: each crawls from its OWN servers, never Nora's,
    # whether it is handed a query or a url.
    search_client: SearchProvider | None = None
    if cfg.search.enabled:
        search_key = os.environ.get(cfg.search.api_key_env, "")
        if search_key and cfg.search.provider == "exa":
            from nora.infra.search import ExaSearchProvider
            search_client = ExaSearchProvider(
                api_key=search_key,
                url=cfg.search.exa.url,
                contents_url=cfg.search.exa.contents_url,
                search_type=cfg.search.exa.type,
                max_results=cfg.search.max_results,
                content_max_chars=cfg.search.content_max_chars,
                extract_max_chars=cfg.search.extract_max_chars,
                fetch_max_age_hours=cfg.search.exa.fetch_max_age_hours,
                timeout_seconds=cfg.search.timeout_seconds,
            )
        elif search_key:
            from nora.infra.search import TavilySearchProvider
            search_client = TavilySearchProvider(
                api_key=search_key,
                url=cfg.search.tavily.url,
                max_results=cfg.search.max_results,
                search_depth=cfg.search.tavily.search_depth,
                content_max_chars=cfg.search.content_max_chars,
                timeout_seconds=cfg.search.timeout_seconds,
                extract_url=cfg.search.tavily.extract_url,
                extract_depth=cfg.search.tavily.extract_depth,
                extract_max_chars=cfg.search.extract_max_chars,
                min_score=cfg.search.tavily.min_score,
            )
        else:
            log.warning(
                "search enabled on provider %s but %s is unset — web_search "
                "stays unavailable",
                cfg.search.provider,
                cfg.search.api_key_env,
            )
    # `x_read`, `x_search` (DEC-201), `instagram_read` and `instagram_search`
    # get ONE `SocialProvider` — `SocialRouter`
    # — and the registry stays unaware that there are two backends under it.
    # Each backend is wired independently and there is NO credential on
    # either, so `enabled` alone is the gate (the `[preview]` shape); an
    # unwired backend answers `unavailable` for ITS OWN rows (R-5) and never
    # widens or narrows the other's reach.
    #
    # The DEFAULT is OpenCLI, reaching the host-side bridge over loopback; its
    # read-only (site, command) allowlist is re-enforced THERE
    # (`nora.infra.social.bridge_server`), outside the container.
    social_client: SocialProvider | None = None
    opencli_client = None
    if cfg.social.enabled:
        from nora.infra.social import OpenCliBridgeClient
        opencli_client = OpenCliBridgeClient(
            bridge_url=cfg.social.bridge_url,
            timeout_seconds=cfg.social.timeout_seconds,
            content_max_chars=cfg.social.content_max_chars,
        )
    # The INSTAGRAM backend serves every Instagram read the bridge has a route
    # for — `instagram/story` and `instagram/profile`, `instagram_read`'s two,
    # `instagram/post`, its read of a pasted post or reel link, and
    # `instagram/search`, `instagram_search`'s, which takes free text and needs
    # no index.
    # The two read rows take a PERSON, so the router needs a
    # `SocialSubjectIndex`: `PersonsSubjectIndex` resolves
    # person -> igid off `identity.toml` (kind-aware; none or several is
    # `ambiguous`) and answers Tier 0 off `policy.toml`'s group rooms (a `*`
    # room and `[dm]` never count). `SocialRouter` refuses to be constructed
    # with that backend and no index, so the pairing is not a discipline to
    # remember.
    #
    # Its own `enabled`, because the two backends are provisioned and fail
    # independently: turning Instagram on must not need the host's OpenCLI
    # service, and a checkpointed Instagram session must not take X with it.
    instagram_social = None
    social_subjects = None
    if cfg.social.instagram.enabled:
        from nora.infra.social import InstagramSocialClient, PersonsSubjectIndex
        instagram_social = InstagramSocialClient(
            base_url=cfg.social.instagram.base_url,
            timeout_seconds=cfg.social.instagram.timeout_seconds,
            content_max_chars=cfg.social.content_max_chars,
        )
        social_subjects = PersonsSubjectIndex(persons, allowlist)
    if opencli_client is not None or instagram_social is not None:
        from nora.infra.social import EXCEPTION_ROUTES, SocialRouter
        social_client = SocialRouter(
            default=opencli_client,
            exceptions=(
                dict.fromkeys(EXCEPTION_ROUTES, instagram_social)
                if instagram_social is not None else {}
            ),
            subjects=social_subjects,
            # A typed handle or a pasted link names a USERNAME, and the
            # bridge's exact lookup is the only thing that turns one into an
            # igid. The same client:
            # the lookup is a GET on the same bridge.
            accounts=instagram_social,
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
    # `online_now`'s Steam half is wired ONLY when
    # `[online] enabled` AND the key the section NAMES is present — the
    # enabled-AND-keyed gate `search` uses. Off, or keyless, leaves the
    # registry without a provider and the `online`/`playing` arms answer
    # `unavailable` (R-5's honest «not configured»), never a 403 per call.
    # The `voice` arm is NOT gated here — see its own block at the registry
    # build below.
    steam_client: SteamPresenceProvider | None = None
    if cfg.online.enabled:
        steam_key = os.environ.get(cfg.online.api_key_env, "")
        if steam_key:
            from nora.infra.steam import SteamPresenceClient
            steam_client = SteamPresenceClient(
                api_key=steam_key,
                timeout_seconds=cfg.online.timeout_seconds,
            )
        else:
            log.warning(
                "online enabled but %s is unset — online_now's Steam arms "
                "stay unavailable",
                cfg.online.api_key_env,
            )
    # `whereis` (DEC-59) is wired ONLY when
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
    # present → build the lane (the same keyless-`enabled` gate `route` uses).
    # Off (or tokenless) leaves the `generate_image` tool answering
    # `unavailable` — never a crash, never `nothing_found` (R-5/R-27). The
    # state files live in the same repo-tree state dir `./state/signal-cli`
    # already uses (container-writable, host-immutable `.env` holds only the
    # bootstrap refresh tokens).
    #
    # DEC-308: EVERY OAuth lane whose token is set gets its lane object, not
    # only the selected provider's, and each is registered with the one
    # keepalive so no login dies idle. The selected lane's object is the SAME
    # one the adapter holds: one asyncio lock, so the keeper and a call never
    # both spend a rotating token. `nora/__main__.py` starts the keeper; the
    # eval subject composes the same graph and never does.
    image_refs_allowed = (
        cfg.image_gen.enabled and cfg.image_gen.allow_reference_images
    )
    # Image custody: generated images are stored in Nora's
    # OWN blob_store (rustfs) and sent as platform attachments.
    image_generator, oauth_keepalive = _image_gen_wiring(
        cfg, metrics, os.environ, state_dir=Path("/app/state"),
    )
    # Sender-generated link previews ([preview]). Keyless, so `enabled` is the
    # whole gate. Off is the shipped default and is not a
    # degradation: a reply carrying a link goes out with no card. It is ONE instance for the
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
            # load-bearing, so a cut-off just falls back to the stored fix.
            timeout_seconds=cfg.presence.refresh_timeout_seconds + 2.0,
        )
    # The on-demand reverse-geocoder: `Whereis.locate` (nora/agent/whereis.py)
    # calls it ONLY when a fix has no address and someone actually asked
    # `where_is` about it. The collector talks to Life360 alone, never Google:
    # geocoding every fix for every tracked person on every cadence tick would
    # bill Google for addresses nobody reads. Switchable behind the SAME
    # `ReverseGeocoder` port (`[presence] geocode_provider`, "google" default
    # or "hkgov").
    # The durable half of the cache: survives a restart, and is
    # what `nora.analytics` reads (READ-ONLY — it never calls a geocoder
    # itself) to label a derived place. REUSES `engine` for the same reason
    # `presence_store` above does — a second `create_sql_engine` would fork
    # the pool.
    from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
    geocode_cache_store = SqlGeocodeCacheStore(engine)
    geocoder = reverse_geocoder(cfg, os.environ, cache_store=geocode_cache_store)
    # The generic deferred-delivery path: the `ComputeBackend` port,
    # `ComputeJobPoller`, `compute_jobs` and boot recovery. The poller below is
    # never built while no backend is registered.
    # `kind -> ComputeBackend`: the poller resolves a row's backend by its `kind`.
    # `[video_gen]` registers the video backend just below; the
    # sub-agent worker is registered once the tool registry exists.
    compute_backends: dict[str, ComputeBackend] = {}
    # Video generation ([video_gen]): enabled AND the key named by
    # `[video_gen.minimax_cn] api_key_env` present builds the MiniMax CN backend, registered
    # under `VIDEO_KIND` for the poller, and the desk `video_create` calls. The config refuses
    # `enabled` without `[compute] enabled`. Enabled without the key builds nothing: the
    # registry withholds the tool and this warns, never a crash (R-5/R-27).
    video_desk: VideoDesk | None = None
    if cfg.video_gen.enabled:
        video_cfg = cfg.video_gen.minimax_cn
        video_key = os.environ.get(video_cfg.api_key_env, "")
        if video_key:
            from nora.infra.video_gen import MiniMaxVideoBackend
            video_backend = MiniMaxVideoBackend(
                api_key=video_key, model=video_cfg.model,
                resolution=cfg.video_gen.resolution,
                duration_seconds=cfg.video_gen.duration_seconds, ratio=cfg.video_gen.ratio,
                metrics=metrics, host=video_cfg.host,
                timeout_seconds=video_cfg.timeout_seconds,
                reference_seconds=cfg.video_gen.reference_seconds,
            )
            compute_backends[VIDEO_KIND] = video_backend
            video_desk = VideoDesk(
                jobs=compute_jobs, backend=video_backend, clock=clock,
                tz=cfg.analytics.tz_name,
                max_per_room_per_day=cfg.video_gen.max_clips_per_room_per_day,
            )
        else:
            log.warning(
                "[video_gen] enabled but %s is unset; video_create is withheld",
                video_cfg.api_key_env,
            )
    # Nora's computer (DEC-243): `ComputerdRuntime`,
    # built ONLY when `[computer] enabled`. Off leaves it `None` and every
    # computer tool answers `unavailable` (R-5). `ComputerToolSettings` is the
    # conversion point from config to a domain value (`nora.agent` may not
    # read `nora.config`), the `ReminderCaps` shape.
    # (`computer`, the runtime, is built above, beside its session store.)
    computer_settings = ComputerToolSettings(
        run_timeout_seconds=cfg.computer.run_timeout_seconds,
        max_run_timeout_seconds=cfg.computer.max_run_timeout_seconds,
        max_output_chars=cfg.computer.max_output_chars,
        max_read_chars=cfg.computer.max_read_chars,
        export_max_bytes=cfg.computer.export_max_bytes,
        import_max_bytes=cfg.computer.import_max_bytes,
        limits=cfg.computer.limits.as_pairs(),
        idle_after_minutes=cfg.computer.sessions.idle_after_minutes,
        lapsed_visible_hours=cfg.computer.sessions.lapsed_visible_hours,
    )
    # `computer_export`: its record of what left the
    # computer, built unconditionally like the audit store. The same store is the
    # portal's Artifacts read (ADR 0023), and a link is the portal's
    # `<[documents] public_url>/artifacts/<artifact_id>`: without a `public_url` a
    # link export answers `unavailable`, never the boot (`chat` and `keep` need none).
    computer_exports: ComputerExportStore = SqlComputerExportStore(engine)
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
    # instance, read by the `version` tool. Two would be two reads of the same
    # file and, worse, two answers to "what am I running" that a mid-boot file
    # change could split. Absent in a source
    # checkout (it is generated by `scripts/build-image.sh` and gitignored),
    # which is why every version answer degrades to `unavailable` here rather
    # than to a guess.
    version_manifest = VersionManifest()
    giphy_client = None
    if giphy_key := os.environ.get("GIPHY_API_KEY"):
        from nora.infra.giphy import GiphyClient
        giphy_client = GiphyClient(api_key=giphy_key)
    reaction_picker = None
    if cfg.reaction.enabled and giphy_client is not None:
        from nora.agent.reaction import ReactionPicker
        reaction_picker = ReactionPicker(
            client=_DebriefModelClient(
                base_url=cfg.llm.base_url,
                api_key=os.environ.get(cfg.llm.api_key_env, ""),
                max_tokens=cfg.reaction.max_tokens,
                temperature=0.3,
                model=cfg.reaction.model or cfg.llm.model,
                timeout_seconds=cfg.reaction.timeout_seconds,
                reasoning_effort=_reasoning_effort(
                    cfg.reaction.reasoning_effort or cfg.llm.reasoning_effort,
                    source="[reaction] reasoning_effort",
                ),
                agent_name="reaction",
            ),
            prompts=doctrine_store, gifs=giphy_client, metrics=metrics,
            timeout_seconds=cfg.reaction.timeout_seconds,
        )
    games_client = None
    if pulse_api_token := os.environ.get("PULSE_API_TOKEN"):
        # The base URL names a private tailnet host, so the deployment states it
        # in `.env` (`PULSE_API_BASE`); the source holds only a placeholder.
        games_client = PulseGamesClient(
            token=pulse_api_token,
            base_url=os.environ.get("PULSE_API_BASE") or PULSE_API_BASE,
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
            # (heartbeat, participation and compute all off). `failed_error` is
            # what `send_to` turns into an honest «I couldn't».
            log.warning("relay asked to speak with no fire path wired")
            return "failed_error"
        return await fire_path.speak(occasion)

    relay = Relay(
        policy=allowlist, speak=_relay_speak, slots=slots,
        occasion_for=relay_occasion, metrics=metrics,
        # The SAME reader `<presence>` renders from,
        # so «Discord is down» in the block and `send_to` refusing into Discord
        # are one read of one mapping, never two opinions.
        transport=presence_reader.state,
    )
    # `DiscordMusicToolBridge` — the
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
                hub=voice_hub, voice_guild_ids=voice_guild_ids,
                resolve_music_channel=resolve_music_channel,
            )
        else:
            log.error(
                "internal error: registry's 'discord' entry is not a DiscordAdapter "
                "— music agent tools stay unavailable"
            )
    # `DiscordVoicePresence` — `online_now`'s voice
    # arm. Built whenever a Discord adapter exists: it reads the live
    # client's guild cache (`voice_states` is already in the default
    # intents), so unlike the Steam half there is no `[online]` gate and
    # no key — the same narrowing `music_bridge` above needs, for the same
    # `current_client()` reason.
    voice_presence: VoicePresenceSource | None = None
    # `DiscordPresence` — the `online` arm's Discord half: per-person presence
    # (status + rich presence), NOT voice membership. Same construction rule as
    # `voice_presence`: it reads the live Discord client (with the
    # `presences` intent enabled), so no `[online]` gate and no key.
    discord_presence: DiscordPresenceSource | None = None
    if "discord" in registry:
        discord_adapter = registry.get("discord")
        if isinstance(discord_adapter, DiscordAdapter):
            from nora.platform.discord.discord_presence import DiscordPresence
            from nora.platform.discord.voice_presence import DiscordVoicePresence
            voice_presence = DiscordVoicePresence(adapter=discord_adapter)
            discord_presence = DiscordPresence(adapter=discord_adapter)
        else:
            log.error(
                "internal error: registry's 'discord' entry is not a DiscordAdapter "
                "— online_now's Discord arms stay unavailable"
            )
    # LINEAGE: the
    # ONE value that says who each room of the policy is, held by the fire
    # path below and by `record_query` through the registry. The tag path's
    # assembler builds the same value from the same three inputs
    # (`build_assembler`).
    lineage = lineage_policy(cfg, policy=allowlist, persons=persons)
    # The sub-agent desk the four `subagent_*` tools call: built ONLY under
    # `[subagent] enabled`, so off the registry withholds the tools. The worker is attached below,
    # once built, because its closed tool list is built from this registry.
    subagent_desk = SubagentDesk(
        jobs=compute_jobs, clock=clock,
        documents=document_service if cfg.documents.enabled else None,
    ) if cfg.subagent.enabled else None
    tool_registry = ToolRegistry(
        # `[external_files]` (ships off): the source a shared file link is
        # opened through; None answers the open `unavailable`.
        external_files=external_files_source(
            cfg, google=google_credentials(cfg, metrics=metrics),
        ),
        subagent=subagent_desk,
        video=video_desk,
        video_ref_kinds=frozenset(
            kind for kind, allowed in (
                ("image", cfg.video_gen.allow_reference_images),
                ("video", cfg.video_gen.allow_reference_video),
                ("audio", cfg.video_gen.allow_reference_audio),
            ) if allowed
        ),
        # `[record_query] enabled` (ships off): without the read the registry
        # withholds the tool from every turn.
        record=SqlRecordQuery(engine) if cfg.record_query.enabled else None,
        # `[memory_index] enabled` (ships off):
        # one memory surface: `memory_search` over the index; off, the Honcho
        # verbs as before (DEC-316, `nora.agent.tools.GRAPH_MEMORY_TOOLS`).
        # Built once above: the assembler's pre-fetch holds the same one.
        memory_search=memory_search,
        # The graph surface's `remember` writes a Persona `told` line
        # (design/unified-memory/45); wired with `memory_search`, never alone.
        persona=(SqlPersonaStore(engine, screen=text_offence, index=persona_index)
                 if memory_search is not None else None),
        # THE SWITCH (45 §5): while `[persona] render` is on, `character_read`
        # answers from the Persona accounts — the same reader the tag
        # assembler and the fire path hold, so tool and prompt cannot disagree.
        persona_read=persona_reader,
        lineage=lineage,
        # The current Rollup build's rows and the Standing head. SELECTs, so no
        # flag. The Standing head is Dreamer-only (`DREAMER_ONLY_TOOLS`); the
        # Rollup read is also a tag turn's (`TAG_READABLE_LOOP_TOOLS`), under
        # the same lineage as the session's.
        rollup=SqlRollupRead(engine),
        standing_head=standing_store,
        # `character_read`: withheld while `[culture]` is off.
        culture=culture_reader,
        # `social_read`: the social stream's recorded items, a SELECT, so no
        # flag (an empty Record answers `nothing_found`). Its store leaves out
        # every item of a person whose `identity.toml` block sets `social =
        # false`, the same set the poller's store is given.
        social_items=readable_social_store(engine, persons),
        # `memo_read` (DEC-306): the memo store, unit-resolved like the note reads.
        memos=memo_store,
        route=route_client, vision=vision_client,
        describe_models=_describe_models(cfg),
        describe_providers=_describe_providers(cfg),
        # `[media.describe.video]`'s chunk geometry: the SAME two
        # numbers the comprehension pass is built with just above, so
        # `media_fetch`'s deliberate look reads a clip exactly as far as the
        # automatic read did.
        video_chunk_seconds=cfg.media.describe.video.chunk_seconds,
        video_max_chunks=cfg.media.describe.video.chunk_max,
        media_join=media_join,
        describe_max_bytes=_describe_max_bytes(cfg),
        blobs=blobs, descriptions=media_descriptions,
        video_readings=SqlVideoReadingStore(engine), search=search_client,
        social=social_client,
        # The same router, for the two LinkedIn tools, only when LinkedIn is
        # switched on. The config
        # refuses `[social.linkedin]` without `[social]`, so a router exists.
        linkedin=social_client if cfg.social.linkedin.enabled else None,
        weather=weather_client,
        presence=presence_store, circles=presence_circles,
        # `where_been`'s read seam (DEC-155): the SAME `SqlMobilityStore` the
        # web dashboards read through, built once above on the shared engine —
        # no second store, no new lifecycle (one pool, one lifecycle).
        mobility=mobility,
        relay=relay,
        # `room_list`/`room_read` read through the
        # SAME presence reader `<presence>` renders from: one model of which
        # rooms exist, not a second one for the tools.
        rooms=presence_reader,
        refresher=refresher,
        geocoder=geocoder,
        ai_peer=cfg.memory.ai_peer,
        mcp_tools=mcp_tool_groups,
        # The SAME store the tick reads from — two would be two queues, and the
        # rows `remind` wrote would sit in one while the heartbeat watched the
        # other.
        reminders=reminders,
        # The unified media index store — same engine, same lifecycle.
        media_store=media_objects,
        # The document tools: ships OFF, and off the
        # registry withholds all three from every turn.
        documents=document_service if cfg.documents.enabled else None,
        # the six event tools (`create_event`/`update_event`/`rsvp_event`/
        # `cancel_event`/`list_events`/`suggest_venue`) answer `unavailable`
        # (R-5) whenever a composition has not wired one; every deployment
        # here has, since it needs nothing this file does not already build.
        events=events,
        # The third consumer of `tool_call_log_store` (the first two are
        # EngagementPipeline/FirePath, via `deliver.build`) —
        # `recall_lookups`'s own collaborator.
        tool_call_log_store=tool_call_log_store,
        # Nora's computer: `computer` is `None` whenever `[computer] enabled`
        # is false; the audit store is always wired (see where it is built).
        computer=computer, computer_events=computer_events,
        computer_settings=computer_settings,
        computer_sessions=computer_sessions, computer_leases=computer_leases,
        computer_exports=computer_exports,
        artifact_link_base=cfg.documents.public_url,
        browser=browser,
        # The reminder caps as the DOMAIN value, the SAME `reminder_caps` instance the
        # Cognition session is built with — see that variable's own comment for
        # why they must not be two separately constructed copies.
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
        clock=clock,
        # The note tier's four tools. `None` while `[notes] enabled = false`,
        # so all four answer `unavailable` (R-5) rather than a guessed empty
        # result — the same optional-collaborator shape `events` and
        # `reminders` above use. `note_unit` resolves a room to its
        # `Conversation.unit` (DEC-105), because a note is unit-keyed
        # while every tool argument the model can name is room-scoped; without
        # it the tools would have no way to reach the right notebook. The
        # SAME `allowlist` the assembler is given, for the reason stated there.
        notes=note_store,
        # RESOLVER, not a constant — see `build_tools`' own note. This
        # deployment has two units (`ridgeline`, `friends`) that
        # `policy.toml` says are never paired, so a fixed string would hand one
        # room the other's notebook. `None` for a room policy cannot name, which
        # the note verbs report as `unavailable` rather than guessing a
        # notebook. Same `allowlist` instance the assembler and ingest hold.
        note_unit=_note_unit_of(allowlist),
        # ONE resolver for every unit-keyed verb, so a verb outside the
        # note tier does not silently depend on the NOTE tier being wired —
        # two absences covering for each other.
        unit_for=_note_unit_of(allowlist),
        versions=version_manifest,
        giphy=giphy_client,
        reaction=reaction_picker,
        music=music_bridge,
        # `online_now`'s two live-state reads. Either
        # None leaves its arms answering `unavailable` (R-5), never
        # `nothing_found` — the same optional-collaborator shape `mobility`
        # above uses.
        online=steam_client,
        voice=voice_presence,
        discord_presence=discord_presence,
        image_generator=image_generator,
        image_refs_allowed=image_refs_allowed,
        # `[llm.budgets]`, the per-deployment delta over the table. Threaded
        # here for the same reason `reminder_caps` is: `nora.agent` may not
        # import `nora.config`, so the composition root is the conversion
        # point. Names are validated at config load, so anything arriving here
        # is a tool that exists.
        budget_overrides=cfg.llm.budgets,
    )
    # The sub-agent worker, registered under its kind for the poller below.
    # OFF by default: nothing is built and `compute_backends` stays empty. It needs the tool
    # registry (its closed tool list is built from it), which is why it sits here.
    subagent_backend: SubagentBackend | None = None
    if cfg.subagent.enabled:
        subagent_backend = build_subagent_backend(
            cfg=cfg, client=client, tier=tier, registry=tool_registry, store=audit,
            persons=persons, honcho=memory, metrics=metrics, rosters=rosters,
            self_ids=registry.self_aliases(), clock=clock, result_caps=TOOL_RESULT_CAPS,
            reasoning_effort=str(_reasoning_effort(
                cfg.subagent.reasoning_effort, source="[subagent] reasoning_effort",
            )),
            jobs=compute_jobs,
        )
        compute_backends[SUBAGENT_KIND] = subagent_backend
        if subagent_desk is not None:
            subagent_desk.attach(subagent_backend)
        if subagents_context is not None:
            subagents_context.attach(subagent_backend)
    if cfg.compute.enabled and not compute_backends:
        log.warning(
            "[compute] enabled = true but no ComputeBackend is registered (the "
            "per-job container backend was retired, design/sandbox-runtime "
            "decision 4; `[subagent] enabled` registers the sub-agent worker, `[video_gen]` "
            "the video backend); the "
            "compute-job poller stays idle. Set enabled = false."
        )
    if presence_store is not None:
        # The R-21/R-31 startup invariant (DEC-54(4)): alarm the day a policy
        # conversation is no longer a subset of a circle. A BACKGROUND task, not
        # a startup gate — it needs the signal-cli roster, and an unreadable
        # roster must not stop Nora
        # (the AUTHORITATIVE fail-closed check is per-call in `Whereis.locate`;
        # this is the belt-and-braces pre-flight its docstring names).
        background.spawn(_presence_preflight(
            allowlist=allowlist, rosters=rosters, persons=persons,
            circles=presence_circles, metrics=metrics,
            # Without her aliases Nora HERSELF is the «1 unmapped account»: her
            # own membership resolves to nobody in identity.toml, exactly the
            # stranger-shape the warning exists for. The tag path's Room gets
            # the same set (`EngagementPipeline._run_admitted`).
            #
            # EVERY platform's aliases, not Signal's alone: this pre-flight
            # walks Discord rooms too, and Nora's SNOWFLAKE is what identifies
            # her there. Signal-only would count her own bot account as one of
            # the room's unmapped strangers.
            self_ids=registry.self_aliases(),
        ))
    # The debrief's collaborators, as ONE object that is either present or
    # absent (`debrief.Wiring` — the shape is deliberate: four optional
    # parameters would let a deployment wire the store and forget the client,
    # which runs the stage, finds nothing to do, and reports nothing).
    #
    # `[standing] model` empty means `[llm] model`, the defaulting every
    # per-subsystem model key in this file uses. A separate name is
    # available because the fold is a different job from replying — one
    # structured output, no tools, no audience — and may want a cheaper or a
    # more literal model; empty is the honest default rather than a guess.
    #
    # `objective=dreamer_instructions` is the SAME hot-reloading instruction
    # substrate every Dreamer adaptor holds, read through `objective_body` and
    # NOT `for_objective`: the latter prepends the Dreamer's own doctrine,
    # which is research-framed, and a research-framed doctrine on a task with
    # no research makes the model run away with output.
    # under `nora/dreamer/objectives/` says nothing about Standing being a
    # Dreamer adaptor — it is a tag-path stage; that directory is simply where
    # the bind-mounted, hot-reloading instruction text lives.
    debrief_wiring: DebriefWiring | None = None
    if cfg.standing.enabled:
        debrief_wiring = DebriefWiring(
            store=standing_store,
            media_join=media_join,
            client=_DebriefModelClient(
                base_url=cfg.llm.base_url,
                api_key=os.environ.get(cfg.llm.api_key_env, ""),
                max_tokens=cfg.llm.max_tokens,
                temperature=cfg.llm.temperature,
                model=cfg.standing.model or cfg.llm.model,
                timeout_seconds=cfg.standing.timeout_seconds,
                # Empty means inherit `[llm] reasoning_effort` — the same
                # defaulting `model` above uses.
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
            record_store=audit,
            max_chars=cfg.standing.max_chars,
            fold_batch=cfg.standing.fold_batch,
            timeout_seconds=cfg.standing.timeout_seconds,
            store_timeout_seconds=cfg.standing.store_timeout_seconds,
            working_set_max_items=cfg.standing.working_set_max_items,
            working_set_max_chars=cfg.standing.working_set_max_chars,
        )
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
        limits=limits, defer_tools=cfg.llm.defer_tools,
        metrics=metrics, spawn=background.spawn, llm_state=llm_state,
        reactions=cfg.engagement.reactions.enabled,
        rosters=rosters,
        preview_source=preview_source,
        slots=slots,
        clock=clock,
        tool_call_log_store=tool_call_log_store,
        reasoning_strip_store=reasoning_strip_store,
        tag_trace_store=tag_trace_store,
        debrief_wiring=debrief_wiring,
        # The shared decision log. The tag path only
        # WRITES: somebody addressed her, so it is never in doubt about
        # whether to speak. What it writes is what the other two
        # deciders could not see.
        decisions=decision_log,
    )
    # The Dispatcher is built LATE, below the fire path — the participation
    # trigger it may carry needs `fire_path.speak`, which does not exist yet
    # here. Nothing between here and there consumes it.

    # Boot recovery, UNCONDITIONALLY — deliberately NOT behind
    # `cfg.heartbeat.enabled`. A crash leaves rows FIRING, and a heartbeat that
    # has been switched off is exactly the state in which a stranded row would
    # otherwise sit there forever: at-most-once would silently stop applying on
    # the very config that looks safest. Only the LOOP is gated.
    #
    # `recover` counts what it sweeps, so a crash that ate reminders is visible
    # to dashboards.
    await recover(store=reminders, metrics=metrics, now=clock())
    # Compute-job boot recovery, ALSO unconditional, and for the SAME reason —
    # deliberately NOT behind `cfg.compute.enabled`: a deployment that crashes
    # mid-delivery and then flips `[compute] enabled=false` would otherwise
    # leave any `FIRING` `ComputeJob` row stranded forever.
    # The rows are RETURNED so the people waiting on them can be told. Sweeping
    # them terminal is right and not claiming they failed is right, but without
    # a delivery nobody would be told ANYTHING: the requester heard «I'm
    # working on it» and then nothing, ever. Delivered below, once `fire_path`
    # exists.
    lost_jobs = await compute_poller_recover(
        store=compute_jobs, metrics=metrics, now=clock(),
        # A video row's handle is the provider's task id, not memory: when the video backend
        # is wired its pending rows are polled again rather than swept as lost.
        durable_kinds=(
            frozenset({VIDEO_KIND}) if VIDEO_KIND in compute_backends else frozenset()
        ),
    )
    # The liveness gauge is seeded HERE, not in `HeartbeatLoop.__init__` alone —
    # otherwise every deployment on the shipped default (`enabled = false`)
    # exports 0.0 forever, which reads identically to "ticked a moment ago".
    publish_idle_gauge(metrics)
    # Three occasions need a fire turn: a reminder coming due, a compute job
    # that has finished and needs delivering, and a participation decision.
    fire_path: FirePath | None = None
    # `or cfg.compute.enabled`: without this, a deployment that enables
    # `[compute]` alone (heartbeat off) would build
    # `compute_backends` and accept deferred jobs, but the poller below —
    # which needs `fire_path` to deliver a finished job — would never spawn,
    # and every such job would sit `pending` forever with no path to ever
    # reach the group it was asked into.
    if cfg.heartbeat.enabled or cfg.compute.enabled or cfg.participation.enabled:
        # The real fire turn. It holds the SAME collaborators the tag path does
        # — one record store, one Honcho client, one runner, one tool registry —
        # because a fire turn is a turn in the same conversation and a second
        # set would be a second Nora with her own memory and her own budgets.
        # It reads doctrine at `applies_to="heartbeat"` itself.
        fire_path = FirePath(
            persons=persons, records=audit, memory=memory,
            lineage=lineage,
            media_join=media_join,
            # The shared quote-window resolver (DEC-195). The SAME instance
            # `build_assembler` above holds — a fire turn reads the same
            # `context_window`-sized transcript a tag reply does, and a reply
            # INSIDE that window can still quote a parent outside it, whether
            # or not this particular turn was triggered by one message in
            # particular.
            quote_window=quote_window,
            doctrine_store=doctrine_store, metrics=metrics,
            runner=runner, adapters=dict(registry.adapters), registry=tool_registry,
            canary=cfg.doctrine.canary, display_name=cfg.doctrine.display_name,
                ai_peer=cfg.memory.ai_peer,
            context_window=cfg.store.context_window,
            limits=limits, defer_tools=cfg.llm.defer_tools,
            # `[heartbeat]`, not `[llm]`: nobody is waiting on a fire turn, so
            # the 60s chosen for a person staring at a group chat is the wrong
            # bound.
            timeout_seconds=cfg.heartbeat.timeout_seconds,
            clock=clock,
            rosters=rosters,
            # The SAME preview source the tag path holds. A fire turn is a
            # message into the same group, so a link in it earns the same card.
            preview_source=preview_source,
            llm_state=llm_state,
            tool_call_log_store=tool_call_log_store,
            reasoning_strip_store=reasoning_strip_store,
            # The reminder-derived Event registry (DEC-139): kind -> factory, wired here at
            # the composition root so the domain type stays free of app
            # wiring. The registry holds the kinds whose Events are PRODUCED
            # BY a due Reminder row; compute/relay/lost-job call speak
            # directly and are not registry entries. An event does not own
            # a reminder (DEC-303): a reminder is a person's or Nora's own.
            event_registry={
                "reminder": reminder_occasion,
                # The proactive act turn: a row the Cognition session's
                # `remind` writes, built `person_id=None`. Registered
                # unconditionally — a row of this kind can only exist if a writer
                # was once wired, and an unregistered kind fails the
                # row `failed_error` with no way to say why.
                "proactive": proactive_occasion,
            },
            # `<standing>` renders on the fire path too. A fire turn
            # speaks into a room UNPROMPTED, so «where had this conversation
            # got to» is the context she is least likely to have any other
            # way: there is no tagging message to infer it from. Reads only;
            # the fire path writes no position (see `FirePath.__init__`).
            standing=standing_store if cfg.standing.enabled else None,
            # THE CULTURE HEAD (DEC-207) — the SAME reader
            # the tag assembler holds. Keyed on `primary_id`, which is why this
            # path resolves the occasion through the policy it already holds
            # before reading (`FirePath._read_culture`).
            culture=culture_reader,
            # THE SWITCH (45 §5): the SAME persona reader the tag assembler
            # holds; `None` until `[persona] render` is on.
            persona=persona_reader,
            lessons=lessons_reader,
            standing_store_timeout_seconds=cfg.standing.store_timeout_seconds,
            # The participation turn's memory reads. ONE knob set,
            # read at the composition root exactly as the tag assembler reads
            # `tagger_card_conclusions` — two readers each carrying their own idea
            # of how much memory a prompt may hold is how the fire path came to
            # render none at all. Not gated on `[participation] enabled`: every
            # read is gated on `occasion.kind == "participation"` in
            # `_read_memory`, which is a condition no other occasion can meet,
            # and a second gate here would be a place for the two to disagree.
            people_in_window_conclusions=cfg.memory.people_in_window_conclusions,
            people_in_window_speakers=cfg.memory.people_in_window_speakers,
            memory_read_timeout_seconds=cfg.memory.read_timeout_seconds,
            # The shared decision log, written from
            # `_determined` — the one method all four determination
            # exits already converge on. Participation and proactive
            # act turns only; a reminder firing is not a decision
            # about whether to speak.
            decisions=decision_log,
            # `<presence>` — the SAME reader the tag path's assembler holds, so
            # a participation turn or a reminder knows where it is the way a
            # tag does.
            # `<computer>`, the reader the tag
            # path's assembler holds; `None` while `[computer]` is off.
            computer=computer_context,
            documents=documents_context,
            presence=presence_reader,
            # The note tier: the participation determination grounds
            # on «transcript + standing/notes». Wired for every
            # fire occasion, gated exactly like the assembler's reads above:
            # the SAME store instance (already `None` while the tier's
            # `enabled = false`), and the SAME `allowlist` the assembler scopes
            # its unit reads through — two readers each carrying their own
            # idea of a room's unit is the DEC-105 violation.
            notes=note_store,
            memos=memo_store,
            policy=allowlist,
            # The participation occasion's own runner — None whenever the
            # occasion is disabled, which is also exactly when no participation
            # Event can reach `speak` (no policy marks one, no trigger exists).
            participation_runner=participation_runner,
            # The proactive act turn's runner: the same auto-runner,
            # under its own name. Gated on a HOME ROOM existing, not on the
            # adaptor being wired: a proactive row written
            # yesterday still comes due after `[dreamer] enabled` is flipped
            # off, the screen below admits it, and a fire path with no runner
            # would fail every such row `failed_error`. The row's existence
            # condition is the home room; so is the runner's.
            proactive_runner=proactive_runner,
            # The shadow mirror: wired only when the operator turned BOTH
            # the occasion AND the review surface on — a mirrored determination
            # always cleared the room opt-in and the enabled gate first. The
            # channel id is validated non-empty by `ParticipationConfig` when
            # `shadow = true`.
            shadow_channel=(
                cfg.participation.shadow_channel
                if cfg.participation.enabled and cfg.participation.shadow else None
            ),
            shadow_platform=cfg.participation.shadow_platform,
            # The Relevance Gate's OWN review channel, wired
            # only when the gate is on AND in shadow. Separate from the
            # mirror above because the two report different things: that one
            # carries what she WOULD HAVE SAID, this one carries what the
            # gate ADMITTED with no turn behind it at all.
            gate_shadow_channel=(
                cfg.relevance_gate.shadow_channel
                if cfg.relevance_gate.enabled and cfg.relevance_gate.shadow else None
            ),
            gate_shadow_platform=cfg.relevance_gate.shadow_platform,
        )
    if lost_jobs and fire_path is not None:
        # Spawned, never awaited — a boot must not wait on a model call, and
        # `BackgroundTasks` holds the strong reference, exactly as the
        # heartbeat and the poller do.
        background.spawn(_tell_about_lost_jobs(
            jobs=lost_jobs, speak=fire_path.speak, slots=slots, metrics=metrics,
        ))
    elif lost_jobs:
        # No fire path on this deployment, so there is no way to say it. Loud
        # rather than silent: somebody is still waiting on each of these.
        log.error(
            "%d compute job(s) lost across the restart and NOBODY CAN BE TOLD — "
            "this deployment wires no fire path (heartbeat, participation and "
            "compute all off)", len(lost_jobs),
        )

    # THE FIRE-TIME SCREEN for a `kind="proactive"` row, built
    # unconditionally. It is a pure value with no side effects. The screen
    # belongs to the ROW, not to the loop, so asking "would this row be
    # refused" should not require turning the loop on.
    proactive_screen = ProactiveScreen(policy=allowlist, notes=note_store)

    if cfg.heartbeat.enabled and fire_path is not None:
        heartbeat = HeartbeatLoop(
            store=reminders,
            # The SAME `slots` the engagement pipeline holds. Two instances
            # would be two locks and no serialisation at all.
            slots=slots,
            # `allowlist` — policy.toml — NOT `policy`, which is the
            # CompositePolicy deciding whether a message is a tag and has no
            # `is_allowed` at all. Re-checking the ALLOWLIST at fire time is the
            # whole point of this parameter: the file is read once at
            # startup and a row may be thirty days old.
            policy=allowlist,
            clock=clock,
            metrics=metrics,
            tick_seconds=cfg.heartbeat.tick_seconds,
            fire=fire_path.run,
            # The `kind="proactive"` screen: the room's flag and
            # the note's state, read through the SAME allowlist and the SAME
            # note store every other reader holds. Wired whenever the
            # heartbeat is, not only when the adaptor is: a proactive row
            # written yesterday must be screened today even if the adaptor
            # was switched off in between — and with no screen the loop
            # fails such a row rather than firing it unscreened.
            proactive=proactive_screen,
        )
        background.spawn(heartbeat.run())

    # The compute-job poller: a background
    # loop, gated on BOTH the feature flag and a live `fire_path` (a compute
    # job is delivered as a fire turn into the same conversation, exactly as
    # a reminder is). `compute_backends` is only non-empty
    # when `cfg.compute.enabled` is true (see its construction above), so
    # this condition is really `cfg.compute.enabled and fire_path is not
    # None` restated — spelled out with the third check anyway so the
    # relationship holds even if `compute_backends`' own gate ever moves.
    if cfg.compute.enabled and fire_path is not None and compute_backends:
        compute_poller = ComputeJobPoller(
            store=compute_jobs, backends=compute_backends,
            documents=document_service if cfg.documents.enabled else None,
            # A finished clip's bytes are kept where a generated image's are (DEC-138): the
            # same blob store and media index, so `media_search` / `media_send` reach it.
            blobs=blobs, media=media_objects,
            # `speak`, NOT `run`: `run` is the heartbeat's reminder-shaped
            # adapter and would need rows this occasion does not have.
            speak=fire_path.speak,
            # The SAME `slots` every other path into a conversation holds.
            # Two would be two locks and no serialisation at all.
            slots=slots,
            metrics=metrics,
            tick_seconds=cfg.compute.poll_tick_seconds,
            clock=clock,
        )
        # Spawned, never awaited — the same reason `heartbeat.run()` above
        # is: a boot must not wait on a background
        # loop, and `BackgroundTasks` holds the strong reference.
        background.spawn(compute_poller.run())

    # The public Instagram summons consumer, gated on BOTH
    # `[social.instagram] enabled` and its own `summons.enabled` (the config
    # refuses the second without the first) — the same `summons_wired` that
    # enabled the inbox above, so a
    # queued card always has a consumer. It takes cards off the inbox the
    # Instagram adapter fills from the bridge's SSE stream, one at a time;
    # there is no poll (the notification feed never carries these mentions),
    # so `summons.poll_seconds` is read by nothing. It needs no
    # `fire_path`: a summons speaks into no room. Spawned, never awaited;
    # `cancel_all` stops it at shutdown with every other background loop.
    if summons_wired and summons_runner is not None and social_subjects is not None:
        from nora.app.summons import SummonsOccasion
        from nora.app.summons_consumer import SummonsConsumer
        from nora.infra.social.summons_client import InstagramSummonsClient
        from nora.infra.sql.summons import SqlSummonsLedger

        summons_cfg = cfg.social.instagram.summons
        summons_client = InstagramSummonsClient(
            base_url=cfg.social.instagram.base_url,
            timeout_seconds=cfg.social.instagram.timeout_seconds,
        )
        summons_ledger = SqlSummonsLedger(engine)
        summons_consumer = SummonsConsumer(
            inbox=summons_inbox,
            occasion=SummonsOccasion(
                client=summons_client, ledger=summons_ledger,
                summoners=social_subjects, persons=persons,
                doctrine_store=doctrine_store, registry=tool_registry,
                runner=summons_runner, metrics=metrics,
                canary=cfg.doctrine.canary, handle=summons_cfg.handle,
                mode=summons_cfg.mode,
                max_age=timedelta(minutes=summons_cfg.max_age_minutes),
                limits=limits, timeout_seconds=tier.timeout_seconds, clock=clock,
            ),
            ledger=summons_ledger, metrics=metrics, clock=clock,
        )
        background.spawn(summons_consumer.run())
        log.info("instagram summons consumer wired (mode=%s)", summons_cfg.mode)
        if "instagram" not in registry:
            # The cards arrive on the MESSAGING adapter's SSE stream, so a
            # summons needs `[platforms.instagram]` too. Said loudly rather
            # than refused at load: the consumer idles harmlessly without it.
            log.error(
                "[social.instagram.summons] is enabled but [platforms.instagram] "
                "is not: summons cards arrive on the Instagram adapter's event "
                "stream, so no summons will ever be received",
            )

    # The social stream's poller (design/cognition-loop/22-social-stream.md):
    # built here, started by `nora/__main__.py`, because the eval subject
    # composes this graph too and must not read members' accounts through the
    # deployment's login. `[social.poll] enabled` needs `[social.instagram]`
    # (refused at load), so `social_subjects` is set whenever it is on.
    social_poller: SocialPoller | None = None
    social_perception: SocialPerception | None = None
    if cfg.social.poll.enabled and social_subjects is not None:
        from nora.infra.social.instagram_source import InstagramSocialSource
        from nora.infra.social.subjects import instagram_roster

        # ONE store for the poller and every reader, holding who opted out:
        # the read-side exclusion lives in the store's reads (§2).
        social_store = readable_social_store(engine, persons, metrics=metrics)
        social_poller = SocialPoller(
            source=InstagramSocialSource(
                base_url=cfg.social.instagram.base_url,
                timeout_seconds=cfg.social.instagram.timeout_seconds,
            ),
            store=social_store,
            health=SqlSourceHealthLog(engine),
            roster=functools.partial(instagram_roster, social_subjects),
            settings=cfg.social.poll, metrics=metrics,
            # The same custody every room's media goes through; `None` while
            # media custody is off, and then items are recorded without bytes.
            media=media_sources.for_platform("instagram") if media_sources else None,
            blobs=blobs, media_objects=media_objects,
        )
        # Perception needs `[media] comprehension` (`comprehension` is None
        # without it, and then items stay unperceived and their reads are
        # announced as recorded); the stimulus needs `[cognition] enabled`.
        social_perception = SocialPerception(
            store=social_store, media_objects=media_objects,
            comprehension=comprehension, stimuli=stimulus_store,
            units=social_person_units(allowlist), rooms=social_person_rooms(allowlist),
            daily_cap=cfg.social.poll.perception_daily_cap,
            tz=ZoneInfo(cfg.cognition.timezone),
            chunk_seconds=cfg.media.describe.video.chunk_seconds,
            chunk_max=cfg.media.describe.video.chunk_max,
            tick_seconds=cfg.social.poll.tick_seconds, metrics=metrics,
        )
        log.info("social poller wired (instagram)")
    # The bound is on bytes the poller already kept, so it holds while the
    # poller is off; it reads every person's frames, opted out or not.
    story_expiry = StoryMediaExpiry(
        store=SqlSocialStreamStore(engine), blobs=blobs,
        keep_days=cfg.social.story_media_keep_days,
        max_days=cfg.social.story_media_keep_max_days,
        tick_seconds=cfg.social.poll.tick_seconds, metrics=metrics,
        embedded=_story_frames_embedded(engine, fingerprint(cfg.memory_index.embedder_model)),
    ) if blobs is not None and cfg.social.story_expiry_enabled else None

    # The participation occasion: trigger, then
    # dispatcher. Gated on BOTH the flag and a live `fire_path` — the same
    # shape as the poller above. The trigger holds `fire_path.speak` as a
    # BOUND METHOD (`tests/test_composition.py` asserts the same of the
    # heartbeat's `_fire`), and the SAME `slots` every other path into a
    # conversation holds — two would be two locks and no serialisation at all.
    #
    # The LOOP (`nora.app.runtime.participation_loop`) is spawned in
    # `nora/__main__.py` from `deps.participation_trigger`, like every other
    # background clock — this section only builds the trigger itself.
    participation_trigger: ParticipationTrigger | None = None
    # Bound in the branch below, beside the trigger that holds it.
    gate: RelevanceGate | None = None
    if cfg.relevance_gate.enabled and fire_path is not None:
        # The gate is built HERE and not beside the other model clients,
        # because it is the only one whose absence would change behaviour
        # silently: a trigger with `gate=None` admits every closed window.
        # Constructed in the same branch that constructs the trigger, so the
        # two cannot come apart.
        gate = RelevanceGate(
            client=_RelevanceGateModelClient(
                # The process-wide `AsyncOpenAI` (DEC-13), not a second
                # transport of its own: the gate's model is its own combo, but
                # the connection pool, the retry policy and the base url are
                # the deployment's one client.
                client=client,
                model=cfg.relevance_gate.model,
                max_tokens=cfg.relevance_gate.max_tokens,
                temperature=cfg.relevance_gate.temperature,
                timeout_seconds=cfg.relevance_gate.timeout_seconds,
                reasoning_effort=_reasoning_effort(
                    cfg.relevance_gate.reasoning_effort,
                    source="[relevance_gate] reasoning_effort",
                ),
            ),
            # The SAME store the turn reads, at a DIFFERENT audience. The gate
            # gets `participation.md` and nothing else; Nora never sees that
            # file and the gate never sees hers. Hot-reloads on mtime, which
            # is what makes the shadow soak a tuning loop instead of a
            # rebuild-per-edit loop.
            prompts=doctrine_store,
            records=audit,
            metrics=metrics,
            # DEC-80's projection, the SAME `Persons` the fire path and the
            # tag path use. Without it `person_name` is None on every row and
            # the gate labels speakers by their self-set platform profile
            # name — the two-name-space bug, on the one call that reads every
            # opted-in room.
            persons=persons,
            # The SAME standing store the fire path reads (`None` while
            # `[standing] enabled = false`), so the gate and the turn behind
            # it see one room and not two.
            standing=standing_store if cfg.standing.enabled else None,
            transcript_limit=cfg.relevance_gate.transcript_rows,
            # The SAME number the client above is built with, handed to the
            # gate as well. Not belt-and-braces: the client's
            # `wait_for` is a property of THIS wiring, and the gate's own
            # `decide` promise — never raise, never hang — has to hold against
            # whatever satisfies `ModelClient`. Whichever fires first produces
            # the same `timeout` verdict, so a single configured ceiling still
            # describes the deployment.
            call_timeout_seconds=cfg.relevance_gate.timeout_seconds,
            # The SAME join the turn behind it runs. Without it the gate reads
            # an image-only message as an EMPTY BODY, `render_context` drops
            # the row, and the window a photo prompted is the one the gate is
            # least able to see — a systematic under-admission exactly where
            # the room is most likely to be asking something.
            media_join=media_join,
            # NO `capabilities`: giving the gate her derived read-only tool
            # surface made it STRICTER and worse on the eval suite (more false
            # NO_GO, no capability improved) and adds prompt tokens on every
            # quiet window in every opted-in room.
            #
            # `ToolRegistry.capability_digest()` and `--capabilities` on the
            # suite are kept so the comparison can be re-run.
        )
        participation_trigger = ParticipationTrigger(
            speak=fire_path.speak,
            slots=slots,
            # Still `[participation]`'s: the quiescence window
            # belongs to the TRIGGER, which is shared, and the gate is what
            # runs when a window closes — not what defines one.
            settle_seconds=cfg.participation.settle_seconds,
            max_window_messages=cfg.participation.max_window_messages,
            cooldown_seconds=cfg.participation.cooldown_minutes * 60,
            gate=gate,
            # Shadow is DECIDED here, once: passing the poster IS shadow mode.
            # A boolean flag plus a poster would admit a fourth state (shadow
            # on, nowhere to post) that silently behaves like live.
            gate_shadow=(
                fire_path.post_gate_verdict if cfg.relevance_gate.shadow else None
            ),
            # The per-room watermarks, persisted so the SEND cooldown survives
            # a force-recreate: otherwise she could speak again at once however
            # recently she had spoken, and the cooldown's whole job is to cap
            # what a room HEARS.
            store=SqlParticipationStateStore(engine),
        )
    elif cfg.participation.enabled:
        # The CONVERSE of `_the_gate_needs_something_behind_it` (nora/config.py),
        # and it cannot live there: that validator refuses a gate with no turn
        # behind it, which is a config error. This is a turn with no gate in
        # FRONT of it, which is a legal config that simply does nothing — the
        # room policy and the trigger are both keyed on `[relevance_gate]
        # enabled`, so participation alone notes no message, closes no window
        # and runs no turn. Loud rather than inert, so an operator reading
        # `[participation]` has a way to learn this.
        log.error(
            "participation.enabled = true but relevance_gate.enabled = false — "
            "NOTHING will happen: since design/dreamer-agent-loop/36 the "
            "quiescence trigger and the room policy are the gate's, so with the "
            "gate off no window is ever noted, judged or turned into a turn. "
            "Set [relevance_gate] enabled = true (it ships in shadow mode)."
        )
    elif cfg.relevance_gate.enabled:
        # Reachable only if the fire-path gate above stopped including
        # `cfg.participation.enabled` — loud rather than inert, because a
        # trigger-less participation flag would otherwise look healthy.
        log.error(
            "relevance_gate.enabled = true but no fire path was built — no "
            "window can ever be judged and no turn can ever run. Check the "
            "FirePath gate."
        )
    dispatcher = Dispatcher(
        ingest=ingest, engagement=engagement, background=background,
        participation=participation_trigger,
    )

    # ---- the Dreamer runtime --------------------
    #
    # Constructed only when `[dreamer] enabled` is true, the shape every
    # gated subsystem in this file uses: a
    # subsystem that is off builds NOTHING, so a wiring mistake in it cannot
    # cost a deployment that never turned it on.
    #
    # `dream_adaptors` is empty unless `[culture]` turns its pass on:
    # `dream_loop` with an empty tuple ticks the cursor and runs nothing.
    dream_adaptors: tuple[DreamAdaptor, ...] = ()
    dreamer_runtime: DreamerRuntime | None = None
    dream_schedules: Mapping[str, AdaptorSchedule] = {}
    # ONE schedule-state store, shared by the loop's slots and
    # any other schedule clock: two instances would be two
    # engines over one table for no reason.
    dream_schedule_clock = SqlDreamScheduleClock(engine)
    # The Cognition session: built inside the Dreamer block below ONLY under
    # `[cognition] enabled`; off builds nothing here, no model client and no
    # agent-runner import.
    #: Skills injected per agenda kind (`AGENDA_SKILLS`: the Register skill).
    agenda_skills = cognition_agenda_skills(dreamer_instructions)
    cognition_runner: CognitionSessionRunner | None = None
    #: The units a session can run for (a home room each); only they are woken.
    cognition_session_units: Collection[str] | None = None
    cognition_upcoming: Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]] | None = None
    if cfg.dreamer.enabled:
        # THE CULTURE TIER (DEC-182..185). An account of
        # how each room works and why, folded from that room's own messages
        # when it has said enough (DEC-288) and appended to a chain.
        #
        # READ BY THE PROMPT AND BY THE CHARACTER SESSIONS: `<culture>` renders
        # a room's account on tag and fire turns (DEC-207), and the person and
        # unit sessions below read it as evidence (cited accounts only,
        # DEC-290). The chain is the contribution: a tier judged on its first
        # snapshot is judged on the one thing it does not contribute.
        #
        # Gated on `[culture] enabled` alone — no note-tier dependency: the
        # corpus is `messages`, which every deployment has.
        if cfg.culture.enabled:
            dream_schedules = {
                **dream_schedules,
                "culture": AdaptorSchedule(
                    # DEC-288: fire on
                    # `wake_batch` new human messages, or on any new one once
                    # the last fold is `stale_seconds` old. A SILENT ROOM STILL
                    # NEVER FIRES, which is correct rather than a defect:
                    # nothing in this tier retires, so a quiet room's account
                    # is accurate and there is nothing to refresh it against.
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.culture.wake_batch,
                    # THE BATCH IS THE CORPUS — see `CultureConfig.batch_limit`.
                    # This is how much of the room the model gets to see, not a
                    # throttle.
                    batch_limit=cfg.culture.batch_limit,
                    # START FROM WHAT THE ROOM HAS BEEN SAYING LATELY, not from
                    # its origin story. `seed_watermarks` inherits from the
                    # `language` cursor, and that tier is DELETED — so a room it
                    # never covered would seed at 0 and spend days walking a
                    # backlog, installing an account derived from the room's
                    # past as the current head each time.
                    seed_at_head=True,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                CultureAdaptor(
                    culture=SqlCultureStore(engine),
                    # THE NOTE INDEX: an account of how a room works that cannot see
                    # what the room has OPEN is inferring a thing the note
                    # index already states. `None` for the tier being off is
                    # a real configuration and renders no block, not an empty
                    # one. NOT `standing`: it is folded from raw tool results
                    # (a presence read among them) and this account is input
                    # to ones shown in every room of the unit (R-31).
                    notes=note_store if cfg.notes.enabled else None,
                    # The THREE policy resolutions, every one of them the same
                    # `.get` over the allowlist keyed on `conv.primary_id` —
                    # which is this adaptor's own key, because a Dreamer
                    # adaptor is handed `scopes()` (GLOSSARY.md). Built here, in
                    # one place, so they cannot disagree about which room a key
                    # names.
                    unit_for={
                        conv.primary_id: conv.unit
                        for conv in allowlist.conversations
                        if conv.primary_id and conv.unit
                    }.get,
                    # What the room is CALLED and what the operator says it IS
                    # (`Conversation.label` / `.description`), for the `<room>`
                    # block. A room with neither is simply absent from the map
                    # and the block falls back without ever rendering an id.
                    label_for={
                        conv.primary_id: conv.label
                        for conv in allowlist.conversations
                        if conv.primary_id and conv.label
                    }.get,
                    description_for={
                        conv.primary_id: conv.description
                        for conv in allowlist.conversations
                        if conv.primary_id and conv.description
                    }.get,
                    metrics=metrics,
                    # THE ROOM'S PLATFORM and A NAME RESOLVER,
                    # off the SAME allowlist read `label_for`/`description_for`
                    # already are, plus `persons` — the one instance every
                    # other write path in this file already holds. This is
                    # what lets `inputs()` fill a mapped sender's canonical
                    # name into `<room_messages>` before it renders.
                    platform_for={
                        conv.primary_id: conv.platform
                        for conv in allowlist.conversations
                        if conv.primary_id
                    }.get,
                    resolve_names=(
                        lambda rows, platform:
                            persons.name_transcript(rows, platform=platform)
                    ),
                ),
            )
            # CHARACTER · PERSON: one session
            # per (unit, person listed in that unit's rooms), woken by that
            # person's own messages across the unit's rooms. Same store,
            # same chain, the stale rule `[culture] stale_seconds` shares.
            person_keys = character_person_keys(allowlist, persons)
            culture_store = SqlCultureStore(engine)
            dream_schedules = {
                **dream_schedules,
                CHARACTER_PERSON_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.culture.person_wake_batch,
                    # THE WAKE BATCH, as Nora's session reads its own: a
                    # person's session reads their newest `person_wake_batch`
                    # messages, not a room's `batch_limit`, which sizes a room's
                    # whole corpus.
                    batch_limit=cfg.culture.person_wake_batch,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                CharacterPersonAdaptor(
                    culture=culture_store,
                    evidence=PersonEvidence(
                        store=SqlCharacterEvidence(engine), keys=person_keys,
                        # Their social items are evidence too (design/
                        # cognition-loop/22 §6), read whether or not the poller
                        # runs, opted-out persons left out by the store.
                        social=readable_social_store(engine, persons),
                        former_ids=persons.former_ids_for,
                    ),
                    keys=person_keys, metrics=metrics,
                    screen=scan_identifiers,
                    render_chars=cfg.media.comprehension_render_chars,
                    # A caption keeps only the author's own @handle.
                    handle_owners=persons.instagram_username_owners,
                ),
            )

            # CHARACTER · UNIT: one session per unit, woken by new
            # revisions of its rooms' cultures.
            unit_keys = character_unit_keys(allowlist)
            dream_schedules = {
                **dream_schedules,
                CHARACTER_UNIT_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.culture.unit_wake_batch,
                    batch_limit=cfg.culture.batch_limit,
                ),
            }
            # CHARACTER · NORA: her own account per unit, from closed
            # response episodes; read back as the `self` block.
            nora_keys = {
                nora_character_key(uk.unit): NoraKey(unit=uk.unit, rooms=uk.rooms)
                for uk in unit_keys.values()
            }
            dream_schedules = {
                **dream_schedules,
                CHARACTER_NORA_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.culture.stale_seconds,
                    wake_batch=cfg.culture.nora_wake_batch,
                    batch_limit=cfg.culture.nora_wake_batch,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                CharacterNoraAdaptor(
                    culture=culture_store,
                    evidence=NoraEvidence(
                        store=SqlCharacterEvidence(engine), keys=nora_keys,
                    ),
                    keys=nora_keys, metrics=metrics,
                    name_of=lambda pid: (
                        ref.name if pid and (ref := persons.by_person.get(pid)) is not None
                        else "someone"
                    ),
                ),
            )
            dream_adaptors = (
                *dream_adaptors,
                CharacterUnitAdaptor(
                    culture=culture_store,
                    evidence=UnitEvidence(
                        store=SqlCharacterEvidence(engine), keys=unit_keys,
                    ),
                    keys=unit_keys, metrics=metrics,
                ),
            )

            # PERSONA · COLLECTIVES' keys (45 §1's key forms): a room's
            # account under its `primary_id`, the unit's and Nora's under the
            # culture keys — the same key the prose head they backfill from
            # is written under.
            persona_room_keys = {
                conv.primary_id: CollectiveKey(
                    unit=conv.unit,
                    rooms=(CharacterRoom(key=conv.primary_id, platform=conv.platform,
                                         label=conv.label),),
                    label=conv.label or "", description=conv.description or "",
                )
                for conv in allowlist.conversations if conv.primary_id and conv.unit
            }
            persona_unit_keys = {
                key: CollectiveKey(unit=uk.unit, rooms=uk.rooms, label=uk.unit)
                for key, uk in unit_keys.items()
            }
            persona_nora_keys = {
                key: CollectiveKey(unit=nk.unit, rooms=nk.rooms, label="Nora")
                for key, nk in nora_keys.items()
            }
            persona_collective_keys = {
                **persona_room_keys, **persona_unit_keys, **persona_nora_keys,
            }

            # PERSONA · PERSON, in shadow (design/unified-memory/45 §4): one
            # session per (unit, listed person), on the person's Character
            # wake plus a turn's `told` line; it writes `persona_line`, which
            # nothing renders until the switch. Inside the culture block: it
            # reads the Character head and the rooms' cultures.
            if cfg.persona.enabled:
                persona_store = SqlPersonaStore(
                    engine, screen=text_offence, index=persona_index)
                persona_reads = SqlCharacterEvidence(engine)
                # The person accounts' social input (45 §4): the readable
                # store's (opted-out, gone and hidden left out there), the
                # same store the social cursor and the counts come from.
                persona_social = readable_social_store(engine, persons)
                dream_schedules = {
                    **dream_schedules,
                    PERSONA_PERSON_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.culture.stale_seconds,
                        wake_batch=cfg.culture.person_wake_batch,
                        batch_limit=cfg.culture.person_wake_batch,
                    ),
                    # The verdict judge (45 §3): one unit's docket of
                    # window-closed open hypotheses, fired the tick one is due
                    # (wake_batch 1), never by its writer.
                    PERSONA_VERDICT_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.culture.stale_seconds,
                        wake_batch=1,
                        batch_limit=cfg.culture.person_wake_batch,
                    ),
                }
                dream_adaptors = (
                    *dream_adaptors,
                    PersonaPersonAdaptor(
                        persona=persona_store, culture=culture_store,
                        evidence=PersonaEvidence(
                            store=persona_reads, persona=persona_store, keys=person_keys,
                            wake_batch=cfg.culture.person_wake_batch,
                            social=persona_social,
                            former_ids=persons.former_ids_for,
                        ),
                        reads=persona_reads, keys=person_keys,
                        name_of=lambda pid: (
                            ref.name if pid and (ref := persons.by_person.get(pid)) is not None
                            else "someone"
                        ),
                        clock_ms=_now_ms, metrics=metrics,
                        # The social items' render (45 §4): screened like the
                        # Character session's, captions redacted to the author's
                        # own handle, each reading clipped as the media reads.
                        social=persona_social,
                        screen=scan_identifiers,
                        render_chars=cfg.media.comprehension_render_chars,
                        handle_owners=persons.instagram_username_owners,
                    ),
                    PersonaVerdictAdaptor(
                        store=persona_store, reads=persona_reads,
                        rollup=SqlRollupRead(engine),
                        # The docket covers every account of the unit, a
                        # collective's included: each mapped to who the judge
                        # says it is about and the rooms its window covers.
                        keys={
                            **{key: DocketAccount(
                                about=(ref.name if (ref := persons.by_person.get(
                                    pk.person.person_id)) is not None else "someone"),
                                rooms=tuple((r.platform, r.key) for r in pk.rooms
                                            if r.key in pk.listed),
                            ) for key, pk in person_keys.items()},
                            **{key: DocketAccount(
                                about=ck.label or key,
                                rooms=tuple((r.platform, r.key) for r in ck.rooms),
                            ) for key, ck in persona_collective_keys.items()},
                        },
                        name_of=lambda pid: (
                            ref.name if pid and (ref := persons.by_person.get(pid)) is not None
                            else "someone"
                        ),
                        clock_ms=_now_ms, metrics=metrics,
                    ),
                )

            # PERSONA · COLLECTIVES, in shadow (45 §4): a room's, the unit's
            # and Nora's own account, each woken by its own evidence — the
            # room's messages, the rooms' culture revisions, the unit's
            # closed response episodes.
            if cfg.persona.enabled:
                dream_schedules = {
                    **dream_schedules,
                    PERSONA_ROOM_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.culture.stale_seconds,
                        wake_batch=cfg.culture.wake_batch,
                        batch_limit=cfg.culture.batch_limit,
                    ),
                    PERSONA_UNIT_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.culture.stale_seconds,
                        wake_batch=cfg.culture.unit_wake_batch,
                        batch_limit=cfg.culture.batch_limit,
                    ),
                    PERSONA_NORA_ADAPTOR_NAME: AdaptorSchedule(
                        min_interval_seconds=cfg.culture.stale_seconds,
                        wake_batch=cfg.culture.nora_wake_batch,
                        batch_limit=cfg.culture.nora_wake_batch,
                    ),
                }
                collective_kinds: tuple[
                    tuple[Literal["room", "unit", "nora"], Mapping[str, CollectiveKey]], ...,
                ] = (
                    ("room", persona_room_keys), ("unit", persona_unit_keys),
                    ("nora", persona_nora_keys),
                )
                dream_adaptors = (
                    *dream_adaptors,
                    *(PersonaCollectiveAdaptor(
                        kind=kind, persona=persona_store, culture=culture_store,
                        evidence=CollectiveEvidence(
                            store=persona_reads, keys=keys, kind=kind),
                        reads=persona_reads, keys=keys,
                        name_of=lambda pid: (
                            ref.name if pid and (ref := persons.by_person.get(pid)) is not None
                            else "someone"
                        ),
                        clock_ms=_now_ms, metrics=metrics,
                    ) for kind, keys in collective_kinds),
                )

        # LESSONS: one session per unit, woken by
        # closed response episodes, writing through the book's checked verbs.
        # Gated on `[lessons] enabled` alone (off by default).
        if cfg.lessons.enabled:
            lessons_keys = {
                uk.unit: LessonsKey(unit=uk.unit, rooms=uk.rooms)
                for uk in character_unit_keys(allowlist).values()
            }
            # THE DOCTRINE CHECK: every lesson is read against the law by a
            # separate call before it is written; a conflict is a tension row.
            # Its instruction is read once HERE so a missing file stops startup
            # instead of refusing every lesson as `check_unavailable`.
            if not dreamer_instructions.objective_body(CHECK_OBJECTIVE).strip():
                raise StartupError(f"the {CHECK_OBJECTIVE} objective is empty")
            lesson_check = DoctrineCheck(
                # The relevance gate's raw JSON completion, not the Strands
                # `_DebriefModelClient`: the Strands path returns an EMPTY
                # answer (`end_turn`) on a live lesson, while the same request
                # raw returns `{"verdict": "consistent"}`. Empty is an `error`,
                # so every such lesson would be refused.
                client=_RelevanceGateModelClient(
                    client=client,
                    model=cfg.lessons.check_model or cfg.llm.model,
                    max_tokens=cfg.lessons.check_max_tokens, temperature=0.0,
                    timeout_seconds=cfg.lessons.check_timeout_seconds + 1.0,
                    reasoning_effort=_reasoning_effort(
                        cfg.lessons.check_reasoning_effort or "low",
                        source="[lessons] check_reasoning_effort",
                    ),
                ),
                law=doctrine_store.law_rules,
                instruction=lambda: dreamer_instructions.objective_body(CHECK_OBJECTIVE),
                timeout_seconds=cfg.lessons.check_timeout_seconds,
                metrics=metrics,
            )
            lesson_book = LessonBook(
                store=SqlLessonStore(engine),
                law=lambda: [r.text for r in doctrine_store.law_rules()],
                screen=dream_offence,
                clock_ms=_now_ms,
                check=lesson_check, tensions=SqlTensionStore(engine), metrics=metrics,
            )
            dream_schedules = {
                **dream_schedules,
                LESSONS_ADAPTOR_NAME: AdaptorSchedule(
                    min_interval_seconds=cfg.lessons.stale_seconds,
                    wake_batch=cfg.lessons.wake_batch,
                    batch_limit=cfg.lessons.batch_limit,
                ),
            }
            dream_adaptors = (
                *dream_adaptors,
                LessonsAdaptor(
                    book=lesson_book,
                    evidence=LessonEvidence(
                        store=SqlCharacterEvidence(engine), book=lesson_book,
                        keys=lessons_keys, wake_batch=cfg.lessons.wake_batch,
                        horizon_seconds=cfg.lessons.horizon_seconds,
                        clock_ms=_now_ms,
                        correction_quiet_seconds=(
                            cfg.lessons.correction_quiet_seconds or None),
                        stale_seconds=cfg.lessons.stale_seconds, metrics=metrics,
                        self_ids=frozenset(registry.self_aliases()),
                    ),
                    keys=lessons_keys,
                    name_of=lambda pid: (
                        ref.name if pid and (ref := persons.by_person.get(pid)) is not None
                        else "someone"
                    ),
                    horizon_seconds=cfg.lessons.horizon_seconds,
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
        # VALIDATED here for its refusal, then passed as the plain string
        # `ModelSpec` declares. `_reasoning_effort` raises `StartupError` on
        # anything the provider will not accept and returns a narrowed literal
        # that `ModelSpec.reasoning_effort: str` does not want; the refusal is
        # the part that matters and it happens either way.
        dream_effort = cfg.dreamer.reasoning_effort or tier.reasoning_effort
        _reasoning_effort(
            dream_effort,
            source="[dreamer] reasoning_effort (or [llm]'s, inherited)",
        )
        dream_models = factory.ModelSource(
            client=client,
            spec=factory.ModelSpec(
                # `cfg.dreamer_max_tokens()` and NOT `tier.max_tokens`. The
                # tier's number is sized for a TAG TURN; a batch pass whose
                # objective asks for one verdict per message has an output that
                # scales with the batch, and a tag-sized ceiling fails every run
                # with `PartialReplyError` — held watermark, re-read batch, one
                # retry per hour forever. The accessor folds the `[llm]`
                # fallback in, so an unset `[dreamer] max_tokens` falls back
                # to it.
                model=dream_model, max_tokens=cfg.dreamer_max_tokens(),
                temperature=tier.temperature,
                # `[dreamer] reasoning_effort` where set, else the tier's. At
                # the tier's "low" this model reasons its way to
                # `{"candidates": []}` on a transcript that plainly contains
                # claims, and to `MaxTokensReachedException` on a real one —
                # reasoning is billed as completion and it spends the ceiling.
                # The key is the Dreamer's own so her replies keep theirs.
                # WRAPPED, and the wrap is the point rather than a tidy-up.
                # `[dreamer] reasoning_effort` is typed, but `""` is its
                # default and its documented inherit value, and what it
                # inherits is `[llm] reasoning_effort`, a bare `str`.
                # Unwrapped, `[llm] = "hgh"` with `[dreamer] = ""` LOADS and
                # resolves to `"hgh"`, which reaches the provider as a 400
                # mid-tick and surfaces as one more `unavailable` in a
                # background pass nobody watches.
                #
                # `_reasoning_effort` exists for this case; the debrief's
                # `standing-or-llm` expression is the same shape.
                reasoning_effort=dream_effort,
                tool_choice=tool_choice.AFTER_SPEAKING,
            ),
        )
        # ONE tool source, shared by the runtime and the startup check below —
        # two would be two ceilings, and the check would be measuring something
        # other than what the runtime enforces.
        dream_tools = RegistryToolSource(
            registry=tool_registry, store=audit, persons=persons,
            honcho=memory, metrics=metrics, ai_peer=cfg.memory.ai_peer,
            # The home rooms, from the SAME allowlist read the
            # adaptor's room map came from, with each room's `location`
            # flag threaded as DEC-74 threads it on a tag. Passed whether or
            # not the proactive adaptor is wired: the map is inert for an
            # adaptor with no home-room grant, and a grant with no map fails
            # closed inside `build`.
            home_rooms={
                ws: HomeRoom(
                    unit=ws, platform=conv.platform,
                    conversation_id=conv.primary_id, conversation_type="group",
                    location_trusted=conv.location,
                )
                for ws, conv in proactive_homes.items()
            },
            # EVERY policy conversation, keyed by every id form it answers to
            # — the SCOPE-keyed half of a home-room grant
            # (`AdaptorGrant.home_room`). A scope-keyed adaptor's key already
            # IS a conversation, so there is no room to choose and no
            # `home_room = true` to read: the grant resolves the adaptor's own
            # room, and this map is what carries the platform (the roster
            # source), the id to bind and the `location` flag DEC-74 threads.
            # Read off the SAME allowlist as `home_rooms` above so the two cannot
            # disagree about which room an id names.
            # Inert for an adaptor with no home-room grant, exactly as
            # `home_rooms` is.
            scope_rooms={
                cid: HomeRoom(
                    unit=conv.unit, platform=conv.platform,
                    conversation_id=conv.primary_id, conversation_type="group",
                    location_trusted=conv.location,
                )
                for conv in allowlist.conversations
                for cid in conv.ids
            },
            rosters=rosters,
            # EVERY platform's aliases, for `_presence_preflight`'s reason: a
            # home room's roster must skip Nora's own account rather than
            # count it as one unnamed stranger — which alone makes `where_is`
            # refuse in a location-trusted room.
            self_ids=registry.self_aliases(),
        )
        # Each adaptor's room grant, by NAME, handed to the runtime AND to the
        # startup check below so the two measure the same grant. No adaptor
        # holds an act verb.
        dream_grants: dict[str, AdaptorGrant] = {
            # The culture adaptor's own room, and no act verb
            # (`culture_grant`). SCOPE-keyed, so this resolves through
            # `scope_rooms` above rather than through `home_rooms`: its key
            # already names the conversation, so the grant is an identity and
            # there is nothing for the operator to choose. Passed
            # unconditionally, the same way `home_rooms` is: a grant for an
            # adaptor that is not wired is never read, and `[culture] enabled
            # = false` is what keeps the adaptor out of `dream_adaptors`.
            CULTURE_ADAPTOR_NAME: culture_grant(),
            # A person session's read tools stand in the first of its unit's
            # rooms that lists the person (`CharacterPersonAdaptor.tool_room`),
            # resolved through `scope_rooms` exactly as the room fold's are.
            CHARACTER_PERSON_ADAPTOR_NAME: culture_grant(),
            # The Persona session stands where the person's Character session
            # does (`PersonaPersonAdaptor.tool_room`), with the same reads.
            PERSONA_PERSON_ADAPTOR_NAME: culture_grant(),
            # A room's Persona session holds Culture's read tools, scoped to
            # its own room (`PersonaCollectiveAdaptor.tool_room`).
            PERSONA_ROOM_ADAPTOR_NAME: culture_grant(),
            # The verdict judge holds no read tool (its windows are in the
            # prompt) and writes only through `record_verdict`; the default
            # grant (nothing) is its ceiling, kept here by omission.
        }
        # `DreamBudgetConfig` -> `AdaptorBudget`, through the ONE named
        # converter (`_adaptor_budget` above): the same five numbers across the
        # `nora.agent` may-not-import-`nora.config` boundary, carried in one
        # place rather than written out at each construction site.
        dream_budgets = {
            name: _adaptor_budget(b) for name, b in cfg.dreamer.budgets.items()
        }
        # A Character session with no block of its own runs on the room
        # fold's numbers when the deployment set them: the same loop shape (a
        # handed batch, a few reads, one account written), and a live config
        # needs no new block for it. The Lessons session too: a handed batch
        # and a few checked writes.
        if "culture" in dream_budgets:
            for name in (*CHARACTER_ADAPTOR_NAMES, LESSONS_ADAPTOR_NAME,
                         PERSONA_PERSON_ADAPTOR_NAME, PERSONA_VERDICT_ADAPTOR_NAME,
                         *PERSONA_COLLECTIVE_ADAPTOR_NAMES):
                dream_budgets.setdefault(name, dream_budgets["culture"])
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
            # The Dreamer's reasoning-strip store: a tool-using run at
            # `[dreamer] reasoning_effort = low` is the multi-turn shape the
            # transport strips. Low-traffic and judgement-forming, so a
            # discarded reasoning there is both closer to being the verdict
            # and harder to notice than on a reply somebody reads.
            reasoning_strip_store=reasoning_strip_store,
        )
        # R-5's refuse-to-start shape, and the one check that must run at
        # STARTUP rather than at the first tick: an adaptor holding an act verb
        # is not a condition to discover at 3am from a metric. Since DEC-162
        # it refuses everything but the one grant it is handed.
        verify_tool_subsets(
            dream_adaptors, tools=dream_tools, grants=dream_grants,
            # The per-adaptor per-tool ceilings, read off the SAME budgets the
            # runtime got above — so «this ceiling names a tool the adaptor
            # does not hold» is a boot failure rather than a raise the
            # operator made that quietly does nothing.
            tool_calls={
                name: b.tool_calls for name, b in cfg.dreamer.budgets.items()
            },
        )

        if cfg.cognition.enabled and stimulus_store is not None:
            # Sessions ride the Dreamer's model, tool source and objective
            # corpus (the config validator refuses sessions without
            # `[dreamer] enabled`), and write through the tag path's stores.
            surface = read_surface(
                read_only=dream_tools.read_only(),
                enabled_flags=(
                    *(("record_query.enabled",) if cfg.record_query.enabled else ()),
                    *(("culture.enabled",) if cfg.culture.enabled else ()),
                ),
                # The memory surface the registry selected (DEC-316), read
                # from the registry that builds the tools, not from config.
                registry_withheld=tool_registry.withheld_tools(),
            )
            log.info(
                "cognition sessions: read tools wired=%s missing=%s withheld=%s",
                sorted(surface.wired), sorted(surface.missing), sorted(surface.withheld),
            )
            homes = allowlist.home_rooms()
            cognition_wakeable, cognition_skipped = units_with_home_rooms(
                list(unit_scopes()), homes,
            )
            cognition_session_units = frozenset(cognition_wakeable)
            metrics.gauge(
                "nora_cognition_session_units", float(len(cognition_wakeable)),
                state="runnable",
            )
            metrics.gauge(
                "nora_cognition_session_units", float(len(cognition_skipped)),
                state="no_home_room",
            )
            if cognition_skipped:
                log.error(
                    "cognition sessions: unit(s) %s name no home room in policy.toml, so no "
                    "session can start for them and they are not woken "
                    "(nora_cognition_session_units{state=no_home_room}). Set `home_room = "
                    "true` on one of their rooms to cover them",
                    cognition_skipped,
                )
            if note_store is None:
                raise StartupError(
                    "[cognition] enabled needs [notes] enabled: a session writes the notebook",
                )

            async def _note_titles(unit: str) -> Sequence[str]:
                if note_store is None:
                    return ()
                return [n.title for n in await note_store.standing(unit=unit)]

            async def _note_index(unit: str) -> Sequence[Any]:
                if note_store is None:
                    return ()
                return await note_store.standing(unit=unit)

            _open_events = open_events_reader(event_store)

            # The unit -> its rooms' every id form, for the grounding read: a
            # stimulus naming a room outside its unit is never read (DEC-231).
            cognition_unit_rooms: dict[str, set[tuple[str, str]]] = {}
            for conv in allowlist.conversations:
                cognition_unit_rooms.setdefault(conv.unit, set()).update(
                    (conv.platform, cid) for cid in conv.ids
                )
            if cfg.cognition.upcoming_days > 0:
                upcoming_rooms: dict[str, list[tuple[str, str, Collection[str]]]] = {}
                for conv in allowlist.conversations:
                    upcoming_rooms.setdefault(conv.unit, []).append(
                        (conv.platform, conv.primary_id, conv.ids),
                    )
                cognition_upcoming = upcoming_reader(
                    _open_events, lambda unit: upcoming_rooms.get(unit, []),
                    days=cfg.cognition.upcoming_days,
                    room_proactive=lambda platform, cid: allowlist.proactive_allowed(
                        platform=platform, conversation_id=cid,
                    ),
                )
            # The policy's label for each of those ids: the prompt calls a
            # room by it and never by the conversation id.
            cognition_room_labels: dict[tuple[str, str], str] = {
                (conv.platform, cid): conv.label
                for conv in allowlist.conversations for cid in conv.ids
            }

            async def _room_events(unit: str, conversation_id: str) -> Sequence[Any]:
                return await _open_events(conversation_id)

            grounder = build_grounder(
                # NOT `tool_registry`'s `record`, which is None unless `[record_query]
                # enabled`: the rows that raised a stimulus are code's own input, and a
                # session must be grounded with the tool off.
                records=SqlRecordQuery(engine), persons=persons, media_join=media_join,
                note_index=_note_index, events_in=_room_events,
                memo_index=lambda unit: memo_store.kept(unit=unit),
                pending_reminds=lambda: reminders.pending_of(cfg.memory.ai_peer),
                lineage=lineage,
                unit_rooms=lambda unit: cognition_unit_rooms.get(unit, set()),
                ai_display_name=cfg.doctrine.display_name,
                room_label=lambda platform, cid: cognition_room_labels.get((platform, cid), ""),
                clock=lambda: clock().timestamp(),
                own_send_lookback_seconds=cfg.cognition.own_send_lookback_seconds,
                own_sends_max=cfg.cognition.own_sends_max,
                tz=ZoneInfo(cfg.cognition.timezone),
                # A deviation line says where the stay was and what is usual
                # for the person (DEC-279), scored with the analytics process's
                # own thresholds, as the rollup that raised it was.
                stay_context=stay_context_reader(
                    mobility, tz_name=cfg.analytics.tz_name,
                    min_samples=cfg.analytics.anomaly_min_samples,
                    z_threshold=cfg.analytics.anomaly_z_threshold,
                    novelty_radius_m=cfg.analytics.cluster_eps_m,
                ),
                # ...but names a place only where `where_been` would (R-35):
                # every room of the unit, read live, a subset of a circle the
                # person is in, or location-trusted (DEC-79).
                place_release=place_release(
                    rooms_of=lambda unit: [
                        (conv.platform, conv.primary_id, conv.location)
                        for conv in allowlist.conversations
                        if conv.unit == unit and conv.primary_id
                    ],
                    room_view=lambda platform, conversation_id: Room(
                        platform=platform, conversation_id=conversation_id,
                        roster_source=rosters.get(platform), persons=persons,
                        self_ids=registry.self_aliases(),
                    ).members(),
                    circles=presence_circles,
                ),
                # Her own account in the unit, under
                # `[culture] enabled` like every Character read.
                # Her own account in the unit, shown only where every room of the
                # unit agrees on `location` (R-31; `unit_self_reader`).
                self_account=unit_self_reader(culture_reader, allowlist),
                # THE SWITCH (45 §5): when `[persona] render` is on, her
                # Persona account replaces the prose one; the SAME reader the
                # tag and fire paths hold.
                self_persona=unit_self_persona_reader(persona_reader, allowlist),
                metrics=metrics,
            )

            try:
                cognition_runner = build_cognition_runner(
                    cfg=cfg, models=dream_models,
                    retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
                    metrics=metrics, tools=dream_tools, instructions=dreamer_instructions,
                    store=SqlCognitionStore(engine), stimulus_store=stimulus_store,
                    note_titles=_note_titles, open_events_in=_open_events,
                    notes=note_store, events=events, standing=standing_store,
                    memos=memo_store,
                    reminders=reminders, ai_peer=cfg.memory.ai_peer,
                    reminder_caps=reminder_caps,
                    room_proactive=lambda platform, conversation_id: allowlist.proactive_allowed(
                        platform=platform, conversation_id=conversation_id,
                    ),
                    # The HEAD with its `standing_revision` id: the token a
                    # `standing_revise` base is checked against (DEC-250).
                    standing_of=lambda platform, conversation_id: standing_store.read_head(
                        platform=platform, conversation_id=conversation_id,
                    ),
                    roster_of=_roster_names_of(rosters, persons, registry.self_aliases()),
                    homes=homes, units=cognition_wakeable,
                    skills=agenda_skills,
                    grounder=grounder,
                    person_known=lambda person_id: persons.by_person.get(
                        persons.subject_id_for(person_id),
                    ) is not None,
                    render_budget=RenderBudget(
                        record_chars=cfg.cognition.record_chars,
                        message_chars=cfg.cognition.message_chars,
                        register_lines=cfg.cognition.register_lines,
                    ),
                    surface=surface, clock=lambda: clock().timestamp(),
                    model_id=dream_model, model_route=cfg.llm.base_url,
                    budget=cognition_budget(cfg.dreamer.budget_for(COGNITION_ADAPTOR_NAME)),
                    result_caps=TOOL_RESULT_CAPS,
                )
            except (InstructionError, PreludeMissingError, SkillNotInstalledError) as exc:
                # A missing or malformed `cognition` objective, a prelude it resolved
                # without, or a mapped skill that is not installed: named at startup,
                # not the first wake's failure and never a raw `LookupError`.
                raise StartupError(f"Cognition session error: {exc}") from exc

    # The wake evaluator, after the Dreamer block so it can be handed the
    # runner's hook. The boot check below refuses `enabled` with no runner or
    # no hook. Only units a session can run for are woken.
    wake_evaluator: WakeEvaluator | None = None
    cognition_shutdown: CognitionShutdown | None = None
    if stimulus_store is not None:
        wake_evaluator = _build_wake_evaluator(
            cfg, store=stimulus_store,
            units=lambda: sorted(cognition_session_units or ()), metrics=metrics,
            on_wake=cognition_runner.wake_hook if cognition_runner is not None else None,
            session_units=(
                None if cognition_session_units is None else (lambda: cognition_session_units)
            ),
            floors_ready="time" in agenda_skills.by_kind,
            upcoming=cognition_upcoming,
        )
        if cognition_runner is not None:
            cognition_shutdown = CognitionShutdown(
                evaluator=wake_evaluator, runner=cognition_runner,
                grace_seconds=SESSION_SHUTDOWN_GRACE_SECONDS,
            )
    try:
        require_session_hook(
            enabled=cfg.cognition.enabled, runner=cognition_runner,
            evaluator=wake_evaluator, session_units=cognition_session_units,
        )
    except RuntimeError as exc:
        raise StartupError(str(exc)) from exc

    otp: OtpService | None = None
    otp_api_token = ""
    if cfg.otp.enabled:
        otp_secret, otp_api_token = _otp_secrets(cfg)
        otp = OtpService(
            store=SqlOtpStore(engine), persons=persons, adapters=dict(registry.adapters),
            records=audit, doctrine_store=doctrine_store, canary=cfg.doctrine.canary,
            metrics=metrics, secret=otp_secret,
            ttl=timedelta(seconds=cfg.otp.ttl_seconds),
            max_attempts=cfg.otp.max_attempts, max_per_hour=cfg.otp.max_per_hour,
            max_per_day=cfg.otp.max_per_day,
            clock=lambda: datetime.now(UTC),
        )
    return Dependencies(
        audit=audit, memory=memory, persons=persons, metrics=metrics,
        media_join=media_join,
        policy=policy, assembler=assembler, ingest=ingest, engagement=engagement,
        resolve_voice_room=_voice_room_of(allowlist),
        workspaces_of=functools.partial(workspaces_of_person, allowlist),
        documents=documents_store, document_service=document_service,
        rooms_of=functools.partial(rooms_named, allowlist),
        room_event_shown=functools.partial(room_event_shown, lineage),
        room_label=functools.partial(room_label, allowlist),
        blobs=blobs, artifacts=computer_exports,
        dispatcher=dispatcher, adapter=adapter, registry=registry,
        participation_trigger=participation_trigger,
        relevance_gate=gate,
        fire_path=fire_path,
        self_presence=presence_reader,
        clock=clock,
        proactive_screen=proactive_screen,
        account=account, doctrine=doctrine,
        doctrine_store=doctrine_store, debounce=debounce, background=background,
        llm_state=llm_state, client=client, reminders=reminders,
        games_client=games_client,
        presence=presence_store, presence_circles=presence_circles,
        mobility=mobility,
        skills_dir=cfg.doctrine.dir.parent / "skills",
        language_store=language_store,
        language_scopes=language_scopes,
        dreamer_instructions=dreamer_instructions,
        dreamer_runtime=dreamer_runtime,
        dream_adaptors=dream_adaptors,
        dream_schedules=dream_schedules,
        dream_schedule_clock=dream_schedule_clock,
        revisions=SqlRevisionStore(engine),
        unit_scopes=unit_scopes,
        events=events,
        speaker=speaker,
        voice_hub=voice_hub,
        voice_event_consumer=voice_event_consumer,
        wake_evaluator=wake_evaluator,
        cognition_runner=cognition_runner,
        cognition_shutdown=cognition_shutdown,
        subagent_backend=subagent_backend,
        wake_tick_seconds=cfg.cognition.tick_seconds,
        computer_sweeper=(
            ComputerSessionSweeper(
                sessions=computer_sessions, events=computer_events,
                runtime=computer, leases=computer_leases,
                rules=SweepRules(
                    idle_expiry=timedelta(hours=cfg.computer.sessions.idle_expiry_hours),
                    max_running=timedelta(days=cfg.computer.sessions.max_running_days),
                    idle_after=timedelta(minutes=cfg.computer.sessions.idle_after_minutes),
                ),
                clock=lambda: datetime.now(UTC), metrics=metrics,
            ) if computer is not None else None
        ),
        computer_sweep_seconds=cfg.computer.sessions.sweep_interval_seconds,
        computer_probe=computer_probe,
        health_recorder=AdapterHealthRecorder(
            log=SqlSourceHealthLog(engine), metrics=metrics,
            timeout_seconds=cfg.cognition.store_timeout_seconds,
        ),
        otp=otp,
        otp_api_token=otp_api_token,
        oauth_keepalive=oauth_keepalive,
        social_poller=social_poller,
        social_perception=social_perception,
        story_expiry=story_expiry,
        memory_embed=(
            MemoryEmbedWorker(
                engine, LlamaEmbedder(cfg.memory_index.embedder_url), blobs,
                model=fingerprint(cfg.memory_index.embedder_model),
                model_file=model_file(cfg.memory_index.embedder_model),
                batch=cfg.memory_index.embed_batch,
                images_per_hour=cfg.memory_index.embed_images_per_hour,
                metrics=metrics,
            ) if cfg.memory_index.embed else None
        ),
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


def _roster_names_of(
    rosters: Mapping[str, RosterSource], persons: Persons, self_ids: frozenset[str],
) -> Callable[[str, str], Awaitable[Mapping[str, str] | None]]:
    """A cognition session's roster seam: the room's roster NAMES with their
    person ids, read through the same `Room` the tag path resolves a room with
    (Nora's own accounts are not members: `self_ids`). `None` when the roster
    cannot be read, so a note write can say `unavailable` instead of refusing
    every owner against an empty room."""

    async def roster_of(platform: str, conversation_id: str) -> Mapping[str, str] | None:
        view = await Room(
            platform=platform, conversation_id=conversation_id,
            roster_source=rosters.get(platform), persons=persons, self_ids=self_ids,
        ).members()
        if not view.available:
            return None
        return {p.name: p.person_id for p in view.people}

    return roster_of


def reverse_geocoder(
    cfg: Config, environ: Mapping[str, str], *, cache_store: Any,
) -> ReverseGeocoder | None:
    """The on-demand reverse-geocoder `[presence] geocode_provider` names, or
    `None` when that backend's credential (read from `environ` under the env
    var the config names) or endpoint is absent. Independent of `[presence]
    enabled`: `reverse_geocode` holds it whatever the reader's state. A name of
    its own so a composition's geocoder can be checked without driving a turn
    (`tests/unit/test_eval_full_restore.py`)."""
    if cfg.presence.geocode_provider == "hkgov":
        hkgov_token = environ.get(cfg.presence.hkgov_token_env, "")
        if hkgov_token and cfg.presence.hkgov_geocode_url:
            from nora.infra.geocode_hkgov import HKGovGeocoder
            return HKGovGeocoder(
                service_url=cfg.presence.hkgov_geocode_url, token=hkgov_token,
                lang_zh=cfg.presence.hkgov_lang_zh, lang_en=cfg.presence.hkgov_lang_en,
                cache_store=cache_store, area_screen=screened_area,
            )
        return None
    maps_key = environ.get(cfg.presence.geocode_api_key_env, "")
    if maps_key:
        from nora.infra.geocode import GoogleGeocoder
        return GoogleGeocoder(
            api_key=maps_key, cache_store=cache_store, area_screen=screened_area,
        )
    return None


async def _presence_preflight(
    *,
    allowlist: Policy,
    rosters: Mapping[str, RosterSource],
    persons: Persons,
    circles: tuple[Circle, ...],
    metrics: Metrics,
    self_ids: frozenset[str] = frozenset(),
) -> None:
    """The R-21/R-31 disclosure pre-flight: for every policy
    conversation, is its resolved membership a SUBSET of each circle?

    `check_disclosure_invariant` publishes `nora_presence_circle_subset_ok` and
    logs an ERROR on the day the answer is no — the operator's tell that a
    stranger joined the group (or someone left the circle) and `whereis` is now
    refusing there. Belt-and-braces over the KNOWN person_ids only; the per-call
    guard in `Whereis.locate` is authoritative and sees unmapped accounts
    (`view.unnamed`) that a person_id subset cannot — so this logs those as a
    warning rather than pretending the gauge covers them.

    **In a location-TRUSTED room that per-call guard does not refuse on
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
        # per-call in `Whereis.locate` — but the gauge and the operator's
        # boot-time warning would silently cover only part of the rooms.
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
                # What remains in a trusted room (DEC-79) is the roster READ
                # (degraded → `unavailable`), the target resolving inside it,
                # and the waiver counter. Skipping this gauge is correct (it
                # would be a permanent false alarm), but it is the only
                # pre-flight this room gets.
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
                    # WHICH room, so the gauge is per-conversation: circle alone
                    # would be last-writer-wins across every allowlisted room,
                    # and a failing room could be overwritten by a passing one
                    # and the alarm lost.
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
    `not_configured`: it records every attachment as a placeholder with
    no handle, and counts each one, so custody being off is visible in the
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
        # Unconditional, like its two siblings and NOT gated on
        # `[platforms.instagram] enabled`: the registry is keyed by platform,
        # so a fetcher for a platform producing no messages costs nothing,
        # while a gate here would make custody depend on two settings instead
        # of one. Absent one, `stages.media` counts every Instagram
        # attachment `not_supported`.
        InstagramMediaSource(
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


def _note_unit_of(policy: Policy) -> Callable[[str, str], str | None]:
    """`(platform, conversation_id)` → the room's unit, or `None`.

    A free function rather than a lambda so the `None` branch is nameable: a
    room `policy.toml` does not list has no unit, and the note verbs must
    answer `unavailable` for it. Guessing one — defaulting to "nora", or to
    whichever unit happens to be first — would write a note into a
    notebook the room is not part of, and `standing` would then render it to
    people who never saw the conversation it came from.
    """

    def _resolve(platform: str, conversation_id: str) -> str | None:
        convo = policy.conversation(platform, conversation_id)
        return convo.unit if convo is not None else None

    return _resolve


def _room_ids_of(policy: Policy) -> Callable[[str, str], frozenset[str]]:
    """Every id the room `(platform, conversation_id)` answers to (`Conversation.ids`),
    or just the id itself for a room the policy does not list. A room with an alias is
    one room to `DocumentService`: for reading its pictures and for revising its own
    document."""

    def room_ids(platform: str, conversation_id: str) -> frozenset[str]:
        convo = policy.conversation(platform, conversation_id)
        return convo.ids if convo is not None else frozenset({conversation_id})

    return room_ids


def _voice_room_of(policy: Policy) -> Callable[[str], str | None]:
    """A Discord `guild_id` → the `conversation_id` its speech is recorded in,
    or `None`.

    A free function for the same reason as `_note_unit_of`: the `None`
    branch has to be nameable. A guild with no room marked `voice = true` has
    nowhere its speech belongs, and `nora.web.voice` answers 400 — falling
    back to the voice channel's own id would create per-channel rooms, one
    spoken sentence at a time.

    Returns the id rather than the `Conversation` so `nora.web` stays unaware
    that `nora.policy` exists at all."""

    def _resolve(guild_id: str) -> str | None:
        convo = policy.voice_room("discord", guild_id)
        return convo.primary_id if convo is not None else None

    return _resolve


def _music_channel_of(policy: Policy) -> Callable[[str], str | None]:
    """A Discord `guild_id` → the CHANNEL id its music announcements are
    posted in, or `None`.

    A thin bind of `Policy.music_channel_for`, for `_voice_room_of`'s reason:
    the platform layer must not learn that `nora.policy` exists, and the
    `None` branch has to be nameable. `None` means something different here
    than in `_voice_room_of` — not a refusal but the fallback to where the
    session started, which `music_notify.make_notify` holds."""

    def _resolve(guild_id: str) -> str | None:
        return policy.music_channel_for("discord", guild_id)

    return _resolve
