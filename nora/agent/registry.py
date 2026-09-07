"""`ToolRegistry` — the tool-extension axis DEC-10 exists to unblock (spec §9).

Before this phase, `ReplyGuard.evaluate` (`nora/agent/guards.py`) read a
hardcoded three-entry `BUDGETS` class attribute and vetoed anything absent
from it under R-7 — silently rejecting every tool not in that literal table,
including a fourth native tool or `end_episode`. `ToolRegistry.budgets()` is
what a `ReplyGuard` is now constructed with instead (`nora.engagement.stages
.deliver.build`, threaded from `nora.app.composition`/`nora.app.offline`), so
a new tool's budget comes from wherever it is registered, not from a table a
future author has to remember to also edit in `guards.py`.

It registers whatever `nora.agent.tools` builds, at the budgets that module
declares — `NATIVE_TOOL_BUDGETS` is the single table both this class and
`build_tool_specs` read, so the number `ReplyGuard` enforces and the number a
tool is declared to cost can never drift apart. That is the property DEC-10
exists for, and adding the three room-scoped tools (`group_members`,
`lookup_person`, `about`) exercised it: they became callable by appearing in
that one table, with no edit to `guards.py` at all.

Deliberately NOT here: a `ToolProvider` protocol, MCP tool discovery/lifecycle,
or anything dynamic — spec §7/§11 defer all of that. `ToolRegistry` is a plain,
constructed collaborator (no module-level singleton — CLAUDE.md's test-seam
policy) built once per process by the composition root.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from strands.tools.decorator import FunctionToolMetadata

from ..domain.compute import ComputeJobStore
from ..domain.models import TranscriptRow
from ..domain.ports import (
    BlobStore,
    ComputeBackend,
    GifSource,
    MediaDescriptionStore,
    MediaObjectStore,
    MusicToolBridge,
    ParkingProvider,
    SearchProvider,
    SocialProvider,
    StreetParkingProvider,
    WeatherProvider,
)
from ..domain.reminders import ReminderCaps, ReminderStore
from ..domain.tool_call_log import ToolCallLogStore
from ..infra.parking import ParkingClient, StreetParkingClient
from ..infra.routes import RouteClient
from ..infra.shortener import LinkShortener
from ..infra.transit import TransitClient
from ..infra.version import VersionManifest
from ..infra.vision import VisionCache, VisionClient
from .links import LinkCarry
from .mcp import McpToolGroup
from .ports import ToolSpec, adapt
from .relay import Relay
from .room import Room
from .tools import (
    NATIVE_TOOL_BUDGETS,
    NATIVE_TOOLS,
    EventsCollaborator,
    ImageGenerator,
    NoteCollaborator,
    build_tool_specs,
    utc_now,
)
from .whereis import Circle, PresenceReader

log = logging.getLogger("nora.agent.registry")


class ToolRegistry:
    """Owns which native tools exist and what each costs (DEC-10).

    It also HOLDS the one long-lived collaborator a tool needs across tags.
    `TransitClient` caches both operators' route catalogues — 350 KB from KMB,
    republished at 05:00 daily — and a per-tag instance would re-pull that for
    every bus question anybody asked. The registry is the right owner because it
    is already the one thing the composition root builds once per process
    (DEC-10), and because putting the cache anywhere else would mean a
    module-level mutable singleton, which CLAUDE.md's test-seam policy forbids
    and `tests/architecture/test_no_module_level_mutable_singletons.py`
    enforces.

    It is DEFAULT-CONSTRUCTED rather than required. `nora.app.eval_support`
    builds throwaway registries purely to read `budgets()` and `tool_names()`,
    neither of which touches a socket — and a required parameter there would
    either force those to invent one or tempt the next author into an optional
    `None` that silently makes `transit` answer `unavailable` forever.
    Constructing one costs nothing: no session opens and no catalogue is
    fetched until a tool is actually called.
    """

    def __init__(
        self, *,
        transit: TransitClient | None = None,
        parking: ParkingProvider | None = None,
        street_parking: StreetParkingProvider | None = None,
        route: RouteClient | None = None,
        vision: VisionClient | None = None,
        describe_models: Mapping[str, str] | None = None,
        describe_providers: Mapping[str, str] | None = None,
        describe_max_bytes: Mapping[str, int] | None = None,
        blobs: BlobStore | None = None,
        descriptions: MediaDescriptionStore | None = None,
        search: SearchProvider | None = None,
        social: SocialProvider | None = None,
        weather: WeatherProvider | None = None,
        presence: PresenceReader | None = None,
        circles: tuple[Circle, ...] = (),
        # DEC-105. `None` is the honest not-configured state every offline and
        # eval composition ships in — `send_to` then answers `unavailable`,
        # never a silent no-op.
        relay: Relay | None = None,
        refresher: Any = None,
        geocoder: Any = None,
        # The DERIVED-presence read behind `where_been` (DEC-155,
        # presence-analytics P1): the composition root's ONE `SqlMobilityStore`
        # (same engine, same lifecycle as every other store). NOT
        # default-constructed, for the reason `route`/`presence` are not:
        # there is no storeless default, so every composition that has not
        # built the substrate — offline, eval — leaves `where_been` answering
        # `unavailable`, honest and inert (R-5). The per-call R-31 guard lives
        # in the tool itself, through the same `DisclosureGuard` seam.
        mobility: Any = None,
        shortener: LinkShortener | None = None,
        ai_peer: str = "nora",
        reminders: ReminderStore | None = None,
        reminder_caps: ReminderCaps | None = None,
        events: EventsCollaborator | None = None,
        notes: NoteCollaborator | None = None,
        note_workspace: Callable[[str, str], str | None] | None = None,
        workspace_for: Callable[[str, str], str | None] | None = None,
        compute_backend: ComputeBackend | None = None,
        compute_jobs: ComputeJobStore | None = None,
        tool_call_log_store: ToolCallLogStore | None = None,
        clock: Callable[[], datetime] = utc_now,
        mcp_tools: Mapping[str, McpToolGroup] | None = None,
        versions: VersionManifest | None = None,
        giphy: GifSource | None = None,
        music: MusicToolBridge | None = None,
        image_generator: ImageGenerator | None = None,
        image_refs_allowed: bool = False,
        media_store: MediaObjectStore | None = None,
        budget_overrides: Mapping[str, int] | None = None,
    ) -> None:
        self._budget_overrides = dict(budget_overrides or {})
        self._transit = transit if transit is not None else TransitClient()
        # `parking` is keyless like `transit` — the TD open-data feed needs no
        # key, so the same default-construct argument applies: a required
        # parameter would force offline/eval compositions to invent one, and
        # constructing a `ParkingClient` costs nothing (no session, no index
        # fetch until a tool is called).
        self._parking = parking if parking is not None else ParkingClient()
        # `street_parking` is the same keyless TD feed family (hk-td-msd_1,
        # the metered on-street spaces) — default-constructs for the same
        # reasons as `parking`: no key, no cost until a tool calls it.
        self._street_parking = (
            street_parking if street_parking is not None else StreetParkingClient()
        )
        # `route` is NOT default-constructed: unlike `TransitClient`, a
        # `RouteClient` needs a Google API key, and there is no keyless default
        # to fall back to. When it is None — every composition that has not
        # wired a key, including `nora.app.offline` and the eval registries —
        # the `route` tool answers `unavailable`, which is the honest state for
        # "routing is not configured" (R-5). The composition root passes one
        # built from the resolved `[route]` key (DEC-8); until it does, `route`
        # is present in the catalogue and refuses to leak, but cannot travel.
        self._route = route
        # `vision`/`blobs` are the `media_fetch` collaborators (DEC-56), wired
        # together and only when custody is on (`[media] enabled` + key → a blob
        # store) AND a reachable OmniRoute client exists. Either being None
        # leaves `media_fetch` answering `unavailable` — the same inert-by-config
        # shape as keyless `route` — so it ships present-but-inert while
        # `[media] enabled=false`.
        self._vision = vision
        # `describe_models` rides alongside `vision`, the same VALUE-not-object
        # split `ai_peer` already makes on this class: `nora.agent` may not
        # import `nora.config` (CLAUDE.md), so the composition root resolves
        # `cfg.media.describer_for(...)` and hands plain strings in here, exactly
        # as it resolves the `VisionClient` itself.
        #
        # A modality is PRESENT here only when it is enabled AND names a model,
        # so absence is the single meaning of «no describer» and the tool needs
        # no second flag to read. Audio and video ship absent (2026-08-17 spec).
        self._describe_models = dict(describe_models or {})
        self._describe_providers = dict(describe_providers or {})
        # Per-modality byte ceilings for the describer, present only when the
        # modality names one (0/None = inherit the VisionClient's shared
        # ceiling). Same VALUE-not-object split as the two maps above.
        self._describe_max_bytes = dict(describe_max_bytes or {})
        self._blobs = blobs
        # The description cache lives HERE, once per process, for the reason the
        # transit catalogue does: keyed by content hash, a per-tag cache would
        # never hit, and a module-level singleton is what the test-seam policy
        # forbids. Held even when `media_fetch` is inert — it costs an empty
        # dict and means turning custody on wires nothing new here.
        #
        # `store=descriptions` (Task 10) makes it a read-through onto the
        # durable table rather than a memory-only cache that loses every
        # description on every deploy — `None` (every composition that has not
        # wired one) leaves it exactly as it was before this task.
        self._vision_cache = VisionCache(store=descriptions)
        # `search` (Tavily, DEC-73/DEC-116) is NOT default-constructed, for the
        # same reason `route` is not: `web_search`/`web_fetch` ship INERT behind
        # `[search] enabled` AND a key, so every composition that has not wired
        # one — offline, eval, keyless — leaves this None and both answer
        # `unavailable` (the honest «not configured» state, R-5). No SSRF gate
        # needed for either verb: Tavily fetches from its own servers, never
        # Nora's, whether it is handed a query or a url.
        self._search = search
        # `social` (OpenCLI bridge, DEC-151) is NOT default-constructed either:
        # `social_fetch` ships INERT behind `[social] enabled` (keyless — the
        # bridge is loopback-only and holds the read-only allowlist host-side),
        # so every composition that has not wired one leaves this None and the
        # tool answers `unavailable` (the honest «not configured» state, R-5).
        self._social = social
        # `weather` (HKO, DEC-72) is NOT default-constructed either, for symmetry
        # with `route`/`search`: it ships behind `[weather] enabled`, and every
        # composition that has not wired one leaves this None and `weather` answers
        # `unavailable`. Keyless, so the composition root wires one on `enabled`
        # alone — no key to resolve.
        self._weather = weather
        # `presence`/`circles` are `whereis`'s collaborators (DEC-59, activating
        # DEC-54's inert seam): the local `presence_fixes` reader and the circle
        # rosters derived from identity.toml (`circles_from_persons`). NOT
        # default-constructed, for the reason `route` is not: there is no
        # readerless default, so every composition that has not wired them —
        # offline, eval, `[presence]` disabled, an identity with no circle —
        # leaves `whereis` answering `unavailable`, honest and inert (R-5). The
        # per-call R-31 subset guard lives in `nora.agent.whereis`, not here.
        self._presence = presence
        self._circles = tuple(circles)
        self._relay = relay
        # The LIVE-refresh client (DEC-67) — None leaves `where_is` reading the
        # last stored fix (offline/eval, or [presence] refresh_url unset).
        self._refresher = refresher
        # The on-demand reverse-geocoder (moved here from the collector,
        # 2026-08-14) — None leaves `where_is` falling back to Life360's own
        # address or the raw coordinate, same as no geocoder ever existing.
        self._geocoder = geocoder
        # The derived-presence read behind `where_been` (DEC-155) — the same
        # optional-collaborator shape as `presence` above: None in the
        # offline/eval compositions, where the tool answers `unavailable`
        # rather than a guessed empty (R-5).
        self._mobility = mobility
        # The drop-service mint client ([shortener], cutover 2026-08-12 to
        # `drop`'s internal API; was n.example.net's Worker before that) — None
        # leaves long URLs in replies, and a failed mint does the same; never
        # load-bearing, so no capability line and no budget: it is not a tool,
        # it is a post-processing step inside `route`.
        self._shortener = shortener
        # The OBSERVER peer — Nora's Honcho peer id (cfg.memory.ai_peer). The
        # `about`/`ask_memory` tools read her model of a person as
        # (observer=ai_peer, observed=person), so the registry threads it like any
        # other long-lived collaborator (design/24 §4). A future `[agent] name`
        # config feeds this same seam.
        self._ai_peer = ai_peer
        # The reminders store (design spec §7.3): a per-process collaborator
        # like `transit` and `search`, NOT default-constructed for the reason
        # `route` is not — there is no storeless default, so every composition
        # that has not wired one (offline, eval, and any test that does not care)
        # leaves `remind` answering `unavailable`, the honest "not configured"
        # state (R-5). `nora.app.composition` passes the same `SqlReminderStore`
        # the heartbeat's tick reads from; two would be two queues.
        self._reminders = reminders
        # `GatheringService`'s own collaborator (the Event module design spec),
        # wired the identical honest way `reminders` is: NOT default-constructed
        # — there is no serviceless default — so every composition that has not
        # wired Events (offline, eval, and any test that does not care) leaves
        # this None and the six event tools answer `unavailable` (R-5).
        self._events = events
        # The note tier's pair (note-tier spec §5.2/§6), wired the identical
        # honest way `events` is — no store, no workspace, or neither, and the
        # four note verbs answer `unavailable` (R-5) rather than crash or
        # report an unconsulted notebook as an empty one.
        #
        # `note_workspace` is a registry field because the RESOLVER is
        # process-lifetime; the workspace it returns is not. It was first
        # written as a plain `str` on the reasoning that the seam is fixed by
        # the deployment — false the moment a deployment has two workspaces,
        # and this one has had two all along (`ridgeline` and `friends`,
        # `policy.toml`, deliberately never paired). A constant would hand one
        # room the other's notebook, which is a cross-workspace read AND write
        # past the boundary spec §3 and DEC-105/R-49 both rest on. It takes
        # `(platform, conversation_id)` and may answer `None`.
        self._notes = notes
        self._note_workspace = note_workspace
        # ONE resolver for every workspace-keyed tool. `note_workspace` is its
        # older name and still works; `workspace_for` is what a tier outside
        # the note tier passes, so it does not have to read the NOTE tier's
        # parameter and thereby depend on the note tier being wired.
        self._workspace_for = workspace_for
        # `make_artifact`'s pair (Nora Compute spec §4), wired the same honest
        # way `reminders` is: NOT default-constructed — there is no backendless
        # or storeless default — so every composition that has not wired Nora
        # Compute (offline, eval, and any test that does not care) leaves both
        # None and `make_artifact` answers `unavailable` (R-5).
        self._compute_backend = compute_backend
        self._compute_jobs = compute_jobs
        # `recall_lookups`'s own collaborator (tool-recall-log spec §4), wired
        # the identical honest way `compute_backend`/`compute_jobs` are: `None`
        # in the offline/eval compositions and any composition that has not
        # wired the tool-recall log, which is what makes `recall_lookups`
        # answer `unavailable` (R-5) rather than crash.
        self._tool_call_log_store = tool_call_log_store
        # §9's three anti-runaway caps, as the DOMAIN value — never
        # `nora.config.RemindersConfig`. Import-linter's "only app and __main__
        # read config" contract names `nora.agent` as a source module and
        # `nora.config` as forbidden, so the config type here fails
        # `uv run lint-imports` outright (and a TYPE_CHECKING guard does not
        # rescue it: grimp's `exclude_type_checking_imports` defaults to False
        # and this repo does not set it). `| None` rather than a default
        # instance because ruff's B008 rejects a call in an argument default.
        self._caps = reminder_caps or ReminderCaps()
        # The clock, and there was no convention here to follow: this
        # constructor took ten collaborators and imported no `datetime`. Three
        # of `remind`'s six refusals are clock comparisons (`when < now`,
        # `min_lead_seconds`, `max_horizon_days`), so a fake clock is what makes
        # them testable without sleeping — and a collaborator through `__init__`
        # is the only seam CLAUDE.md's test-seam policy allows.
        self._clock = clock
        # Discovered MCP tool groups (spec: mcp-tool-provider-design §3),
        # keyed by server name. `.budgets()` folds an entry in ONLY when its
        # server name is passed in that call's `allowed_mcp` — see
        # `.budgets()` below for why an unconditional fold was a real
        # disclosure, not an inert one. `.specs()`/`.build()`'s `allowed_mcp`
        # is the SAME value a caller must pass here (final-review Finding 1).
        self._mcp_tools = dict(mcp_tools) if mcp_tools is not None else {}
        # The baked build stamp (DEC-97), DEFAULT-CONSTRUCTED like `transit` and
        # unlike `route`/`search`/`weather`: there is no key to resolve and no
        # service to reach, only a file that either shipped with this code or
        # did not, so there is nothing an unconfigured deployment could be
        # missing. Reading it costs one open, once, on the first `version` call
        # — a source checkout has no `nora/version.json`, and the reader answers
        # `unavailable` for it (never `nothing_found`, R-27). Held HERE for the
        # reason the transit catalogue is: it is a process-lifetime fact, and
        # the alternative — a module-level cache — is the mutable singleton the
        # test-seam policy forbids.
        self._versions = versions if versions is not None else VersionManifest()
        self._giphy = giphy
        # `MusicToolBridge` (design/discord-music/07-agent-tool.md), wired the
        # identical honest way `events`/`compute_backend` are: NOT
        # default-constructed — there is no bridgeless default, and every
        # composition that has not wired `DiscordMusicToolBridge` (offline,
        # eval, `[music] enabled = false`, or Discord itself disabled) leaves
        # this None and all four music tools answer `unavailable` (R-5). The
        # Discord-only gate is a SEPARATE check each tool makes on
        # `room.platform` before ever touching this collaborator.
        self._music = music
        # `ImageGenerator` (DEC-137), wired the same honest way: NOT
        # default-constructed. `None` (config `[image_gen] enabled = false`)
        # leaves the `generate_image` tool present in the catalogue answering
        # `unavailable` — the wiring-gap state, never `nothing_found`.
        self._image_generator = image_generator
        self._image_refs_allowed = image_refs_allowed
        self._media_store = media_store

    def budgets(self, *, allowed_mcp: frozenset[str] = frozenset()) -> Mapping[str, int]:
        """The per-tool call-count ceiling `ReplyGuard` enforces under R-7.

        Includes an MCP tool's budget ONLY when its server name is in
        `allowed_mcp` — the SAME set the caller passes (or will pass) to
        `.build()`. `budgets()` feeds `ReplyGuard`, and `ReplyGuard.evaluate`
        renders the WHOLE dict's keys into the model-facing R-7 unknown-tool
        veto message (`guards.py::_list_tool_names`) — so an unconditional
        fold was a REAL disclosure: a conversation with no `pulse` allowlist
        entry would still be told `pulse_server_status` exists the moment the
        model hallucinated any wrong tool name, contradicting the spec's
        claim that this dict is never rendered into a prompt. It also left
        R-7's default-deny not actually covering those names for an
        unauthorized conversation, since they were present in `_budgets`
        regardless. The default (`frozenset()`) is the safe one: a caller
        with no conversation context (`nora.app.composition`'s throwaway
        registries) gets exactly the native table, nothing more."""
        budgets = dict(NATIVE_TOOL_BUDGETS)
        # `[llm.budgets]` from config.toml, applied OVER the table rather than
        # instead of it, so the file may carry one line without restating
        # twenty (operator, 2026-08-11: budgets "will keep slowly increasing"
        # as tools are added, and a rebuild to move a number is friction with
        # no safety return).
        #
        # This does not break DEC-10/DEC-32's ONE-NUMBER rule, and the
        # distinction is the whole reason it is applied HERE. The table still
        # states the default and the reasoning next to the tool; config states
        # a per-deployment DELTA; and `budgets()` remains the single place
        # anything reads a budget from — `ReplyGuard`'s R-7 ceiling, the
        # `Deliverer`'s message cap and the tests all still resolve to one
        # value at runtime. Putting `send_reply` alone in config, which is what
        # was asked for first, WOULD have broken it: fifteen budgets in code
        # and one in config is the split those decisions exist to prevent.
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
        ends a turn (DEC-21). It used to end one by asking the `Deliverer`
        whether it had delivered — terminality expressed as a single tool's side
        effect, which is the pre-DEC-10 hardcoded `BUDGETS` mistake wearing a
        different hat. `design/proposed/15-gif-replies.md` §1 proposed fixing
        that by defining the verb class *in the guard*; this defines it on the
        tool instead, so `send_gif` — or any other verb that ends a turn —
        becomes one `terminal=True` in `NATIVE_TOOLS`.

        A `frozenset` rather than a mapping: membership is the whole question,
        and there is no per-tool value to carry.
        """
        return frozenset(name for name, spec in NATIVE_TOOLS.items() if spec.terminal)

    def cacheable_tools(self) -> frozenset[str]:
        """The tools whose successful result may be logged to `tool_call_log`
        and later replayed by `recall_lookups` (tool-recall-log spec §2).

        A `frozenset`, the same shape `terminal_tools()` returns and for the
        same reason: membership is the whole question, and there is no
        per-tool value to carry.
        """
        return frozenset(name for name, spec in NATIVE_TOOLS.items() if spec.cacheable)

    def read_only_tools(self) -> frozenset[str]:
        """The tools that only LOOK — the Dreamer's ceiling (keystone §4).

        The hard line the Dreamer-as-an-agent design draws is that a background
        research loop **may look and may not act**: a process that can speak to
        the group unprompted, set a reminder, or write to the events store with
        nobody waiting on it is a categorically different and more dangerous
        thing than one that can only read. That is a property of the runtime,
        not a setting, so it is DERIVED here and hand-listed nowhere.

        **This is a ceiling, not a grant.** Each Dreamer adaptor declares its
        own, narrower subset by tool name (`DreamAdaptor.tools`) — proving a
        fact wants `web_search`/`web_fetch`, glossing a word does not — and the
        runtime refuses any name outside this set. So a tool being read-only
        never by itself puts it in front of a model; it only makes it
        *grantable*.

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
        `identity`/`conduct` file naming a tool it does not have. `skills` is not
        built or described here (see `specs()`), so it does not belong in the
        gate's list either."""
        return tuple(name for name, spec in NATIVE_TOOLS.items() if not spec.plugin)

    def catalog(self) -> tuple[dict, ...]:
        """Every built tool's public metadata — name, model-facing
        description, and declared registry fields — for the web portal's
        `/app/v1/tools` endpoint.

        This is the LIVE surface: the descriptions come from the same
        Strands introspection the agent loop derives (`FunctionToolMetadata.
        extract_metadata`, Args-stripped), and the declared fields are read
        straight off `NATIVE_TOOLS` — so what the portal shows IS what the
        deployed agent can call, and a tool added on master appears here on
        the next deploy with no frontend rebuild.

        Why this method lives HERE and not in `nora.web`: the strands import
        boundary (`tests/test_boundaries.py::test_strands_import_boundary`)
        allows `strands` only under `nora/agent/` and `nora/compute/` — the
        web layer must not import it. The registry already owns the tool
        table, so it is the natural single reader.

        Plugin tools are excluded by construction (`build_tool_specs` does
        not build them). A spec whose `call` cannot be introspected keeps its
        declared fields with `description` dropped — one bad tool must never
        sink the endpoint (R-27), and the failure is logged server-side.
        """
        specs = build_tool_specs(
            store=None,
            conversation_id="catalog",
            conversation_type="group",
            persons=None,
            honcho=None,
            deliverer=None,
            metrics=None,
            room=self._catalog_room(),
            question="",
            context_window=40,
            transit_source=self._transit,
        )
        catalog = []
        for spec in specs:
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

    @staticmethod
    def _catalog_room() -> Any:
        """The minimal `Room` stand-in `build_tool_specs` reads while building.

        Only the attributes the builder touches while constructing the tool
        list matter (`platform`/`conversation_id` for the conversation-scoped
        closures); every collaborator is `None` because no tool is CALLED
        here (a call would be the only thing touching a socket or a roster).
        A real `Room` is constructed per tag with a roster source — the
        catalog needs none of it.
        """
        return SimpleNamespace(
            platform="signal", conversation_id="catalog", tagger=None,
        )

    def specs(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        honcho: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
        transcript: tuple[TranscriptRow, ...] | None = None,
        fire_turn: bool = False,
        location_trusted: bool = False,
        links: LinkCarry | None = None,
        supplied_workspace: str | None = None,
    ) -> tuple[ToolSpec, ...]:
        """The registered tools as plain `ToolSpec`s, closed over this tag's
        dependencies — before any Strands adaptation.

        `transit_source`/`route_source`/`vision_source`/`blob_store`/
        `vision_cache`/`search_source`/`reminders`/`reminder_caps`/`clock`/
        `versions` are
        NOT in this signature and must not become one: each is the registry's
        own long-lived collaborator (or, for the cache, its own per-process
        state), not something a tag brings with it, so every caller of
        `specs()`/`build()` is unchanged by their arrival. `reminder_caps` and
        `clock` qualify for the same reason `ai_peer` does — both are
        process-lifetime VALUES, fixed by the deployment, not by the turn.

        `fire_turn` IS in this signature and does not violate that rule: it is a
        per-turn VALUE, like `question` and `conversation_id`, not a
        registry-owned collaborator. The heartbeat's fire path passes True; see
        `nora.agent.tools.build_tool_specs` for what reads it.

        `transcript` is in it for the same reason and is the most per-turn value
        here: it is `TagContext.transcript`, the window the prefill rendered
        `<group_transcript>` from, and `who_said` (DEC-146) answers out of it.
        It must never become a registry field — a window living for the life of
        the process would let one conversation's messages be searched from
        another, which is a disclosure and not a staleness bug. Passing the SAME
        tuple the prefill used is what keeps the tool and the block in the
        prompt one interpretation of one window rather than two.

        `links` is in it for the same reason and is the same KIND of thing —
        per-TURN, not per-process (DEC-89). It must not become a registry field:
        one `LinkCarry` living for the life of the process would offer a link
        minted in one conversation to a reply being composed in another, which
        is a disclosure rather than a cosmetic bug. It comes from
        `nora.engagement.stages.deliver.build`'s `DeliveryHandle`, which is also
        where the `Deliverer` that consumes it is built.

        `supplied_workspace` is per-CALL for the same reason, from exactly one
        caller: `nora.agent.dreamer.RegistryToolSource`, whose workspace-keyed
        adaptors ARE keyed on the workspace and must SUPPLY it rather than let
        the tool layer derive it from a room they do not have
        (design/dreamer-agent-loop/32 §3.4, `08-state.md` §10). It must not
        become a registry field either: a workspace fixed for the life of the
        process is the cross-workspace read `note_workspace`'s own comment
        (above, in `__init__`) exists to prevent. Every tag-path caller leaves
        it None and the resolver answers as before."""
        return build_tool_specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, transit_source=self._transit, route_source=self._route,
            vision_source=self._vision, blob_store=self._blobs,
            vision_cache=self._vision_cache, describe_models=self._describe_models,
            describe_providers=self._describe_providers,
            describe_max_bytes=self._describe_max_bytes,
            search_source=self._search, weather_source=self._weather,
            parking_source=self._parking,
            street_parking_source=self._street_parking,
            social_source=self._social,
            presence_source=self._presence, presence_circles=self._circles,
            location_trusted=location_trusted,
            mobility=self._mobility,
            refresher=self._refresher,
            geocoder=self._geocoder,
            shortener=self._shortener,
            question=question, context_window=context_window,
            transcript=transcript,
            ai_peer=self._ai_peer, fire_turn=fire_turn,
            reminders=self._reminders, reminder_caps=self._caps, clock=self._clock,
            events=self._events,
            notes=self._notes, note_workspace=self._note_workspace,
            workspace_for=self._workspace_for,
            supplied_workspace=supplied_workspace,
            links=links,
            relay=self._relay,
            versions=self._versions,
            giphy_source=self._giphy,
            compute_backend=self._compute_backend, compute_jobs=self._compute_jobs,
            tool_call_log_store=self._tool_call_log_store,
            music=self._music,
            image_generator=self._image_generator,
            image_refs_allowed=self._image_refs_allowed,
            media_store=self._media_store,
        )

    def build(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        honcho: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
        transcript: tuple[TranscriptRow, ...] | None = None,
        fire_turn: bool = False,
        allowed_mcp: frozenset[str] = frozenset(),
        location_trusted: bool = False,
        links: LinkCarry | None = None,
        supplied_workspace: str | None = None,
    ) -> list[Any]:
        """The registered tools, adapted to what `Agent(tools=...)` wants —
        what `nora.engagement.pipeline.EngagementPipeline` calls in place of
        the old direct `nora.agent.tools.build_tools` import.

        `nora.app.fire.FirePath` is the other caller, and the only one that
        passes `fire_turn=True`.

        `allowed_mcp` (spec §3) is the enforcement point for per-conversation
        MCP scope: only tool groups whose server name is in this set are
        appended, and they are appended AFTER `adapt()` runs over the native
        specs — MCP tools are already Strands tool objects (`McpToolGroup.tools`,
        Task 2), not `ToolSpec`s, so `specs()` cannot return them and `adapt()`
        must not be called on them. Native tools are unaffected — this
        codebase has no per-conversation gating for them today, and this
        change does not add one.

        `supplied_workspace` passes straight through to `specs()` — see that
        method's docstring; it exists for the Dreamer's workspace-keyed build
        and every other caller leaves it None."""
        specs = self.specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
            transcript=transcript,
            fire_turn=fire_turn, location_trusted=location_trusted, links=links,
            supplied_workspace=supplied_workspace,
        )
        tools = [adapt(spec) for spec in specs]
        for name in allowed_mcp:
            group = self._mcp_tools.get(name)
            if group is not None:
                tools.extend(group.tools)
        return tools
