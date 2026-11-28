"""`ToolRegistry`: the tool-extension axis (DEC-10).

`ToolRegistry.budgets()` is what a `ReplyGuard` is constructed with
(`nora.engagement.stages.deliver.build`, threaded from `nora.app.composition`/
`nora.app.offline`), so a tool's budget comes from wherever it is registered,
not from a table in `guards.py`. `ReplyGuard.evaluate` (`nora/agent/guards.py`)
vetoes any tool absent from that dict under R-7.

It registers whatever `nora.agent.tools` builds, at the budgets that module
declares: `NATIVE_TOOL_BUDGETS` is the single table both this class and
`build_tool_specs` read, so the number `ReplyGuard` enforces and the number a
tool is declared to cost cannot drift apart. A tool becomes callable by
appearing in that one table, with no edit to `guards.py`.

Deliberately NOT here: a `ToolProvider` protocol, MCP tool discovery/lifecycle,
or anything dynamic. `ToolRegistry` is a plain, constructed collaborator (no
module-level singleton, per CLAUDE.md's test-seam policy) built once per
process by the composition root.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from strands.tools.decorator import FunctionToolMetadata
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.catalog.groups import (
    COMPUTER_TOOLS,
    DOCUMENT_TOOLS,
    GRAPH_PASS_TOOLS,
    HOT_TOOLS,
    LOADER_TOOL,
    NATIVE_TOOL_BUDGETS,
    SUBAGENT_TOOLS,
    VIDEO_TOOLS,
    hot_tools,
)
from nora.agent.toolkit.clock import utc_now
from nora.agent.toolkit.ports import (
    DocumentPublisher,
    RecordQuerySource,
    RollupReadSource,
    RoomDirectory,
    SocialItemsSource,
)
from nora.agent.tools import build_tool_specs
from nora.memory import GraphProfileReader

from ..domain.computer import ComputerToolSettings
from ..domain.computer_events import ComputerEventStore
from ..domain.computer_exports import ComputerExportStore
from ..domain.computer_leases import ComputerLeases
from ..domain.computer_sessions import ComputerSessionStore
from ..domain.media_join import MediaJoin
from ..domain.models import TranscriptRow
from ..domain.ports import (
    BlobStore,
    BrowserRunner,
    DiscordPresenceSource,
    ExternalFilesSource,
    GifSource,
    ImageGenerator,
    MediaDescriptionStore,
    MediaObjectStore,
    MusicToolBridge,
    ParkingProvider,
    SandboxRuntime,
    SearchProvider,
    SocialProvider,
    SteamPresenceProvider,
    StreetParkingProvider,
    VideoReadingStore,
    VoicePresenceSource,
    WeatherProvider,
)
from ..infra.parking import ParkingClient, StreetParkingClient
from ..infra.releases import GithubReleases
from ..infra.routes import RouteClient
from ..infra.transit import TransitClient
from ..infra.version import VersionManifest
from ..infra.vision import VisionCache, VisionClient
from ..infra.vision.preprocess import VIDEO_CHUNK_SECONDS, VIDEO_MAX_CHUNKS
from .lineage import LineagePolicy
from .links import LinkCarry
from .mcp import McpToolGroup, stream_mounted
from .ports import ToolSpec, adapt
from .reaction import ReactionPicker
from .relay import Relay
from .room import Room
from .shelf_hook import ShelfLoader
from .subagent_desk import SubagentDesk
from .surface import (
    DREAMER_LOOP_TOOLS,
    TAG_ONLY_TOOLS,
    TAG_READABLE_LOOP_TOOLS,
    ToolScope,
    ToolShelf,
    ToolSurface,
)
from .video_desk import VideoDesk
from .whereis import Circle, PresenceReader

log = logging.getLogger("nora.agent.registry")

#: : Parameters Strands injects itself rather than asks the model to supply; : mirrors
#: `FunctionToolMetadata._is_special_parameter` in : `strands/tools/decorator.py`.
#: `tool_param_names()` below excludes these : from what it calls a tool's ACCEPTED arguments:
#: the model is never asked to : supply them, so a call missing one is not the R-99 defect this
#: table : exists to catch. `"tool_context"` is fixed rather than read per tool: :
#: `nora.agent.ports.adapt` always passes `context=True` (never a renamed : parameter) when a
#: tool's signature declares one at all.
_SPECIAL_TOOL_PARAMS: frozenset[str] = frozenset({"self", "cls", "agent", "tool_context"})


def _opening(description: str) -> str:
    """A tool description's OPENING (ADR 0007's L1: what it does and when to
    call it) as one unwrapped line.

    The opening is the description's FIRST PARAGRAPH, a declared unit rather
    than a heuristic: `tests/test_tool_surface_policy.py::
    test_the_opening_paragraph_is_an_l1` pins every tool's first paragraph to
    carry a when-to-call clause and to fit a catalogue line. The first
    SENTENCE would hold the «what» and drop the «when» (a deferred turn would
    judge whether to call `calculate` from «Work out a number exactly»
    without the «use this for ALL arithmetic» that decides it).

    Unwrapped, because a line break inside the paragraph is a typographic
    accident that would render a catalogue line cut mid-clause at the column.
    """
    unwrapped = " ".join(description.strip().split("\n\n")[0].split())
    return unwrapped.rstrip(".")


class _AggregateBudget:
    """One MCP server's ceiling for one surface: every tool of the server draws on it."""

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self._admitted = 0

    def admit(self) -> bool:
        if self._admitted >= self._budget:
            return False
        self._admitted += 1
        return True


class _AggregateBudgetedTool(AgentTool):
    """A mounted tool whose invocation also draws on its server's shared budget."""

    def __init__(
        self, tool: Any, aggregate: _AggregateBudget, *, mount: str, envelope: Any,
    ) -> None:
        super().__init__()
        self._tool = tool
        self._aggregate = aggregate
        self._mount, self._envelope = mount, envelope

    @property
    def tool_name(self) -> str:
        return str(self._tool.tool_name)

    @property
    def tool_spec(self) -> Any:
        return self._tool.tool_spec

    @property
    def tool_type(self) -> str:
        return str(self._tool.tool_type)

    async def stream(self, tool_use: Any, invocation_state: Any, **kwargs: Any) -> Any:
        if not self._aggregate.admit():
            yield ToolResultEvent({
                "toolUseId": tool_use["toolUseId"], "status": "error",
                "content": [{"text": "This server's budget for this turn is spent; "
                                     "answer from what you already have."}],
            })
            return
        async for event in stream_mounted(
            self._tool, tool_use, invocation_state, mount=self._mount,
            envelope=self._envelope, **kwargs,
        ):
            yield event


def aggregate_budgeted_tools(group: McpToolGroup, envelope: Any = None) -> list[Any]:
    """`group`'s tools, each gated on the one aggregate budget its server shares."""
    aggregate = _AggregateBudget(group.budget)
    return [
        _AggregateBudgetedTool(tool, aggregate, mount=group.name, envelope=envelope)
        for tool in group.tools
    ]


class ToolRegistry:
    """Owns which native tools exist and what each costs (DEC-10).

    It also HOLDS the one long-lived collaborator a tool needs across tags.
    `TransitClient` caches both operators' route catalogues (large, republished
    daily), and a per-tag instance would re-pull that for every bus question.
    The registry is the right owner because it is already the one thing the
    composition root builds once per process (DEC-10), and because putting the
    cache anywhere else would mean a module-level mutable singleton, which
    CLAUDE.md's test-seam policy forbids and
    `tests/architecture/test_no_module_level_mutable_singletons.py` enforces.

    It is DEFAULT-CONSTRUCTED rather than required. `nora.app.eval_support`
    builds throwaway registries purely to read `budgets()` and `tool_names()`,
    neither of which touches a socket, and a required parameter there would
    either force those to invent one or tempt an optional `None` that
    silently makes `transit` answer `unavailable` forever. Constructing one
    costs nothing: no session opens and no catalogue is fetched until a tool
    is actually called.
    """

    def __init__(
        self, *,
        display_name: str = "",
        transit: TransitClient | None = None,
        parking: ParkingProvider | None = None,
        street_parking: StreetParkingProvider | None = None,
        route: RouteClient | None = None,
        vision: VisionClient | None = None,
        describe_models: Mapping[str, str] | None = None,
        describe_providers: Mapping[str, str] | None = None,
        describe_max_bytes: Mapping[str, int] | None = None,
        video_chunk_seconds: int = VIDEO_CHUNK_SECONDS,
        video_max_chunks: int = VIDEO_MAX_CHUNKS,
        media_join: MediaJoin | None = None,
        blobs: BlobStore | None = None,
        descriptions: MediaDescriptionStore | None = None,
        video_readings: VideoReadingStore | None = None,
        search: SearchProvider | None = None,
        # `web_fetch`'s OWN backend ([search] fetch_provider): None means the
        # `search` provider serves both verbs, the pre-split shape.
        fetch: SearchProvider | None = None,
        # `web_fetch`'s page index (fetched_pages): None leaves every call
        # serving straight from the provider's text with no persistence.
        fetched_pages: Any = None,
        social: SocialProvider | None = None,
        linkedin: SocialProvider | None = None,
        weather: WeatherProvider | None = None,
        presence: PresenceReader | None = None,
        circles: Sequence[Circle] = (),
        # DEC-105. `None` is the honest not-configured state every offline and
        # eval composition ships in — `send_to` then answers `unavailable`,
        # never a silent no-op.
        relay: Relay | None = None,
        # The room tools' collaborator: the ONE presence reader, seen through
        # `RoomDirectory`. `None` leaves `room_list`/`room_read` answering
        # `unavailable` (R-5) — the shape offline and eval compositions ship.
        rooms: RoomDirectory | None = None,
        refresher: Any = None,
        geocoder: Any = None,
        # The DERIVED-presence read behind `where_been` (DEC-155): the
        # composition root's ONE `SqlMobilityStore` (same engine, same lifecycle
        # as every other store). NOT default-constructed, for the reason
        # `route`/`presence` are not: there is no storeless default, so every
        # composition that has not built the substrate (offline, eval) leaves
        # `where_been` answering `unavailable`, honest and inert (R-5). The
        # per-call R-31 guard lives in the tool itself, through the same
        # `DisclosureGuard` seam.
        mobility: Any = None,
        ai_peer: str = "nora",
        unit_for: Callable[[str, str], str | None] | None = None,
        # Nora's computer (DEC-243): the runtime is `None` whenever `[computer]
        # enabled` is false, and every computer tool then answers
        # `unavailable`. `computer_events` is the audit store, built
        # unconditionally by the composition root so an `unavailable` call is
        # audited too; `computer_settings` the bounds.
        computer: SandboxRuntime | None = None,
        computer_events: ComputerEventStore | None = None,
        computer_settings: ComputerToolSettings | None = None,
        # Sessions and leases: the session store and the process's ONE
        # `ComputerLeases`, shared by every turn this registry builds tools
        # for, which is what makes an in-process lock a lock.
        computer_sessions: ComputerSessionStore | None = None,
        computer_leases: ComputerLeases | None = None,
        # `computer_export`'s record of what left the computer (always built
        # by the composition root).
        computer_exports: ComputerExportStore | None = None,
        # `web_browse`: None while `[computer.browse]` is off.
        browser: BrowserRunner | None = None,
        # The scoped-computer runtime ([computer.scoped]): `ComputerAdmission`
        # is the per-scope FIFO the write-path tools queue through (None: they
        # run synchronously as before); `browser_manager` owns task-bound tabs
        # and answers `computer_capture`. Both `None` keep the shared-VM shape.
        computer_admission: Any = None,
        browser_manager: Any = None,
        # The canonical-DM scope resolver (`docs/design/people/direct-messages.md`): a DM
        # room's `(person, anchor unit)` scope id — process-lifetime because
        # it is the policy, not the turn. `None` leaves DM computer calls
        # `unavailable` (fail-closed, never a group VM for a DM).
        computer_dm_scope_for: Callable[[str, str], str | None] | None = None,
        # The context block's cache invalidation: a mutating segment calls
        # it with the scope so the next turn re-reads the machine.
        computer_context_invalidate: Callable[[str], None] | None = None,
        # `computer_export(to="link")`'s portal origin (ADR 0023), `[documents]
        # public_url`; `""` answers a link export `unavailable`.
        artifact_link_base: str = "",
        clock: Callable[[], datetime] = utc_now,
        mcp_tools: Mapping[str, McpToolGroup] | None = None, mcp_act_envelope: Any = None,
        versions: VersionManifest | None = None,
        releases: GithubReleases | None = None,
        giphy: GifSource | None = None,
        reaction: ReactionPicker | None = None,
        music: MusicToolBridge | None = None,
        online: SteamPresenceProvider | None = None,
        voice: VoicePresenceSource | None = None,
        discord_presence: DiscordPresenceSource | None = None,
        image_generator: ImageGenerator | None = None,
        image_refs_allowed: bool = False,
        media_store: MediaObjectStore | None = None,
        # `document_publish`/`document_read`: the document service seen through
        # `DocumentPublisher`. `None` withholds both tools.
        documents: DocumentPublisher | None = None,
        budget_overrides: Mapping[str, int] | None = None,
        # `record_query`'s Record read and the process's one `LineagePolicy`.
        # Process-lifetime, like `rooms`: the per-turn part (which room) is the
        # turn's own `conversation_id`. `None` answers `unavailable`.
        record: RecordQuerySource | None = None,
        lineage: LineagePolicy | None = None,
        # `rollup_read`'s Rollup read and `standing_read`'s Standing head: both
        # Dreamer-only tools (`nora.agent.surface.DREAMER_ONLY_TOOLS`), and each
        # withheld from every surface while its read is not wired.
        rollup: RollupReadSource | None = None,
        # Scoped graph profiles for room_list.
        graph_profiles: GraphProfileReader | None = None,
        # `social_read`'s read of the social stream's Record: `None` withholds it.
        social_items: SocialItemsSource | None = None,
        # `[subagent] enabled` (ships off): the desk the four `subagent_*` tools
        # call. Without it the registry withholds them from every turn.
        subagent: SubagentDesk | None = None,
        # `[video_gen]` wired (ships off): the desk `video_create` calls, and the reference
        # kinds its gates allow (`image`, `video`, `audio`). Without the desk the registry
        # withholds the tool from every turn.
        video: VideoDesk | None = None, video_ref_kinds: frozenset[str] = frozenset(),
        # `[external_files]` (ships off): the one read-only source a shared file
        # link is opened through; `None` answers the open `unavailable`. Its
        # `google_account` names who the NOT_SHARED note says to share with.
        external_files: ExternalFilesSource | None = None, google_account: str = "",
        graph_retrieval: Any = None,
        engagement_graph_retrieval: Any = None,
        intention_admission: Any = None,
        # The graph's dispute and forget verbs `profile_dispute` writes through;
        # without them the registry withholds the tool from every turn.
        memory_intake: Any = None,
        # Typed observation reads; None withholds them, not the message Record read.
        observations: Any = None,
        # Shared management invocation; specs supplies the verified per-turn message.
        room_management: Any = None,
        content_checks: bool = True,
    ) -> None:
        self._display_name = display_name
        self._content_checks = content_checks
        self._observations = observations
        self._room_management = room_management
        self._budget_overrides = dict(budget_overrides or {})
        self._external_files, self._google_account = external_files, google_account
        self._graph_retrieval = graph_retrieval
        self._engagement_graph_retrieval = engagement_graph_retrieval
        self._intention_admission = intention_admission
        self._memory_intake = memory_intake
        self._record = record
        self._documents = documents
        self._lineage = lineage
        self._rollup = rollup
        self._graph_profiles = graph_profiles
        self._social_items = social_items
        # Tools with missing dependencies are omitted from every surface
        # and from the gate's digest, so a turn cannot route to them.
        self._withheld: frozenset[str] = (
            (frozenset() if record is not None else frozenset({"record_query"}))
            | (frozenset() if rollup is not None else frozenset({"rollup_read"}))
            | (frozenset() if social_items is not None else frozenset({"social_read"}))
            | (frozenset() if observations is not None
               else frozenset({"poll_list", "poll_read", "room_state_read"}))
            | (frozenset() if computer is not None else COMPUTER_TOOLS)
            | (frozenset() if browser is not None and computer_admission is not None
               and browser_manager is not None else frozenset({"web_browse"}))
            | (frozenset() if browser_manager is not None else frozenset({"computer_capture"}))
            | (frozenset() if computer_admission is not None else frozenset({"computer_cancel"}))
            | (frozenset() if documents is not None else DOCUMENT_TOOLS)
            | (frozenset() if subagent is not None else SUBAGENT_TOOLS)
            | (frozenset() if video is not None else VIDEO_TOOLS)
            | ({"profile_dispute"} if memory_intake is None else set())
            | ({"memory_search", "memory_read"} if graph_retrieval is None else set())
            | GRAPH_PASS_TOOLS
        )
        self._subagent = subagent
        self._video = video
        self._video_ref_kinds = video_ref_kinds
        self._transit = transit if transit is not None else TransitClient()
        # `parking` is keyless like `transit` (the TD open-data feed needs no
        # key), so the same default-construct argument applies: a required
        # parameter would force offline/eval compositions to invent one, and
        # constructing a `ParkingClient` costs nothing (no session, no index
        # fetch until a tool is called).
        self._parking = parking if parking is not None else ParkingClient()
        # `street_parking` is the same keyless TD feed family (the metered
        # on-street spaces) and default-constructs for the same reasons as
        # `parking`: no key, no cost until a tool calls it.
        self._street_parking = (
            street_parking if street_parking is not None else StreetParkingClient()
        )
        # `route` is NOT default-constructed: unlike `TransitClient`, a
        # `RouteClient` needs a Google API key, and there is no keyless default
        # to fall back to. When it is None (every composition that has not
        # wired a key, including `nora.app.offline` and the eval registries)
        # the `route` tool answers `unavailable`, the honest state for
        # "routing is not configured" (R-5). The composition root passes one
        # built from the resolved `[route]` key (DEC-8); until then, `route` is
        # present in the catalogue and refuses to leak, but cannot travel.
        self._route = route
        # `vision`/`blobs` are the `media_fetch` collaborators (DEC-56), wired
        # together and only when custody is on (`[media] enabled` + key -> a blob
        # store) AND a reachable OmniRoute client exists. Either being None
        # leaves `media_fetch` answering `unavailable`, the same inert-by-config
        # shape as keyless `route`, so it is present-but-inert while
        # `[media] enabled=false`.
        self._vision = vision
        # `describe_models` rides alongside `vision`, the same VALUE-not-object
        # split `ai_peer` makes on this class: `nora.agent` may not import
        # `nora.config` (CLAUDE.md), so the composition root resolves
        # `cfg.media.describer_for(...)` and hands plain strings in here, as it
        # resolves the `VisionClient` itself.
        #
        # A modality is PRESENT here only when it is enabled AND names a model,
        # so absence is the single meaning of «no describer» and the tool needs
        # no second flag to read.
        self._describe_models = dict(describe_models or {})
        self._describe_providers = dict(describe_providers or {})
        # Per-modality byte ceilings for the describer, present only when the
        # modality names one (0/None = inherit the VisionClient's shared
        # ceiling). Same VALUE-not-object split as the two maps above.
        self._describe_max_bytes = dict(describe_max_bytes or {})
        # `[media.describe.video]`'s chunk geometry. Held here for the same
        # reason the per-modality maps are: `media_fetch` is built per turn and
        # must read a clip the same way the ingest-side comprehension pass
        # does, or the deliberate look and the automatic one disagree about how
        # much of it was seen.
        self._video_chunk_seconds = video_chunk_seconds
        self._video_max_chunks = video_max_chunks
        # The SHARED media join: `load_transcript` is a transcript builder
        # inside a tool, and it must render a photo the same way the prefill
        # does or one prompt carries two answers about it.
        self._media_join = media_join
        self._blobs = blobs
        # The description cache lives HERE, once per process, for the reason the
        # transit catalogue does: keyed by content hash, a per-tag cache would
        # never hit, and a module-level singleton is what the test-seam policy
        # forbids. Held even when `media_fetch` is inert: it costs an empty
        # dict and means turning custody on wires nothing new here.
        #
        # `store=descriptions` makes it a read-through onto the durable table
        # rather than a memory-only cache; `None` (every composition that has
        # not wired one) leaves it memory-only.
        self._vision_cache = VisionCache(store=descriptions)
        # `media_fetch`'s VIDEO reading cache; None leaves every video read
        # uncached.
        self._video_readings = video_readings
        # `search` (Tavily, DEC-73/DEC-116) is NOT default-constructed, for the
        # same reason `route` is not: `web_search`/`web_fetch` ship INERT behind
        # `[search] enabled` AND a key, so every composition that has not wired
        # one (offline, eval, keyless) leaves this None and both answer
        # `unavailable` (the honest «not configured» state, R-5). No SSRF gate
        # is needed for either verb: Tavily fetches from its own servers, never
        # Nora's, whether it is handed a query or a url.
        self._search = search
        # `fetch` is `web_fetch`'s own backend when `[search] fetch_provider`
        # splits it (None: `search` serves both verbs); `fetched_pages` is the
        # page index its `offset`/`about` reads answer from (None: no
        # persistence, the pre-split shape).
        self._fetch = fetch
        self._fetched_pages = fetched_pages
        # `social` (OpenCLI bridge, DEC-151) is NOT default-constructed either:
        # `x_read`/`x_search` ship INERT behind `[social] enabled` (keyless; the
        # bridge is loopback-only and holds the read-only allowlist host-side),
        # so every composition that has not wired one leaves this None and the
        # tool answers `unavailable` (the honest «not configured» state, R-5).
        self._social = social
        # `linkedin` is the same router, handed over only when `[social.linkedin]
        # enabled`: the router's one default backend also serves X, so
        # LinkedIn's switch cannot live in it. `None` leaves both LinkedIn tools
        # answering «not set up».
        self._linkedin = linkedin
        # `weather` (HKO, DEC-72) is NOT default-constructed either, for symmetry
        # with `route`/`search`: it ships behind `[weather] enabled`, and every
        # composition that has not wired one leaves this None and `weather`
        # answers `unavailable`. Keyless, so the composition root wires one on
        # `enabled` alone.
        self._weather = weather
        # `presence`/`circles` are `whereis`'s collaborators (DEC-59): the local
        # `presence_fixes` reader and the circle rosters derived from
        # identity.toml (`circles_from_persons`). NOT default-constructed, for
        # the reason `route` is not: there is no readerless default, so every
        # composition that has not wired them (offline, eval, `[presence]`
        # disabled, an identity with no circle) leaves `whereis` answering
        # `unavailable`, honest and inert (R-5). The per-call R-31 subset guard
        # lives in `nora.agent.whereis`, not here.
        self._presence = presence
        self._circles = circles
        self._relay = relay
        self._rooms = rooms
        # The LIVE-refresh client (DEC-67): None leaves `where_is` reading the
        # last stored fix (offline/eval, or [presence] refresh_url unset).
        self._refresher = refresher
        # Passed to `Whereis`, which accepts it but does not call it: `locate`
        # answers without an address.
        self._geocoder = geocoder
        # The derived-presence read behind `where_been` (DEC-155): the same
        # optional-collaborator shape as `presence` above. None in the
        # offline/eval compositions, where the tool answers `unavailable`
        # rather than a guessed empty (R-5).
        self._mobility = mobility
        # Nora's configured author id for tool calls that act as her.
        self._ai_peer = ai_peer
        self._unit_for = unit_for
        self._computer = computer
        self._computer_admission = computer_admission
        self._browser_manager = browser_manager
        self._computer_dm_scope_for = computer_dm_scope_for
        self._computer_context_invalidate = computer_context_invalidate
        self._computer_events = computer_events
        self._computer_settings = computer_settings
        self._computer_sessions = computer_sessions
        self._computer_leases = computer_leases
        self._computer_exports = computer_exports
        self._browser = browser
        self._artifact_link_base = artifact_link_base
        self._clock = clock
        # Budget, specification and execution all use the caller's same MCP allowlist.
        self._mcp_tools = dict(mcp_tools) if mcp_tools is not None else {}
        self._mcp_act_envelope = mcp_act_envelope
        # One immutable snapshot for running identity and shipped release history.
        self._versions = versions if versions is not None else VersionManifest()
        self._releases = releases
        self._giphy = giphy
        # `nora.agent.reaction`: None keeps every reply exactly as written.
        self._reaction = reaction
        # `MusicToolBridge`, wired the identical honest way `events`/`reminders`
        # are: NOT default-constructed (there is no bridgeless default), and
        # every composition that has not wired `DiscordMusicToolBridge`
        # (offline, eval, `[music] enabled = false`, or Discord itself disabled)
        # leaves this None and all four music tools answer `unavailable` (R-5).
        # The Discord-only gate is a SEPARATE check each tool makes on
        # `room.platform` before ever touching this collaborator.
        self._music = music
        # `online_now`'s live-state reads, wired the identical honest way
        # `mobility`/`music` are: NOT default-constructed (there is no
        # clientless default), so every composition that has not wired them
        # (offline, eval, `[online]` disabled or keyless, Discord disabled)
        # leaves them None and the matching arms answer `unavailable` (R-5),
        # never `nothing_found`. `voice` and `discord_presence` are independent
        # of `[online]`: they read the live Discord client and wire whenever the
        # Discord platform is enabled. `voice` is the narrow "who is in which
        # voice channel" sub-arm; `discord_presence` is the `online` arm's
        # Discord half: per-person presence (status + rich presence).
        self._online = online
        self._voice = voice
        self._discord_presence = discord_presence
        # `ImageGenerator` (DEC-137), wired the same honest way: NOT
        # default-constructed. `None` (config `[image_gen] enabled = false`)
        # leaves the `generate_image` tool present in the catalogue answering
        # `unavailable`: the wiring-gap state, never `nothing_found`.
        self._image_generator = image_generator
        self._image_refs_allowed = image_refs_allowed
        self._media_store = media_store
        # `_descriptions()`'s memo — process-lifetime for the reason the
        # transit catalogue is, and empty until the first catalogue renders.
        self._described: dict[str, str] | None = None
        # `tool_param_names()`'s memo, the same shape and the same reason
        # (R-99): a tool's signature is fixed by the code that shipped, so
        # re-introspecting it every tag would be pure waste.
        self._param_names: dict[str, frozenset[str]] | None = None

    def budgets(self, *, allowed_mcp: frozenset[str] = frozenset()) -> Mapping[str, int]:
        """The per-tool call-count ceiling `ReplyGuard` enforces under R-7.

        Includes an MCP tool's budget ONLY when its server name is in
        `allowed_mcp`, the SAME set the caller passes (or will pass) to
        `.build()`. `ReplyGuard.evaluate` renders the WHOLE dict's keys into
        the model-facing R-7 unknown-tool veto message
        (`guards.py::_list_tool_names`), so an unconditional fold would tell a
        conversation with no `demo` allowlist entry that
        `demo_server_status` exists the moment the model named any wrong
        tool, and R-7's default-deny would not cover those names for an
        unauthorized conversation. The default (`frozenset()`) is the safe
        one: a caller with no conversation context (`nora.app.composition`'s
        throwaway registries) gets exactly the native table, nothing more."""
        budgets = dict(NATIVE_TOOL_BUDGETS)
        # `[llm.budgets]` from config.toml, applied OVER the table rather than
        # instead of it, so the file may carry one line without restating the
        # rest.
        #
        # This keeps DEC-10/DEC-32's ONE-NUMBER rule, and that is why it is
        # applied HERE. The table states the default and the reasoning next to
        # the tool; config states a per-deployment DELTA; and `budgets()` is the
        # single place anything reads a budget from (`ReplyGuard`'s R-7 ceiling,
        # the `Deliverer`'s message cap and the tests all resolve to one value
        # at runtime). Putting one budget alone in config would split the
        # table between code and config, which those decisions exist to
        # prevent.
        #
        # Unknown names are REJECTED at config load (`nora.config`), not
        # silently dropped here: a typo'd tool name in `[llm.budgets]` is an
        # operator who thinks they raised a cap and did not.
        budgets.update(self._budget_overrides)
        for name in allowed_mcp:
            group = self._mcp_tools.get(name)
            if group is None:
                continue
            for tool in group.tools:
                budgets[tool.tool_name] = group.budget
        return budgets

    def terminal_tools(self) -> frozenset[str]:
        """The verbs that END A TURN, for `ReplyGuard`'s model-call hook (DEC-32).

        Exposed exactly the way `budgets()` is, and for the same reason. Under
        `tool_choice="required"` the model must call something on every pass, so
        the guard's `BeforeModelCallEvent` cancellation is the ONLY thing that
        ends a turn (DEC-21). Terminality is declared on the tool, not in the
        guard and not as a single tool's side effect, so any verb that ends a
        turn (`send_gif` included) is one `terminal=True` in `NATIVE_TOOLS`.

        A `frozenset` rather than a mapping: membership is the whole question,
        and there is no per-tool value to carry.
        """
        return frozenset(name for name, spec in NATIVE_TOOLS.items() if spec.terminal)

    def cacheable_tools(self) -> frozenset[str]:
        """The tools whose successful result may be logged to `tool_call_log`
        and later replayed by `recall_lookups`.

        A `frozenset`, the same shape `terminal_tools()` returns and for the
        same reason: membership is the whole question, and there is no
        per-tool value to carry.
        """
        return frozenset(name for name, spec in NATIVE_TOOLS.items() if spec.cacheable)

    def lookup_tools(self) -> frozenset[str]:
        """The tools that LOOK and report whether they found anything: R-97's
        set, and the one thing "did this turn come back with something" can be
        asked of.

        Two declared fields ANDed, derived here and hand-listed nowhere: a tool
        is a lookup when it only reads (`read_only`) **and** its result carries
        R-27's status axis (`output_contract == "three_state"`). Both halves are
        load-bearing.

        A write such as `intention_commit` also declares `three_state`.
        Counting its failure as an empty read would silence an otherwise
        useful reply.

        Without `output_contract` it would swallow `load_transcript`, the one
        read on the table that deliberately carries NO status (its empty list
        IS the answer, ADR 0009 §2). Reading an unparseable result as "found
        nothing" is the `unavailable`-as-`nothing_found` substitution R-27
        exists to prevent, and it would make "she read older messages and then
        spoke" indistinguishable from "she went looking and came back empty".

        A `frozenset`, the same shape the three above return and for the same
        reason: membership is the whole question.
        """
        return frozenset(
            name for name, spec in NATIVE_TOOLS.items()
            if spec.read_only and spec.output_contract == "three_state"
            # The one named exception. `load_tools` is read-only and carries a
            # status, so the derivation admits it, but what it reports is
            # whether a SCHEMA loaded, and counting that as the turn having
            # found something would let a successful load silence a reply
            # after a lookup that came back empty.
            and name != LOADER_TOOL
        )

    def with_clock(self, clock: Callable[[], datetime]) -> ToolRegistry:
        """This registry with the one injected clock swapped, for an occasion
        whose reads must see a fixed instant rather than the process's wall
        clock (the historical lane's evidence time)."""
        from copy import copy

        scoped = copy(self)
        scoped._clock = clock
        return scoped

    def withheld_tools(self) -> frozenset[str]:
        """The tools no occasion in this process is offered: a read with no
        store wired, and the memory surface not selected (DEC-316). The ONE
        answer to «is X offered here», so a consumer that keeps its own
        catalogue (Cognition's `read_surface`) reads it rather than
        re-deriving it from config."""
        return self._withheld

    def read_only_tools(self) -> frozenset[str]:
        """The tools that only LOOK: the Dreamer's ceiling.

        A background research loop **may look and may not act**: a process that
        can speak to the group unprompted, set a reminder, or write to the
        events store with nobody waiting on it is categorically more dangerous
        than one that can only read. That is a property of the runtime, not a
        setting, so it is DERIVED here and hand-listed nowhere.

        The session supplies its narrower set by tool name. Its tool source
        only builds names that also appear in this read-only set.

        A `frozenset`, the same shape `terminal_tools()` and `cacheable_tools()`
        return and for the same reason: membership is the whole question.
        """
        return frozenset(name for name, spec in NATIVE_TOOLS.items() if spec.read_only)

    def tool_names(self) -> tuple[str, ...]:
        """Every registered tool's name, for the doctrine loader's catalogue gate.

        `nora.doctrine` sits BELOW `nora.agent` in the layer contract and cannot
        import the tool table, so the composition root hands it these instead
        (`nora.doctrine.loader.check_kind_boundaries`).

        Plugin tools (`spec.plugin`, DEC-50) are EXCLUDED: `skills` is not a tool
        this registry builds, and the catalogue gate exists to stop an authored
        `identity`/`conduct` file naming a tool it does not have. `skills` is
        not built or described here (see `specs()`), so it does not belong in
        the gate's list either."""
        return tuple(name for name, spec in NATIVE_TOOLS.items() if not spec.plugin)

    def capability_digest(self) -> str:
        """The read-only tool surface as one line each, for a reader that has
        to judge what Nora can answer WITHOUT being able to call anything.

        That reader is the Relevance Gate, which decides «is there something
        in this room she can actually serve?», a question about her
        capabilities. DEC-150 deleted `kind: capability` because tools are the
        source of truth and an authored copy is a cache that goes stale
        silently; this is derived for the same reason.

        DERIVED, so it cannot go stale: a tool added to `NATIVE_TOOLS` appears
        here on the next start with nothing to edit.

        READ-ONLY tools only (`read_only_tools`). The gate is asking whether an
        ANSWER exists, and a verb that acts (`send_reply`, `intention_commit`,
        `play_music`) is never the reason a window is worth a turn.

        FIRST SENTENCE only. The full descriptions are written for a model that
        is about to CALL the tool and carry long when-to-use guidance; a reader
        deciding whether an answer could exist needs the verb and its subject,
        not its usage rules.
        """
        return "\n".join(self._digest_lines(
            self.read_only_tools() - {LOADER_TOOL} - TAG_ONLY_TOOLS
            - self._withheld - (DREAMER_LOOP_TOOLS - TAG_READABLE_LOOP_TOOLS),
        ))

    def _digest_lines(
        self, names: Iterable[str], *, hot: Container[str] = frozenset()
    ) -> list[str]:
        """`- name — first sentence`, one line per name, sorted.

        Extracted so `capability_digest` and `tool_catalogue` cannot drift into
        two renderings of the same thing. They differ only in WHICH names and
        whether a name is marked resident; the line itself has one
        implementation.
        """
        described = self._descriptions()
        lines = []
        for name in sorted(names):
            mark = "*" if name in hot else "-"
            first = _opening(described.get(name, ""))
            lines.append(f"{mark} {name} — {first}" if first else f"{mark} {name}")
        return lines

    def tool_catalogue(self, *, hot: Container[str] = HOT_TOOLS) -> str:
        """EVERY tool, one line each: the index the model selects from.

        The difference from `capability_digest` is the reader and therefore the
        scope. The Relevance Gate asks «could an answer exist here?», so it
        gets READ-ONLY tools. This is read by the model that is about to
        CHOOSE, so it gets all of them; leaving `send_reply` or `intention_commit` out
        would leave out a thing she can do.

        A line answers ONE question: is this the tool I want, or not? It
        carries the capability and, where a confusable sibling exists, the
        discriminator against it (`where_is` says RIGHT NOW, `where_been` says
        HAS BEEN). It carries no parameters, no arms, no `status` values and no
        reporting rules; those are answered by the schema and by the result.

        DERIVED, like `capability_digest` and for the same reason: a tool added
        to `NATIVE_TOOLS` appears here on the next start with nothing to edit.

        FIRST SENTENCE, not a separate authored field: the first sentences
        already discriminate between confusable siblings
        (`find_parking`/`parking_status`, `note_read`/`note_search`), so a repair
        belongs in the docstring, one source of truth per tool.

        `hot` marks the tools whose full schema is already resident, so the
        model does not spend a round trip fetching what it already holds. It
        defaults to the one declaration in `nora.agent.tools`; the parameter
        exists so a test can render a catalogue without depending on the
        current set.
        """
        names = set(self.tool_names()) - {LOADER_TOOL}
        return "\n".join(self._digest_lines(names, hot=hot))

    def _descriptions(self) -> Mapping[str, str]:
        """Each tool's model-facing description, introspected once per process.

        Docstrings are fixed by the code that shipped, so this cannot go stale
        within a process, and `ToolSurface.catalogue` renders per turn, where
        re-introspecting every closure every tag would be pure waste.
        """
        if self._described is None:
            self._described = {
                entry["name"]: str(entry.get("description") or "")
                for entry in self.catalog()
            }
        return self._described

    def catalog(self) -> tuple[dict, ...]:
        """Every built tool's public metadata (name, model-facing
        description, and declared registry fields) for the web portal's
        `/app/v1/tools` endpoint.

        This is the LIVE surface: the descriptions come from the same
        Strands introspection the agent loop derives (`FunctionToolMetadata.
        extract_metadata`, Args-stripped), and the declared fields are read
        straight off `NATIVE_TOOLS`, so what the portal shows IS what the
        deployed agent can call.

        This method lives HERE and not in `nora.web` because the strands import
        boundary (`tests/test_boundaries.py::test_strands_import_boundary`)
        allows `strands` only under `nora/agent/`. The registry already owns
        the tool table, so it is the natural single reader.

        Plugin tools are excluded by construction (`build_tool_specs` does
        not build them). A spec whose `call` cannot be introspected keeps its
        declared fields with `description` dropped: one bad tool must never
        sink the endpoint (R-27), and the failure is logged server-side.
        """
        catalog = []
        for spec in self._introspection_specs():
            declared = NATIVE_TOOLS[spec.name]
            entry: dict = {
                "name": spec.name,
                "budget": declared.budget,
                "terminal": declared.terminal,
                "time_shiftable": declared.time_shiftable,
                "source_tier": declared.source_tier,
                "cacheable": declared.cacheable,
                "read_only": declared.read_only,
                "side_effect_state": declared.side_effect_state,
                "idempotency": declared.idempotency,
                "retry": declared.retry,
                "output_contract": declared.output_contract,
            }
            try:
                metadata = FunctionToolMetadata(spec.call).extract_metadata()
                description = metadata.get("description")
                if description:
                    entry["description"] = description
            except Exception:
                log.exception("could not introspect tool %s for the catalog", spec.name)
            catalog.append(entry)
        return tuple(sorted(catalog, key=lambda entry: entry["name"]))

    def _introspection_specs(self) -> tuple[ToolSpec, ...]:
        """Every native tool, built ONLY to introspect its callable, never invoked.

        Shared by `catalog()` and `tool_param_names()` (R-99) so both read the
        identical built specs rather than two copies of the all-but-one-argument
        -None `build_tool_specs()` call. `_catalog_room()`'s docstring says why
        the room stand-in is enough for both callers: no tool is CALLED here,
        only introspected.
        """
        return build_tool_specs(
            display_name=self._display_name,
            store=None,
            conversation_id="catalog",
            conversation_type="group",
            persons=None,
            deliverer=None,
            metrics=None,
            room=self._catalog_room(),
            # The catalog is the DECLARED surface: «what can this deployment
            # call», not «what does one room see». `_catalog_room()` says
            # platform="discord" so the Discord-only music verbs appear; this is
            # the same exception for the Dreamer-only memory verb. Both gates
            # live in `build_tool_specs`, and an endpoint that dropped either
            # would under-report what the agent can do.
            dream_adaptor="catalog",
            # Both memory surfaces, for the same reason (DEC-316): the flag
            # picks one per deployment, and the catalog and R-99's argument
            # table describe what the code declares, not one config.
            all_memory_surfaces=True,
            question="",
            context_window=40,
            transit_source=self._transit,
            # `generate_image` states the wired provider's reference limit in
            # its description (DEC-307); without the generator the portal
            # would show a description no deployed turn reads.
            image_generator=self._image_generator,
            content_checks=self._content_checks,
        )

    @property
    def content_checks(self) -> bool:
        return self._content_checks

    @property
    def video_chunk_seconds(self) -> int:
        """One video reading window, as configured (`[media.describe.video]
        chunk_seconds`): the longest clip an automatic reading reads, the
        cutoff `instagram_read`'s items use inside `build_tool_specs`, handed
        to a reader outside it (the public summons) so both cut at the same
        place."""
        return self._video_chunk_seconds

    def tool_param_names(self) -> Mapping[str, frozenset[str]]:
        """Each tool's ACCEPTED argument names, read off its own signature,
        never hand-copied, for `ReplyGuard`'s R-99 unknown-argument veto.

        `strands.tools.decorator.FunctionToolMetadata._create_input_model`
        builds its validating pydantic model from exactly these names: one
        field per signature parameter, with no `model_config` set, so pydantic
        v2's default (`extra="ignore"`) makes `validate_input` SILENTLY DROP
        any key a call carries that is not one of them. Reading the same
        signature here, the same way, is what lets `ReplyGuard` veto that call
        BEFORE Strands' validation runs (`BeforeToolCallEvent` fires on the raw
        `tool_use`, ahead of it).

        A tool this registry does not BUILD (a `plugin=True` entry such as
        `skills`, or an MCP tool folded in only through
        `budgets(allowed_mcp=...)`) has no entry here at all, deliberately:
        this table is read from `build_tool_specs()`'s own output, which never
        constructs either kind, so there is no signature to derive one from.
        `evaluate` treats a missing entry as "cannot verify", never as "reject
        everything", the same fail-closed-on-the-CHECK-itself direction
        `catalog()`'s per-spec `except Exception` takes.

        Memoized like `_descriptions()`: fixed by the code that shipped, so
        re-introspecting every closure every tag would be pure waste.
        """
        if self._param_names is None:
            names: dict[str, frozenset[str]] = {}
            for spec in self._introspection_specs():
                try:
                    params = inspect.signature(spec.call).parameters
                except (TypeError, ValueError):
                    # Mirrors `catalog()`'s own `except Exception` around the
                    # identical introspection call: one tool whose callable
                    # cannot be read must not sink this table for every other
                    # tool (R-27's fail-closed direction applied to the CHECK).
                    log.exception(
                        "could not introspect tool %s for tool_param_names", spec.name)
                    continue
                names[spec.name] = frozenset(
                    name for name in params if name not in _SPECIAL_TOOL_PARAMS
                )
            self._param_names = names
        return self._param_names

    @staticmethod
    def _catalog_room() -> Any:
        """The minimal `Room` stand-in `build_tool_specs` reads while building.

        Only the attributes the builder touches while constructing the tool
        list matter (`platform`/`conversation_id` for the conversation-scoped
        closures); every collaborator is `None` because no tool is CALLED
        here (a call would be the only thing touching a socket or a roster).
        A real `Room` is constructed per tag with a roster source; the
        catalog needs none of it.
        """
        return SimpleNamespace(
            # platform="discord": the catalog is the REGISTRY FULL TABLE, the
            # operator-facing surface of what the deployed agent can call, not
            # one room's per-turn catalog. Music verbs are filtered at
            # catalog-BUILD time for non-Discord rooms, so a "signal" stand-in
            # would hide them from the portal. Per-turn catalogs still filter
            # correctly for their own room.
            platform="discord", conversation_id="catalog", tagger=None,
        )

    def specs(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
        transcript: tuple[TranscriptRow, ...] | None = None,
        lookup_window: tuple[TranscriptRow, ...] | None = None,
        fire_turn: bool = False,
        relay_turn: bool = False,
        location_trusted: bool = False,
        links: LinkCarry | None = None,
        supplied_unit: str | None = None,
        dream_adaptor: str = "",
        subagent_job: str = "",
        checkpoint_sink: Any = None,
        grant: frozenset[str] | None = None,
        shelf: ToolShelf | None = None,
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
        message: Any = None,
        engagement: bool = False,
        commit_intentions: bool | None = None,
    ) -> tuple[ToolSpec, ...]:
        """Build room-bound tool specs before Strands adaptation."""
        return build_tool_specs(
            display_name=self._display_name,
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, deliverer=deliverer, metrics=metrics,
            room=room, transit_source=self._transit, route_source=self._route,
            vision_source=self._vision, blob_store=self._blobs,
            vision_cache=self._vision_cache, video_readings=self._video_readings,
            describe_models=self._describe_models,
            describe_providers=self._describe_providers,
            describe_max_bytes=self._describe_max_bytes,
            video_chunk_seconds=self._video_chunk_seconds,
            video_max_chunks=self._video_max_chunks,
            media_join=self._media_join,
            search_source=self._search, weather_source=self._weather,
            fetch_source=self._fetch, fetched_pages=self._fetched_pages,
            parking_source=self._parking,
            street_parking_source=self._street_parking,
            social_source=self._social,
            linkedin_source=self._linkedin,
            presence_source=self._presence, presence_circles=self._circles,
            location_trusted=location_trusted,
            mobility=self._mobility,
            refresher=self._refresher,
            geocoder=self._geocoder,
            question=question, context_window=context_window,
            transcript=transcript, lookup_window=lookup_window,
            ai_peer=self._ai_peer, fire_turn=fire_turn, relay_turn=relay_turn,
            dream_adaptor=dream_adaptor,
            subagent_job=subagent_job, checkpoint_sink=checkpoint_sink,
            grant=grant,
            clock=self._clock,
            unit_for=self._unit_for,
            supplied_unit=supplied_unit,
            links=links,
            relay=self._relay,
            rooms=self._rooms,
            record=self._record,
            observations=self._observations,
            graph_retrieval=(self._engagement_graph_retrieval if engagement
                             else self._graph_retrieval),
            intention_admission=(
                self._intention_admission
                if (engagement if commit_intentions is None else commit_intentions) else None
            ),
            memory_intake=self._memory_intake,
            lineage=self._lineage,
            rollup=self._rollup,
            graph_profiles=self._graph_profiles,
            social_items=self._social_items,
            versions=self._versions, releases=self._releases,
            giphy_source=self._giphy,
            reaction_picker=self._reaction,
            computer=self._computer, computer_events=self._computer_events,
            computer_settings=self._computer_settings,
            computer_sessions=self._computer_sessions,
            computer_leases=self._computer_leases,
            computer_exports=self._computer_exports,
            browser=self._browser,
            computer_admission=self._computer_admission,
            browser_manager=self._browser_manager,
            computer_dm_scope_for=self._computer_dm_scope_for,
            computer_context_invalidate=self._computer_context_invalidate,
            artifact_link_base=self._artifact_link_base,
            music=self._music,
            online_source=self._online, voice_source=self._voice,
            presence=self._discord_presence,
            image_generator=self._image_generator,
            image_refs_allowed=self._image_refs_allowed,
            media_store=self._media_store,
            documents=self._documents,
            subagent=self._subagent,
            video=self._video, video_ref_kinds=self._video_ref_kinds,
            external_files=self._external_files, google_account=self._google_account,
            shelf=shelf,
            on_rows=on_rows,
            room_management=self._room_management,
            message=message,
            content_checks=self._content_checks,
        )

    def build(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
        transcript: tuple[TranscriptRow, ...] | None = None,
        lookup_window: tuple[TranscriptRow, ...] | None = None,
        fire_turn: bool = False,
        allowed_mcp: frozenset[str] = frozenset(),
        location_trusted: bool = False,
        links: LinkCarry | None = None,
        supplied_unit: str | None = None,
        dream_adaptor: str = "",
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
        message: Any = None,
    ) -> list[Any]:
        """`surface(...).tools`, for a caller that wants only the list.

        Every production loop calls `surface()` itself, the one builder (see
        its docstring). This stays for the callers that ask «what tools would
        this turn get» and nothing else: tests, and anything reading the
        adapted objects without placing a catalogue. It takes no `grant` and
        never defers, so it is the resident-everything build."""
        return self.surface(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
            transcript=transcript, lookup_window=lookup_window,
            fire_turn=fire_turn, allowed_mcp=allowed_mcp,
            location_trusted=location_trusted, links=links,
            supplied_unit=supplied_unit, dream_adaptor=dream_adaptor,
            on_rows=on_rows, message=message,
        ).tools

    def surface(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
        transcript: tuple[TranscriptRow, ...] | None = None,
        lookup_window: tuple[TranscriptRow, ...] | None = None,
        fire_turn: bool = False,
        relay_turn: bool = False,
        allowed_mcp: frozenset[str] = frozenset(),
        location_trusted: bool = False,
        links: LinkCarry | None = None,
        supplied_unit: str | None = None,
        dream_adaptor: str = "",
        subagent_job: str = "",
        checkpoint_sink: Any = None,
        grant: frozenset[str] | None = None,
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
        defer: bool = False,
        occasion: str | None = None,
        message: Any = None,
        engagement: bool = False,
        commit_intentions: bool | None = None,
    ) -> ToolSurface:
        """Build the resident tools, deferred shelf and catalogue from one admitted set."""
        if commit_intentions is None:
            commit_intentions = engagement
        scope = ToolScope(
            platform=None if room is None else room.platform,
            dreamer=bool(dream_adaptor),
            grant=grant,
            tag_turn=not (fire_turn or dream_adaptor or subagent_job),
            relay=relay_turn,
            worker=bool(subagent_job),
            engagement=engagement,
        )
        shelf = ToolShelf()
        specs = self.specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
            transcript=transcript, lookup_window=lookup_window,
            fire_turn=fire_turn, relay_turn=relay_turn,
            location_trusted=location_trusted, links=links,
            supplied_unit=supplied_unit, dream_adaptor=dream_adaptor,
            subagent_job=subagent_job, checkpoint_sink=checkpoint_sink,
            grant=grant, shelf=shelf, on_rows=on_rows, message=message, engagement=engagement,
            commit_intentions=commit_intentions,
        )
        withheld = self._withheld
        if engagement and self._engagement_graph_retrieval is not None:
            withheld = withheld - {"memory_search", "memory_read"}
        if commit_intentions and self._intention_admission is not None:
            withheld = withheld - {"intention_commit"}
        loader = next(spec for spec in specs if spec.name == LOADER_TOOL)
        admitted = [
            spec for spec in specs
            if spec.name != LOADER_TOOL and scope.admits(spec.name)
            and spec.name not in withheld
        ]
        hot = hot_tools(occasion)
        resident = [spec for spec in admitted if not defer or spec.name in hot]
        shelf.stock({
            spec.name: adapt(spec) for spec in admitted
            if defer and spec.name not in hot
        })
        tools = [adapt(spec) for spec in resident]
        if shelf:
            tools.append(adapt(loader))
        for name in allowed_mcp:
            group = self._mcp_tools.get(name)
            if group is not None:
                tools.extend(aggregate_budgeted_tools(group, self._mcp_act_envelope))
        return ToolSurface(
            scope=scope,
            tools=tools,
            resident=(spec.name for spec in resident),
            shelf=shelf,
            admitted=(spec.name for spec in admitted),
            render=lambda names, hot: self._digest_lines(names, hot=hot),
            # A shelved tool called by name loads on the spot (`ShelfLoader`'s
            # docstring has the reasoning): without it, every direct call
            # costs a failed round trip and a cache miss.
            hooks=(ShelfLoader(shelf),) if shelf else (),
        )
