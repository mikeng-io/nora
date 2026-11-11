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

import functools
import importlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from nora.agent.dreamer import (
    AdaptorSchedule,
    DreamerRuntime,
)
from nora.agent.whereis import Circle
from nora.app.bridge_queue import BridgeRuntime
from nora.app.cognition_session import CognitionSessionRunner
from nora.app.cognition_wiring import (
    CognitionShutdown,
)
from nora.app.composition_agent import (
    backend,
    backends,
    backends_check,
    desk,
    mcp,
)
from nora.app.composition_bridge import runtime
from nora.app.composition_documents import service
from nora.app.composition_dreamer import cognition_inits, enabled, guidance, preamble, wake
from nora.app.composition_fire import (
    build_gate,
    build_recover,
    compute_poller,
    heartbeat,
    path,
    trigger,
)
from nora.app.composition_models import build_runner, lane, runners, source
from nora.app.composition_pipelines import (
    build_dispatcher,
    build_engagement,
    build_ingest,
    build_shared,
    standing,
)
from nora.app.composition_platforms import adapters, channels, policies, presence_preflight
from nora.app.composition_runtime import build_slots, stimulus, tasks
from nora.app.composition_social import expiry, inbox, poller, summons
from nora.app.composition_stores import (
    build_audit,
    build_decision_log,
    build_doctrine,
    build_language_scopes,
    build_language_store,
    build_otp,
    database,
    identity,
    instructions,
    obs_stores,
    observation,
    readers,
    runtime_stores,
)
from nora.app.computer_sweeper import ComputerSessionSweeper
from nora.app.documents_service import DocumentService, room_label, rooms_named
from nora.app.graph_batches import GraphBatchProducer
from nora.app.memory_routing import (
    profile_scopes_of_person,
    workspaces_of_person,
)
from nora.app.oauth_keepalive import OAuthKeepalive
from nora.app.otp import OtpService
from nora.app.social_expiry import StoryMediaExpiry
from nora.app.social_perception import SocialPerception
from nora.app.social_poller import SocialPoller
from nora.app.source_health import AdapterHealthRecorder
from nora.app.startup import StartupError  # noqa: F401 — boot surface for __main__
from nora.app.subagent_backend import SubagentBackend
from nora.app.voice_events import VoiceEventConsumer
from nora.app.wake import WakeEvaluator
from nora.config import Config
from nora.config_reload import ReloadFiles
from nora.doctrine.loader import Doctrine, DoctrineStore
from nora.domain.computer_exports import ComputerExportStore
from nora.domain.documents import DocumentStore, RoomKey
from nora.domain.media_join import MediaJoin
from nora.domain.memory_index import IndexState
from nora.domain.ports import (
    BlobStore,
    ContextAssembler,
    DreamAdaptor,
    EngagePolicy,
    MemoryStore,
    RecordStore,
    Speaker,
)
from nora.domain.reminders import ReminderStore
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.self_presence import PresenceReader
from nora.infra.computer.probe import ComputerdProbe
from nora.infra.embedder import LlamaEmbedder, fingerprint, model_file
from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.dream_schedule import SqlDreamScheduleClock
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.memory_embed import MemoryEmbedWorker
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.revisions import SqlRevisionStore
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.infra.voice.hub import VoiceControllerHub
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.relevance_gate import RelevanceGate
from nora.instructions.dreamer import DreamerInstructions
from nora.obs.health import LLMState
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.bridge.client import (
    BridgeAdapter,
)
from nora.platform.registry import AdapterRegistry
from nora.platform.signal.adapter import SignalAdapter
from nora.policy import Policy
from nora.tag import Debounce
from nora.web.games import PulseGamesClient

from .dispatch import Dispatcher
from .fire import (
    FirePath,
)
from .gatherings import GatheringService, room_event_shown
from .participation import ParticipationTrigger
from .proactive_screen import ProactiveScreen
from .runtime import BackgroundTasks, ComputerLifecycle

if TYPE_CHECKING:
    pass

log = logging.getLogger("nora.app.composition")


# Provider name (`DescriberConfig.provider`) → its direct lane. `"omniroute"`
# is NOT here: it is the process-wide gateway client, built once above for
# every model call, not a describer lane.


#: Every Character adaptor that is not the room fold, by name (budgets, grants).


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
    config_reload: ReloadFiles
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
    # The portal's Profile reads (45, the switch): the read-only store (None
    # until `[memory.profile] read` is on — `profile_reader` above), a person_id ->
    # the (unit, workspace) pairs their accounts live under, and the Record
    # read that resolves a line's message/episode citations for the page.
    profile_portal: SqlProfileStore | None
    profile_scopes_of: Callable[[str], tuple[tuple[str, str], ...]] | None
    profile_cited: SqlCharacterEvidence | None
    # /health's `index` block: the memory index's state and queue length
    # (`SqlMemoryIndex.index_state`, no model fingerprint).
    index_state: Callable[[], Awaitable[IndexState]]
    # Documents (ADR 0020): the store the portal reads, the service that publishes into
    # it (the `document_publish` tool's collaborator), and person_id -> the rooms whose
    # `persons` name them, from the CURRENT policy, for the same reason as
    # `workspaces_of`: `nora.web` never sees a `Policy`.
    documents: DocumentStore
    document_service: DocumentService
    rooms_of: Callable[[str], frozenset[RoomKey]]
    # Whether a member of a room may see an event of it they neither made nor
    # answered on the portal's Plans (`nora.app.gatherings.room_event_shown`,
    # bound to the process's lineage policy AND its trusted room claims, so the
    # membership grant also checks the event's persisted owner).
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
    #: The elected stream readers + work runner when any platform runs
    #: `mode = "bridge"`; `nora/__main__.py` spawns it like every other loop.
    bridge_runtime: BridgeRuntime | None
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
    adapter: SignalAdapter | BridgeAdapter
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
    model_clients: list[AsyncOpenAI]
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
    # page); the web routes instead scope every read to the circles the
    # signed-in viewer belongs to (`nora.web.api._viewer_circles`).
    presence: PresenceStore | None
    presence_circles: Sequence[Circle]
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
    # The scoped computer's queue granter (`ComputerQueueRunner`) and tab
    # manager: `None` unless `[computer.scoped] enabled`. `nora/__main__.py`
    # spawns `runtime.computer_queue_loop` on the runner and
    # `runtime.computer_browser_sweep_loop` on the manager.
    computer_queue_runner: Any = None
    browser_manager: Any = None
    browser_sweep_seconds: float = 30.0
    # The scoped computer's ONE start/close owner
    # (`nora.app.runtime.ComputerLifecycle`): `start` spawns the queue and
    # tab-sweep loops, `close` stops admission and releases the Redis
    # client and journal lock before the SQL/audit teardown. `None`
    # unless `[computer.scoped] enabled`. `__main__` and the eval subject
    # share it, so a subject runs the same maintenance the deployment
    # does.
    computer_lifecycle: Any = None
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
    # The memory index's embedding worker: `None` under `[memory.index] embed =
    # false`, the default. `__main__` hands it to the memory drain loop.
    memory_embed: MemoryEmbedWorker | None = None
    graph_batches: GraphBatchProducer | None = None
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


def wall_clock() -> datetime:
    """The deployment's clock. THE default, and the only one production uses."""
    return datetime.now(UTC)


async def build_dependencies(
    cfg: Config,
    *,
    clock: Callable[[], datetime] = wall_clock,
    operational_clock: Callable[[], datetime] | None = None,
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

    **`operational_clock` is the maintenance clock.** A frozen `clock` is a
    semantic statement («the turn sees this instant»); it must not freeze the
    queue's deadline, lease and idle arithmetic — under a frozen snapshot the
    scoped computer's tasks would never time out and its tabs never idle.
    The scoped-computer machinery (`ComputerAdmission`, `ComputerQueueRunner`,
    `ScopeBrowserManager`, the deferred outbox's stamp) reads
    `operational_clock`, which defaults to `clock`; the eval subject passes
    the wall clock so maintenance keeps moving while the block's time stands
    still.
    """
    op_clock = operational_clock or clock
    content_checks = cfg.content_checks.enabled
    metrics = Metrics()
    _agenda_wire = importlib.import_module("nora.agenda").wire
    _computer_wire = importlib.import_module("nora.computer").wire
    _conversation_wire = importlib.import_module("nora.conversation").wire
    _documents_wire = importlib.import_module("nora.documents").wire
    _location_wire = importlib.import_module("nora.location").wire
    _media_wire = importlib.import_module("nora.media").wire
    _memory_wire = importlib.import_module("nora.memory").wire
    _people_wire = importlib.import_module("nora.people").wire
    _social_wire = importlib.import_module("nora.social").wire
    _subagents_wire = importlib.import_module("nora.subagents").wire
    _voice_wire = importlib.import_module("nora.voice").wire
    _platforms_channels = channels(cfg=cfg)
    _discord_bridge = _platforms_channels._discord_bridge
    channel_directory = _platforms_channels.channel_directory
    discord_box = _platforms_channels.discord_box
    discord_control = _platforms_channels.discord_control
    _stores_identity = identity(cfg=cfg, channel_directory=channel_directory, metrics=metrics)
    allowlist = _stores_identity.allowlist
    persons = _stores_identity.persons
    reload_files = _stores_identity.reload_files
    _stores_database = database(allowlist=allowlist, cfg=cfg, metrics=metrics, persons=persons)
    engine = _stores_database.engine
    _stores_audit = build_audit(cfg=cfg, engine=engine, metrics=metrics)
    audit = _stores_audit.audit
    _location_stores = _location_wire.stores(engine=engine)
    mobility = _location_stores.mobility
    _agenda_reminders = _agenda_wire.reminders(engine=engine)
    reminders = _agenda_reminders.reminders
    _stores_runtime_stores = runtime_stores(engine=engine)
    compute_jobs = _stores_runtime_stores.compute_jobs
    tool_call_log_store = _stores_runtime_stores.tool_call_log_store
    _computer_stores = _computer_wire.stores(cfg=cfg, engine=engine, metrics=metrics)
    computer = _computer_stores.computer
    computer_events = _computer_stores.computer_events
    computer_leases = _computer_stores.computer_leases
    computer_metrics = _computer_stores.computer_metrics
    computer_probe = _computer_stores.computer_probe
    computer_sessions = _computer_stores.computer_sessions
    computer_token = _computer_stores.computer_token
    _computer_queue = _computer_wire.queue()
    browser_manager = _computer_queue.browser_manager
    browser_tabs = _computer_queue.browser_tabs
    computer_admission = _computer_queue.computer_admission
    computer_dm_scope_for = _computer_queue.computer_dm_scope_for
    computer_queue_runner = _computer_queue.computer_queue_runner
    computer_scope_for = _computer_queue.computer_scope_for
    computer_task_backend = _computer_queue.computer_task_backend
    computer_tasks = _computer_queue.computer_tasks
    _computer_scoped = await _computer_wire.scoped(
        allowlist=allowlist,
        browser_tabs=browser_tabs,
        cfg=cfg,
        clock=clock,
        compute_jobs=compute_jobs,
        computer=computer,
        computer_admission=computer_admission,
        computer_dm_scope_for=computer_dm_scope_for,
        computer_events=computer_events,
        computer_metrics=computer_metrics,
        computer_tasks=computer_tasks,
        computer_token=computer_token,
        engine=engine,
        metrics=metrics,
        op_clock=op_clock,
    )
    browser_manager = _computer_scoped.browser_manager
    browser_tabs = _computer_scoped.browser_tabs
    computer = _computer_scoped.computer
    computer_admission = _computer_scoped.computer_admission
    computer_dm_scope_for = _computer_scoped.computer_dm_scope_for
    computer_probe = _computer_scoped.computer_probe
    computer_queue_runner = _computer_scoped.computer_queue_runner
    computer_scope_for = _computer_scoped.computer_scope_for
    computer_task_backend = _computer_scoped.computer_task_backend
    computer_tasks = _computer_scoped.computer_tasks
    _computer_context = _computer_wire.context(
        allowlist=allowlist,
        browser_tabs=browser_tabs,
        cfg=cfg,
        computer=computer,
        computer_events=computer_events,
        computer_scope_for=computer_scope_for,
        computer_sessions=computer_sessions,
        computer_tasks=computer_tasks,
        metrics=metrics,
    )
    computer_context = _computer_context.computer_context
    _stores_obs_stores = obs_stores(engine=engine)
    reasoning_strip_store = _stores_obs_stores.reasoning_strip_store
    tag_trace_store = _stores_obs_stores.tag_trace_store
    _memory_stores = _memory_wire.stores(engine=engine)
    standing_store = _memory_stores.standing_store
    _stores_observation = observation(engine=engine)
    observation_store = _stores_observation.observation_store
    _memory_ledger = _memory_wire.ledger(engine=engine, metrics=metrics)
    ledger_store = _memory_ledger.ledger_store
    _media_stores = _media_wire.stores(engine=engine, metrics=metrics)
    media_objects = _media_stores.media_objects
    _documents_stores = _documents_wire.stores(engine=engine)
    documents_store = _documents_stores.documents_store
    _location_route = _location_wire.route(cfg=cfg)
    route_client = _location_route.route_client
    _documents_ground = _documents_wire.ground(
        allowlist=allowlist,
        cfg=cfg,
        content_checks=content_checks,
        doctrine_store=lambda: doctrine_store,
        persons=persons,
    )
    cognition_context_screen = _documents_ground.cognition_context_screen
    cognition_shared_document_room = _documents_ground.cognition_shared_document_room
    _documents_service = service(
        allowlist=allowlist,
        audit=audit,
        cfg=cfg,
        clock=clock,
        cognition_context_screen=cognition_context_screen,
        cognition_shared_document_room=cognition_shared_document_room,
        content_checks=content_checks,
        documents_store=documents_store,
        media_objects=media_objects,
        metrics=metrics,
        persons=persons,
        route_client=route_client,
    )
    document_service = _documents_service.document_service
    documents_context = _documents_service.documents_context
    _subagents_subjects = _subagents_wire.subjects(
        cfg=cfg, compute_jobs=compute_jobs, document_service=document_service, metrics=metrics
    )
    subagents_context = _subagents_subjects.subagents_context
    _media_join = _media_wire.join(
        cfg=cfg, content_checks=content_checks, media_objects=media_objects, metrics=metrics
    )
    media_join = _media_join.media_join
    _conversation_quote = _conversation_wire.quote(audit=audit, cfg=cfg, metrics=metrics)
    quote_window = _conversation_quote.quote_window
    _stores_decision_log = build_decision_log(
        allowlist=allowlist, clock=clock, engine=engine, metrics=metrics
    )
    decision_log = _stores_decision_log.decision_log
    _stores_language_store = build_language_store(cfg=cfg, engine=engine, metrics=metrics)
    language_store = _stores_language_store.language_store
    _memory_notes = _memory_wire.notes(cfg=cfg, engine=engine)
    memo_store = _memory_notes.memo_store
    note_store = _memory_notes.note_store
    _voice_speech = _voice_wire.speech(
        _discord_bridge=_discord_bridge,
        allowlist=allowlist,
        cfg=cfg,
        engine=engine,
        metrics=metrics,
    )
    discord_token = _voice_speech.discord_token
    music_manager = _voice_speech.music_manager
    voice_hub = _voice_speech.voice_hub
    _stores_doctrine = build_doctrine(cfg=cfg, content_checks=content_checks, metrics=metrics)
    doctrine = _stores_doctrine.doctrine
    doctrine_store = _stores_doctrine.doctrine_store
    _voice_speaker = _voice_wire.speaker(cfg=cfg, doctrine_store=doctrine_store, metrics=metrics)
    speaker = _voice_speaker.speaker
    _voice_rooms = _voice_wire.rooms(
        _discord_bridge=_discord_bridge,
        allowlist=allowlist,
        music_manager=music_manager,
        voice_hub=voice_hub,
    )
    resolve_music_channel = _voice_rooms.resolve_music_channel
    voice_event_consumer = _voice_rooms.voice_event_consumer
    voice_guild_ids = _voice_rooms.voice_guild_ids
    _social_inbox = inbox(cfg=cfg, metrics=metrics)
    summons_inbox = _social_inbox.summons_inbox
    summons_wired = _social_inbox.summons_wired
    _platforms_adapters = await adapters(
        cfg=cfg,
        discord_box=discord_box,
        discord_token=discord_token,
        metrics=metrics,
        music_manager=music_manager,
        persons=persons,
        resolve_music_channel=resolve_music_channel,
        speaker=speaker,
        summons_inbox=summons_inbox,
        voice_guild_ids=voice_guild_ids,
        voice_hub=voice_hub,
    )
    account = _platforms_adapters.account
    adapter = _platforms_adapters.adapter
    registry = _platforms_adapters.registry
    _stores_instructions = instructions(cfg=cfg, metrics=metrics)
    dreamer_instructions = _stores_instructions.dreamer_instructions
    _memory_honcho = await _memory_wire.honcho(allowlist=allowlist, cfg=cfg, persons=persons)
    memory = _memory_honcho.memory
    _runtime_tasks = await tasks(
        cfg=cfg, channel_directory=channel_directory, engine=engine, reload_files=reload_files
    )
    background = _runtime_tasks.background
    debounce = _runtime_tasks.debounce
    llm_state = _runtime_tasks.llm_state
    _platforms_policies = policies(
        allowlist=allowlist,
        cfg=cfg,
        discord_control=discord_control,
        discord_token=discord_token,
        registry=registry,
    )
    policy = _platforms_policies.policy
    presence_reader = _platforms_policies.presence_reader
    rosters = _platforms_policies.rosters
    _stores_readers = readers(cfg=cfg, content_checks=content_checks, engine=engine)
    culture_reader = _stores_readers.culture_reader
    lessons_reader = _stores_readers.lessons_reader
    profile_index = _stores_readers.profile_index
    profile_reader = _stores_readers.profile_reader
    profile_store_reader = _stores_readers.profile_store_reader
    _people_note_maps = _people_wire.note_maps(allowlist=allowlist)
    _memory_search = _memory_wire.search(cfg=cfg, engine=engine, metrics=metrics)
    memory_index_reader = _memory_search.memory_index_reader
    memory_search = _memory_search.memory_search
    _people_unit_rooms = _people_wire.unit_rooms(allowlist=allowlist)
    unit_rooms = _people_unit_rooms.unit_rooms
    _pipelines_shared = build_shared(
        allowlist=allowlist,
        audit=audit,
        background=background,
        cfg=cfg,
        clock=clock,
        computer_context=computer_context,
        culture_reader=culture_reader,
        documents_context=documents_context,
        engine=engine,
        ledger_store=ledger_store,
        lessons_reader=lessons_reader,
        media_join=media_join,
        memo_store=memo_store,
        memory=memory,
        memory_search=memory_search,
        metrics=metrics,
        note_store=note_store,
        observation_store=observation_store,
        persons=persons,
        presence_reader=presence_reader,
        profile_reader=profile_reader,
        quote_window=quote_window,
        registry=registry,
        reminders=reminders,
        rosters=rosters,
        standing_store=standing_store,
        subagents_context=subagents_context,
        tag_trace_store=tag_trace_store,
        unit_rooms=unit_rooms,
    )
    _spawn = _pipelines_shared._spawn
    assembler = _pipelines_shared.assembler
    shared = _pipelines_shared.shared
    _media_sources = await _media_wire.sources(cfg=cfg)
    blobs = _media_sources.blobs
    media_sources = _media_sources.media_sources
    _models_lane = lane(cfg=cfg)
    client = _models_lane.client
    extra_model_clients = _models_lane.extra_model_clients
    model_client_for = _models_lane.model_client_for
    route_model = _models_lane.route_model
    tier = _models_lane.tier
    _stores_language_scopes = build_language_scopes(allowlist=allowlist)
    language_scopes = _stores_language_scopes.language_scopes
    _people_unit_scopes = _people_wire.unit_scopes(allowlist=allowlist)
    unit_scopes = _people_unit_scopes.unit_scopes
    _models_source = source(client=client, route_model=route_model, tier=tier)
    models = _models_source.models
    _computer_browse = _computer_wire.browse(
        browser_manager=browser_manager,
        cfg=cfg,
        computer=computer,
        model_client_for=model_client_for,
        route_model=route_model,
    )
    browser = _computer_browse.browser
    _models_runner = build_runner(cfg=cfg, metrics=metrics, models=models, tier=tier)
    runner = _models_runner.runner
    _people_homes = _people_wire.homes(allowlist=allowlist)
    proactive_homes = _people_homes.proactive_homes
    _models_runners = runners(
        cfg=cfg,
        metrics=metrics,
        model_client_for=model_client_for,
        route_model=route_model,
        summons_wired=summons_wired,
        tier=tier,
    )
    limits = _models_runners.limits
    participation_runner = _models_runners.participation_runner
    proactive_runner = _models_runners.proactive_runner
    summons_runner = _models_runners.summons_runner
    _agenda_reminder_caps = _agenda_wire.reminder_caps(cfg=cfg)
    reminder_caps = _agenda_reminder_caps.reminder_caps
    _agenda_event_store = _agenda_wire.event_store(allowlist=allowlist, engine=engine)
    event_room_claims = _agenda_event_store.event_room_claims
    event_store = _agenda_event_store.event_store
    _location_place_areas = _location_wire.place_areas(cfg=cfg, route_client=route_client)
    place_areas = _location_place_areas.place_areas
    _agenda_events = _agenda_wire.events(
        cfg=cfg,
        content_checks=content_checks,
        event_store=event_store,
        memory=memory,
        place_areas=place_areas,
        route_client=route_client,
    )
    events = _agenda_events.events
    _media_describers = _media_wire.describers(
        _spawn=_spawn,
        blobs=blobs,
        cfg=cfg,
        content_checks=content_checks,
        engine=engine,
        media_objects=media_objects,
        memory=memory,
        metrics=metrics,
        model_client_for=model_client_for,
        route_model=route_model,
        tier=tier,
    )
    comprehension = _media_describers.comprehension
    media_descriptions = _media_describers.media_descriptions
    sticker_reader = _media_describers.sticker_reader
    transcriber = _media_describers.transcriber
    vision_client = _media_describers.vision_client
    _runtime_slots = build_slots(background=background, cfg=cfg)
    slots = _runtime_slots.slots
    _runtime_stimulus = stimulus(allowlist=allowlist, cfg=cfg, engine=engine, registry=registry)
    stimulus_store = _runtime_stimulus.stimulus_store
    stimulus_wiring = _runtime_stimulus.stimulus_wiring
    _pipelines_ingest = build_ingest(
        _spawn=_spawn,
        allowlist=allowlist,
        audit=audit,
        blobs=blobs,
        cfg=cfg,
        comprehension=comprehension,
        media_objects=media_objects,
        media_sources=media_sources,
        memory=memory,
        metrics=metrics,
        persons=persons,
        policy=policy,
        sticker_reader=sticker_reader,
        stimulus_wiring=stimulus_wiring,
        transcriber=transcriber,
    )
    ingest = _pipelines_ingest.ingest
    _media_search = _media_wire.search(cfg=cfg)
    fetch_client = _media_search.fetch_client
    search_client = _media_search.search_client
    _social_clients = _social_wire.clients(allowlist=allowlist, cfg=cfg, persons=persons)
    social_client = _social_clients.social_client
    social_subjects = _social_clients.social_subjects
    _location_weather = _location_wire.weather(cfg=cfg)
    weather_client = _location_weather.weather_client
    _people_steam = _people_wire.steam(cfg=cfg)
    steam_client = _people_steam.steam_client
    _location_presence = _location_wire.presence(
        cfg=cfg, engine=engine, metrics=metrics, persons=persons
    )
    presence_circles = _location_presence.presence_circles
    presence_store = _location_presence.presence_store
    _media_images = _media_wire.images(cfg=cfg, metrics=metrics)
    image_generator = _media_images.image_generator
    image_refs_allowed = _media_images.image_refs_allowed
    oauth_keepalive = _media_images.oauth_keepalive
    preview_source = _media_images.preview_source
    _location_refresh = _location_wire.refresh(cfg=cfg, presence_store=presence_store)
    refresher = _location_refresh.refresher
    _location_geocoder = _location_wire.geocoder(cfg=cfg, engine=engine)
    geocoder = _location_geocoder.geocoder
    _agent_backends = backends()
    compute_backends = _agent_backends.compute_backends
    _media_video = _media_wire.video(
        cfg=cfg,
        clock=clock,
        compute_backends=compute_backends,
        compute_jobs=compute_jobs,
        metrics=metrics,
    )
    video_desk = _media_video.video_desk
    _computer_settings = _computer_wire.settings(cfg=cfg)
    computer_settings = _computer_settings.computer_settings
    _computer_exports = _computer_wire.exports(engine=engine)
    computer_exports = _computer_exports.computer_exports
    _agent_mcp = mcp(cfg=cfg)
    mcp_tool_groups = _agent_mcp.mcp_tool_groups
    version_manifest = _agent_mcp.version_manifest
    _conversation_reactions = _conversation_wire.reactions(
        cfg=cfg, content_checks=content_checks, doctrine_store=doctrine_store, metrics=metrics
    )
    giphy_client = _conversation_reactions.giphy_client
    reaction_picker = _conversation_reactions.reaction_picker
    _people_games = _people_wire.games(cfg=cfg)
    games_client = _people_games.games_client
    _conversation_relay = await _conversation_wire.relay(
        allowlist=allowlist,
        content_checks=content_checks,
        metrics=metrics,
        presence_reader=presence_reader,
        slots=slots,
    )
    relay = _conversation_relay.relay
    _voice_platforms = _voice_wire.platforms(
        _discord_bridge=_discord_bridge,
        cfg=cfg,
        discord_control=discord_control,
        music_manager=music_manager,
        registry=registry,
        resolve_music_channel=resolve_music_channel,
        voice_guild_ids=voice_guild_ids,
        voice_hub=voice_hub,
    )
    discord_presence = _voice_platforms.discord_presence
    music_bridge = _voice_platforms.music_bridge
    voice_presence = _voice_platforms.voice_presence
    _people_lineage = _people_wire.lineage(allowlist=allowlist, cfg=cfg, persons=persons)
    lineage = _people_lineage.lineage
    _agent_desk = desk(
        cfg=cfg, clock=clock, compute_jobs=compute_jobs, document_service=document_service
    )
    subagent_desk = _agent_desk.subagent_desk
    _agent_registry = registry(
        allowlist=allowlist,
        blobs=blobs,
        browser=browser,
        browser_manager=browser_manager,
        cfg=cfg,
        clock=clock,
        computer=computer,
        computer_admission=computer_admission,
        computer_context=computer_context,
        computer_dm_scope_for=computer_dm_scope_for,
        computer_events=computer_events,
        computer_exports=computer_exports,
        computer_leases=computer_leases,
        computer_sessions=computer_sessions,
        computer_settings=computer_settings,
        content_checks=content_checks,
        culture_reader=culture_reader,
        discord_presence=discord_presence,
        document_service=document_service,
        engine=engine,
        events=events,
        fetch_client=fetch_client,
        geocoder=geocoder,
        giphy_client=giphy_client,
        image_generator=image_generator,
        image_refs_allowed=image_refs_allowed,
        ledger_store=ledger_store,
        lineage=lineage,
        mcp_tool_groups=mcp_tool_groups,
        media_descriptions=media_descriptions,
        media_join=media_join,
        media_objects=media_objects,
        memo_store=memo_store,
        memory_search=memory_search,
        metrics=metrics,
        mobility=mobility,
        music_bridge=music_bridge,
        note_store=note_store,
        observation_store=observation_store,
        persons=persons,
        presence_circles=presence_circles,
        presence_reader=presence_reader,
        presence_store=presence_store,
        profile_index=profile_index,
        profile_reader=profile_reader,
        reaction_picker=reaction_picker,
        refresher=refresher,
        relay=relay,
        reminder_caps=reminder_caps,
        reminders=reminders,
        route_client=route_client,
        search_client=search_client,
        social_client=social_client,
        standing_store=standing_store,
        steam_client=steam_client,
        subagent_desk=subagent_desk,
        tool_call_log_store=tool_call_log_store,
        version_manifest=version_manifest,
        video_desk=video_desk,
        vision_client=vision_client,
        voice_presence=voice_presence,
        weather_client=weather_client,
    )
    tool_registry = _agent_registry.tool_registry
    _agent_subagent_backend = backend(
        audit=audit,
        cfg=cfg,
        clock=clock,
        compute_backends=compute_backends,
        compute_jobs=compute_jobs,
        computer_admission=computer_admission,
        computer_scope_for=computer_scope_for,
        memory=memory,
        metrics=metrics,
        model_client_for=model_client_for,
        persons=persons,
        registry=registry,
        rosters=rosters,
        route_model=route_model,
        subagent_desk=subagent_desk,
        subagents_context=subagents_context,
        tier=tier,
        tool_registry=tool_registry,
    )
    subagent_backend = _agent_subagent_backend.subagent_backend
    _computer_dispatch = _computer_wire.dispatch(
        allowlist=allowlist,
        audit=audit,
        compute_backends=compute_backends,
        computer_queue_runner=computer_queue_runner,
        computer_scope_for=computer_scope_for,
        computer_task_backend=computer_task_backend,
        memory=memory,
        metrics=metrics,
        persons=persons,
        registry=registry,
        rosters=rosters,
        tool_registry=tool_registry,
    )
    _agent_backends_check = backends_check(cfg=cfg, compute_backends=compute_backends)
    _platforms_presence_preflight = presence_preflight(
        allowlist=allowlist,
        background=background,
        content_checks=content_checks,
        metrics=metrics,
        persons=persons,
        presence_circles=presence_circles,
        presence_store=presence_store,
        registry=registry,
        rosters=rosters,
    )
    _pipelines_standing = await standing(
        audit=audit,
        cfg=cfg,
        clock=clock,
        content_checks=content_checks,
        dreamer_instructions=dreamer_instructions,
        ledger_store=ledger_store,
        lineage=lineage,
        media_join=media_join,
        metrics=metrics,
        standing_store=standing_store,
        tag_trace_store=tag_trace_store,
    )
    debrief_wiring = _pipelines_standing.debrief_wiring
    standing_fold = _pipelines_standing.standing_fold
    _pipelines_engagement = build_engagement(
        allowlist=allowlist,
        assembler=assembler,
        audit=audit,
        background=background,
        cfg=cfg,
        clock=clock,
        debounce=debounce,
        debrief_wiring=debrief_wiring,
        decision_log=decision_log,
        doctrine_store=doctrine_store,
        limits=limits,
        llm_state=llm_state,
        memory=memory,
        metrics=metrics,
        persons=persons,
        preview_source=preview_source,
        reasoning_strip_store=reasoning_strip_store,
        registry=registry,
        rosters=rosters,
        runner=runner,
        slots=slots,
        tag_trace_store=tag_trace_store,
        tier=tier,
        tool_call_log_store=tool_call_log_store,
        tool_registry=tool_registry,
    )
    engagement = _pipelines_engagement.engagement
    _fire_recover = await build_recover(
        clock=clock,
        compute_backends=compute_backends,
        compute_jobs=compute_jobs,
        metrics=metrics,
        reminders=reminders,
    )
    lost_jobs = _fire_recover.lost_jobs
    _fire_path = path(
        allowlist=allowlist,
        audit=audit,
        background=background,
        cfg=cfg,
        clock=clock,
        computer_context=computer_context,
        culture_reader=culture_reader,
        decision_log=decision_log,
        doctrine_store=doctrine_store,
        documents_context=documents_context,
        lessons_reader=lessons_reader,
        limits=limits,
        lineage=lineage,
        llm_state=llm_state,
        lost_jobs=lost_jobs,
        media_join=media_join,
        memo_store=memo_store,
        memory=memory,
        metrics=metrics,
        note_store=note_store,
        participation_runner=participation_runner,
        persons=persons,
        presence_reader=presence_reader,
        preview_source=preview_source,
        proactive_runner=proactive_runner,
        profile_reader=profile_reader,
        quote_window=quote_window,
        reasoning_strip_store=reasoning_strip_store,
        registry=registry,
        rosters=rosters,
        runner=runner,
        shared=shared,
        slots=slots,
        tag_trace_store=tag_trace_store,
        tool_call_log_store=tool_call_log_store,
        tool_registry=tool_registry,
    )
    fire_path = _fire_path.fire_path
    _fire_heartbeat = heartbeat(
        allowlist=allowlist,
        background=background,
        cfg=cfg,
        clock=clock,
        fire_path=fire_path,
        metrics=metrics,
        note_store=note_store,
        reminders=reminders,
        slots=slots,
    )
    proactive_screen = _fire_heartbeat.proactive_screen
    _fire_compute_poller = compute_poller(
        allowlist=allowlist,
        background=background,
        blobs=blobs,
        cfg=cfg,
        clock=clock,
        compute_backends=compute_backends,
        compute_jobs=compute_jobs,
        document_service=document_service,
        fire_path=fire_path,
        media_objects=media_objects,
        metrics=metrics,
        slots=slots,
    )
    _social_summons = summons(
        background=background,
        cfg=cfg,
        clock=clock,
        content_checks=content_checks,
        doctrine_store=doctrine_store,
        engine=engine,
        limits=limits,
        metrics=metrics,
        persons=persons,
        registry=registry,
        social_subjects=social_subjects,
        summons_inbox=summons_inbox,
        summons_runner=summons_runner,
        summons_wired=summons_wired,
        tier=tier,
        tool_registry=tool_registry,
    )
    _social_poller = poller(
        allowlist=allowlist,
        blobs=blobs,
        cfg=cfg,
        comprehension=comprehension,
        engine=engine,
        media_objects=media_objects,
        media_sources=media_sources,
        metrics=metrics,
        persons=persons,
        social_subjects=social_subjects,
        stimulus_store=stimulus_store,
    )
    social_perception = _social_poller.social_perception
    social_poller = _social_poller.social_poller
    _social_expiry = expiry(blobs=blobs, cfg=cfg, engine=engine, metrics=metrics)
    story_expiry = _social_expiry.story_expiry
    _fire_trigger = trigger()
    participation_trigger = _fire_trigger.participation_trigger
    _fire_gate = build_gate(
        audit=audit,
        cfg=cfg,
        doctrine_store=doctrine_store,
        engine=engine,
        fire_path=fire_path,
        media_join=media_join,
        metrics=metrics,
        model_client_for=model_client_for,
        persons=persons,
        route_model=route_model,
        slots=slots,
        standing_store=standing_store,
    )
    gate = _fire_gate.gate
    participation_trigger = _fire_gate.participation_trigger
    _pipelines_dispatcher = build_dispatcher(
        background=background,
        engagement=engagement,
        ingest=ingest,
        participation_trigger=participation_trigger,
    )
    dispatcher = _pipelines_dispatcher.dispatcher
    _bridge_runtime = await runtime(
        allowlist=allowlist,
        cfg=cfg,
        engagement=engagement,
        engine=engine,
        fire_path=fire_path,
        ingest=ingest,
        memory=memory,
        metrics=metrics,
        observation_store=observation_store,
        participation_trigger=participation_trigger,
        persons=persons,
        policy=policy,
        registry=registry,
        slots=slots,
        stimulus_wiring=stimulus_wiring,
    )
    bridge_runtime = _bridge_runtime.bridge_runtime
    _dreamer_preamble = preamble(engine=engine)
    dream_adaptors = _dreamer_preamble.dream_adaptors
    dream_schedule_clock = _dreamer_preamble.dream_schedule_clock
    dream_schedules = _dreamer_preamble.dream_schedules
    dreamer_runtime = _dreamer_preamble.dreamer_runtime
    _dreamer_guidance = guidance(dreamer_instructions=dreamer_instructions)
    cognition_lessons = _dreamer_guidance.cognition_lessons
    cognition_memory = _dreamer_guidance.cognition_memory
    cognition_runner = _dreamer_guidance.cognition_runner
    session_guidance = _dreamer_guidance.session_guidance
    _memory_graph = _memory_wire.graph(
        allowlist=allowlist,
        cfg=cfg,
        content_checks=content_checks,
        engine=engine,
        media_join=media_join,
        metrics=metrics,
        persons=persons,
        presence_circles=presence_circles,
        profile_index=profile_index,
        registry=registry,
        rosters=rosters,
        route_model=route_model,
        tier=tier,
        unit_scopes=unit_scopes,
    )
    cognition_memory = _memory_graph.cognition_memory
    _dreamer_cognition_inits = cognition_inits()
    cognition_session_units = _dreamer_cognition_inits.cognition_session_units
    cognition_upcoming = _dreamer_cognition_inits.cognition_upcoming
    _dreamer_enabled = await enabled(
        allowlist=allowlist,
        audit=audit,
        cfg=cfg,
        clock=clock,
        cognition_context_screen=cognition_context_screen,
        cognition_lessons=cognition_lessons,
        cognition_memory=cognition_memory,
        content_checks=content_checks,
        culture_reader=culture_reader,
        doctrine_store=doctrine_store,
        document_service=document_service,
        documents_context=documents_context,
        dream_adaptors=dream_adaptors,
        dream_schedules=dream_schedules,
        dreamer_instructions=dreamer_instructions,
        dreamer_runtime=dreamer_runtime,
        engine=engine,
        event_store=event_store,
        events=events,
        ledger_store=ledger_store,
        lineage=lineage,
        media_join=media_join,
        memo_store=memo_store,
        memory=memory,
        metrics=metrics,
        mobility=mobility,
        model_client_for=model_client_for,
        note_store=note_store,
        observation_store=observation_store,
        persons=persons,
        presence_circles=presence_circles,
        presence_reader=presence_reader,
        proactive_homes=proactive_homes,
        profile_index=profile_index,
        profile_reader=profile_reader,
        reasoning_strip_store=reasoning_strip_store,
        registry=registry,
        reminder_caps=reminder_caps,
        reminders=reminders,
        rosters=rosters,
        route_model=route_model,
        session_guidance=session_guidance,
        shared=shared,
        standing_fold=standing_fold,
        standing_store=standing_store,
        stimulus_store=stimulus_store,
        tag_trace_store=tag_trace_store,
        tier=tier,
        tool_registry=tool_registry,
        unit_rooms=unit_rooms,
        unit_scopes=unit_scopes,
    )
    cognition_runner = _dreamer_enabled.cognition_runner
    cognition_session_units = _dreamer_enabled.cognition_session_units
    cognition_upcoming = _dreamer_enabled.cognition_upcoming
    dream_adaptors = _dreamer_enabled.dream_adaptors
    dream_schedules = _dreamer_enabled.dream_schedules
    dreamer_runtime = _dreamer_enabled.dreamer_runtime
    _dreamer_wake = wake(
        cfg=cfg,
        cognition_runner=cognition_runner,
        cognition_session_units=cognition_session_units,
        cognition_upcoming=cognition_upcoming,
        metrics=metrics,
        stimulus_store=stimulus_store,
    )
    cognition_shutdown = _dreamer_wake.cognition_shutdown
    wake_evaluator = _dreamer_wake.wake_evaluator
    _stores_otp = build_otp(
        audit=audit,
        cfg=cfg,
        doctrine_store=doctrine_store,
        engine=engine,
        metrics=metrics,
        persons=persons,
        registry=registry,
    )
    otp = _stores_otp.otp
    otp_api_token = _stores_otp.otp_api_token
    _computer_sweeper = _computer_wire.sweeper(
        cfg=cfg,
        computer=computer,
        computer_events=computer_events,
        computer_leases=computer_leases,
        computer_sessions=computer_sessions,
        metrics=metrics,
    )
    computer_sweeper = _computer_sweeper.computer_sweeper

    return Dependencies(
        config_reload=reload_files,
        audit=audit,
        memory=memory,
        persons=persons,
        metrics=metrics,
        media_join=media_join,
        policy=policy,
        assembler=assembler,
        ingest=ingest,
        engagement=engagement,
        resolve_voice_room=_voice_room_of(allowlist),
        workspaces_of=functools.partial(workspaces_of_person, allowlist),
        # The portal's Profile reads (45): wired with the switch's reader, so
        # the page shows the accounts exactly when the prompts do.
        profile_portal=profile_store_reader,
        profile_scopes_of=(
            functools.partial(profile_scopes_of_person, allowlist)
            if profile_store_reader is not None
            else None
        ),
        profile_cited=(SqlCharacterEvidence(engine) if profile_store_reader is not None else None),
        index_state=memory_index_reader.index_state,
        documents=documents_store,
        document_service=document_service,
        rooms_of=functools.partial(rooms_named, allowlist),
        room_event_shown=functools.partial(room_event_shown, lineage, event_room_claims),
        room_label=functools.partial(room_label, allowlist),
        blobs=blobs,
        artifacts=computer_exports,
        dispatcher=dispatcher,
        adapter=adapter,
        registry=registry,
        bridge_runtime=bridge_runtime,
        participation_trigger=participation_trigger,
        relevance_gate=gate,
        fire_path=fire_path,
        self_presence=presence_reader,
        clock=clock,
        proactive_screen=proactive_screen,
        account=account,
        doctrine=doctrine,
        doctrine_store=doctrine_store,
        debounce=debounce,
        background=background,
        llm_state=llm_state,
        client=client,
        model_clients=extra_model_clients,
        reminders=reminders,
        games_client=games_client,
        presence=presence_store,
        presence_circles=presence_circles,
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
        computer_sweeper=computer_sweeper,
        computer_sweep_seconds=cfg.computer.sessions.sweep_interval_seconds,
        computer_queue_runner=computer_queue_runner,
        browser_manager=browser_manager,
        browser_sweep_seconds=cfg.computer.scoped.browser_sweep_seconds,
        computer_lifecycle=(
            ComputerLifecycle(
                runner=computer_queue_runner,
                manager=browser_manager,
                sessions=computer_sweeper,
                session_seconds=cfg.computer.sessions.sweep_interval_seconds,
                metrics=metrics,
                sweep_seconds=cfg.computer.scoped.browser_sweep_seconds,
            )
            if computer_queue_runner is not None
            else None
        ),
        computer_probe=computer_probe,
        health_recorder=AdapterHealthRecorder(
            log=SqlSourceHealthLog(engine),
            metrics=metrics,
            timeout_seconds=cfg.cognition.store_timeout_seconds,
        ),
        otp=otp,
        otp_api_token=otp_api_token,
        oauth_keepalive=oauth_keepalive,
        social_poller=social_poller,
        social_perception=social_perception,
        story_expiry=story_expiry,
        graph_batches=(
            GraphBatchProducer(engine, metrics=metrics) if cfg.memory.graph.enabled else None
        ),
        memory_embed=(
            MemoryEmbedWorker(
                engine,
                LlamaEmbedder(cfg.memory.index.embedder_url),
                blobs,
                model=fingerprint(cfg.memory.index.embedder_model),
                model_file=model_file(cfg.memory.index.embedder_model),
                batch=cfg.memory.index.embed_batch,
                images_per_hour=cfg.memory.index.embed_images_per_hour,
                metrics=metrics,
            )
            if cfg.memory.index.embed
            else None
        ),
    )


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
