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

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ..domain.compute import ComputeJobStore
from ..domain.ports import BlobStore, ComputeBackend, GifSource, SearchProvider, WeatherProvider
from ..domain.reminders import ReminderCaps, ReminderStore
from ..infra.routes import RouteClient
from ..infra.shortener import LinkShortener
from ..infra.transit import TransitClient
from ..infra.version import VersionManifest
from ..infra.vision import VisionCache, VisionClient
from .links import LinkCarry
from .mcp import McpToolGroup
from .ports import ToolSpec, adapt
from .room import Room
from .tools import NATIVE_TOOL_BUDGETS, NATIVE_TOOLS, build_tool_specs, utc_now
from .whereis import Circle, PresenceReader


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
    builds three throwaway registries purely to read `budgets()`,
    `tool_names()` and `capability()`, none of which touches a socket — and a
    required parameter there would either force those to invent one or tempt
    the next author into an optional `None` that silently makes `transit`
    answer `unavailable` forever. Constructing one costs nothing: no session
    opens and no catalogue is fetched until a tool is actually called.
    """

    def __init__(
        self, *,
        transit: TransitClient | None = None,
        route: RouteClient | None = None,
        vision: VisionClient | None = None,
        blobs: BlobStore | None = None,
        search: SearchProvider | None = None,
        weather: WeatherProvider | None = None,
        presence: PresenceReader | None = None,
        circles: tuple[Circle, ...] = (),
        refresher: Any = None,
        shortener: LinkShortener | None = None,
        ai_peer: str = "nora",
        reminders: ReminderStore | None = None,
        reminder_caps: ReminderCaps | None = None,
        compute_backend: ComputeBackend | None = None,
        compute_jobs: ComputeJobStore | None = None,
        clock: Callable[[], datetime] = utc_now,
        mcp_tools: Mapping[str, McpToolGroup] | None = None,
        versions: VersionManifest | None = None,
        giphy: GifSource | None = None,
        budget_overrides: Mapping[str, int] | None = None,
    ) -> None:
        self._budget_overrides = dict(budget_overrides or {})
        self._transit = transit if transit is not None else TransitClient()
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
        self._blobs = blobs
        # The description cache lives HERE, once per process, for the reason the
        # transit catalogue does: keyed by content hash, a per-tag cache would
        # never hit, and a module-level singleton is what the test-seam policy
        # forbids. Held even when `media_fetch` is inert — it costs an empty
        # dict and means turning custody on wires nothing new here.
        self._vision_cache = VisionCache()
        # `search` (Tavily, DEC-73) is NOT default-constructed, for the same
        # reason `route` is not: `web_search` ships INERT behind `[search] enabled`
        # AND a key, so every composition that has not wired one — offline, eval,
        # keyless — leaves this None and `web_search` answers `unavailable` (the
        # honest «not configured» state, R-5). No `web_fetch` and no SSRF gate:
        # Tavily searches from its own servers and is never handed a URL.
        self._search = search
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
        # The LIVE-refresh client (DEC-67) — None leaves `where_is` reading the
        # last stored fix (offline/eval, or [presence] refresh_url unset).
        self._refresher = refresher
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
        # `make_artifact`'s pair (Nora Compute spec §4), wired the same honest
        # way `reminders` is: NOT default-constructed — there is no backendless
        # or storeless default — so every composition that has not wired Nora
        # Compute (offline, eval, and any test that does not care) leaves both
        # None and `make_artifact` answers `unavailable` (R-5).
        self._compute_backend = compute_backend
        self._compute_jobs = compute_jobs
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

    def live_only_tools(self) -> tuple[str, ...]:
        """The tools whose answer describes THIS instant and nothing else (design/14 §5.1).

        `time_shiftable is False`, not falsy — the field is three-valued and
        `None` (no time dimension at all) must not land in this set. Written as
        an identity check for that reason; `not spec.time_shiftable` would sweep
        in `calculate` and have the model told that arithmetic goes stale.

        Read by `capability()` below and nowhere else today. It is a tuple in
        table order rather than a frozenset because the only consumer renders
        it into a sentence, and a set would reorder that sentence between runs
        of the same build.
        """
        return tuple(
            name for name, spec in NATIVE_TOOLS.items() if spec.time_shiftable is False
        )

    def web_tier_tools(self) -> tuple[str, ...]:
        """The tools whose answer comes from the OPEN WEB (`source_tier == "web"`),
        in table order (E1/DEC-77).

        Read by `capability()` to render the sourcing sentence — the one nudge
        that names the last-resort tier explicitly so the priority
        confirmed → remembered → web is stated, not merely implied. Table order,
        a tuple, for the same reason `live_only_tools` is: its only consumer
        renders it into a sentence and a set would reorder that sentence between
        builds. `web` rather than the whole `source_tier` partition because the
        sentence only needs to NAME the tier the model over-reached into; the
        closer tiers it describes by category, and a tool that grows a second web
        verb joins this set (and the sentence) for free."""
        return tuple(
            name for name, spec in NATIVE_TOOLS.items() if spec.source_tier == "web"
        )

    def tool_names(self) -> tuple[str, ...]:
        """Every registered tool's name, for the doctrine loader's catalogue gate.

        `nora.doctrine` sits BELOW `nora.agent` in the layer contract and cannot
        import the tool table, so the composition root hands it these instead
        (`nora.doctrine.loader.check_kind_boundaries`).

        Plugin tools (`spec.plugin`, DEC-50) are EXCLUDED: `skills` is not a tool
        this registry builds, and the catalogue gate exists to stop an authored
        `identity`/`conduct` file naming a tool the registry generates the
        capability copy for. `skills` is not in that capability copy (see
        `capability()`), so it does not belong in the gate's list either."""
        return tuple(name for name, spec in NATIVE_TOOLS.items() if not spec.plugin)

    def capability(self) -> str:
        """The capability section, GENERATED (design/17 §5).

        The `kind: capability` body handed to `DoctrineStore`. There is no file
        and there must never be one: capability's source of truth is the
        registry, so any authored copy is a cache, and a cache with no
        invalidation is how `soul.md` spent eight weeks telling Nora she knew
        two things when she knew five.

        **Not a schema dump, and the distinction is the whole design.** The
        schemas are already in the request — measured at 1,222 tokens, 25% of
        the call — and they state, verbatim, what each tool does and what it
        takes. Repeating any of that here would be paid twice for nothing. This
        block adds exactly the one thing a schema has no slot for: when to reach
        for a tool *in this situation*. That is `NativeTool.when`, one clause
        each, and nothing else.

        Rendered from the same table `build_tool_specs` populates every
        `ToolSpec.when` from, so the block and the tools cannot describe
        different sets. `tests/unit/test_tool_registry.py` pins that in both
        directions against real specs.

        Plugin tools (`spec.plugin`, DEC-50) are EXCLUDED. `skills` is provided
        by `AgentSkills` and describes itself through the plugin's own
        `<available_skills>` block, injected only where the plugin is wired
        (`nora.agent.factory`). This method also feeds the offline/eval
        compositions, which build the capability block but supply their own
        runner and never wire the plugin — a `- skills — ...` line there would
        promise a tool that composition does not have."""
        lines = [
            f"- {name} — {spec.when}"
            for name, spec in NATIVE_TOOLS.items()
            if not spec.plugin
        ]
        # One sentence for `time_shiftable is False` (design/14 §5.1). It is
        # rendered rather than hand-written for the reason the rest of this
        # block is: `soul.md` spent eight weeks telling Nora she knew two things
        # when she knew five, and a hand-kept list of live-only tools would rot
        # the same way the moment `route()` lands with `arrive_by`.
        live_only = self.live_only_tools()
        clock = (
            f"{' and '.join(live_only)} describe this moment only — their answers are "
            "already wrong for any other time, so never quote one to answer about "
            "later.\n" if live_only else ""
        )
        # The sourcing nudge (E1/DEC-77): a TWO-level ordering — your own sources
        # (confirmed AND remembered, together) before the open web. Named from the
        # `web` tier (rendered, so it cannot rot); the two closer tiers are
        # described by category rather than enumerated, to stay one sentence in a
        # block paid on every call. The model was web-searching a mall's hours and
        # answering "dunno" about a saved place — a closer source existed for both.
        #
        # Deliberately NOT "confirmed → remembered → web": confirmed vs remembered
        # is PROVENANCE, not precedence. Within the location cluster remembered
        # must sometimes OUTRANK confirmed — "我邊度返工" wants the saved «work»
        # (remembered `saved_places`) over a live GPS fix (confirmed `where_is`) —
        # so ranking confirmed above remembered would push toward that exact trap
        # (external review, 2026-08-10). The precedence the model needs is only
        # own-over-web; which own source fits is the tool's own `when` clause.
        web = self.web_tier_tools()
        sourcing = (
            f"When a fact could come from more than one place, prefer your own: a "
            f"Hong Kong thing you can confirm (the weather, a place, a bus, a route) "
            f"and a place or person this household has named or saved both come before "
            f"{' or '.join(web)}, which reaches the open web and is the last resort, "
            f"not the first move.\n" if web else ""
        )
        # The CLOSED-WORLD clause (DEC-90). The list above is what she can do,
        # and until now nothing said it was the WHOLE of what she can do — so a
        # restaurant answer ended «講清楚我幫你睇埋使唔使訂位», offering to check a
        # booking, which no tool here does and none is planned. That is the same
        # class as guessing a location: asserting a fact about the world (that
        # she will find this out) with nothing behind it.
        #
        # It lives HERE rather than in an authored file for the reason the rest
        # of this block does: the sentence is only true relative to the
        # registry, so an authored copy would be a claim about the tool table
        # maintained somewhere the tool table cannot reach — which is precisely
        # how `soul.md` spent eight weeks saying she knew two things. Written in
        # terms of "the list above" so that adding a booking tool makes it true
        # again with no edit at all.
        #
        # Phrased as a rule about OFFERS, not about knowledge. She may always
        # say she does not know something; what she may not do is promise a
        # future act she has no way to perform. The `not_here` boundary in
        # `conduct.md` keeps that rule out of the authored files.
        return (
            "These are the tools you were given. Their schemas say what each one "
            "takes; this says when to reach for it.\n" + "\n".join(lines) + "\n"
            + clock + sourcing +
            "That list is the WHOLE of what you can do — there is nothing else you can "
            "go and check, and no request you can put in on somebody's behalf. Never "
            "offer, promise or volunteer an action that is not one of the things above: "
            "no booking a table, no phoning anyone, no ordering, no buying, no filling "
            "anything in, and nothing you will \"look into\" later. If someone wants one "
            "of those, say plainly that they have to do it themselves and give them what "
            "you DO have that helps.\n"
            "The things here that reach outside this conversation are live bus data "
            "and routing (a paid map service). When you cannot find something out, say "
            "so in one clause and move on."
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
        fire_turn: bool = False,
        location_trusted: bool = False,
        links: LinkCarry | None = None,
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

        `links` is in it for the same reason and is the same KIND of thing —
        per-TURN, not per-process (DEC-89). It must not become a registry field:
        one `LinkCarry` living for the life of the process would offer a link
        minted in one conversation to a reply being composed in another, which
        is a disclosure rather than a cosmetic bug. It comes from
        `nora.engagement.stages.deliver.build`'s `DeliveryHandle`, which is also
        where the `Deliverer` that consumes it is built."""
        return build_tool_specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, transit_source=self._transit, route_source=self._route,
            vision_source=self._vision, blob_store=self._blobs,
            vision_cache=self._vision_cache,
            search_source=self._search, weather_source=self._weather,
            presence_source=self._presence, presence_circles=self._circles,
            location_trusted=location_trusted,
            refresher=self._refresher,
            shortener=self._shortener,
            question=question, context_window=context_window,
            ai_peer=self._ai_peer, fire_turn=fire_turn,
            reminders=self._reminders, reminder_caps=self._caps, clock=self._clock,
            links=links,
            versions=self._versions,
            giphy_source=self._giphy,
            compute_backend=self._compute_backend, compute_jobs=self._compute_jobs,
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
        fire_turn: bool = False,
        allowed_mcp: frozenset[str] = frozenset(),
        location_trusted: bool = False,
        links: LinkCarry | None = None,
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
        change does not add one."""
        specs = self.specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
            fire_turn=fire_turn, location_trusted=location_trusted, links=links,
        )
        tools = [adapt(spec) for spec in specs]
        for name in allowed_mcp:
            group = self._mcp_tools.get(name)
            if group is not None:
                tools.extend(group.tools)
        return tools
