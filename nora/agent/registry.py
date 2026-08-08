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

from ..domain.ports import BlobStore
from ..domain.reminders import ReminderCaps, ReminderStore
from ..infra.routes import RouteClient
from ..infra.search import SearchClient
from ..infra.shortener import LinkShortener
from ..infra.transit import TransitClient
from ..infra.vision import VisionCache, VisionClient
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
        search: SearchClient | None = None,
        presence: PresenceReader | None = None,
        circles: tuple[Circle, ...] = (),
        refresher: Any = None,
        shortener: LinkShortener | None = None,
        ai_peer: str = "nora",
        reminders: ReminderStore | None = None,
        reminder_caps: ReminderCaps | None = None,
        clock: Callable[[], datetime] = utc_now,
        mcp_tools: Mapping[str, McpToolGroup] | None = None,
    ) -> None:
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
        # `search` (SearXNG + Firecrawl, DEC-57) is NOT default-constructed, for
        # the same reason `route` is not: the two web verbs ship INERT behind
        # `[search] enabled` (and the operator's Firecrawl-egress isolation), so
        # every composition that has not wired one — offline, eval, and any
        # deployment with `[search]` off — leaves this None, and `search`/`fetch`
        # answer `unavailable` (the honest «not configured» state, R-5). The
        # composition root passes one built from `[search]` only when enabled.
        self._search = search
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
        # The n.example.net mint client ([shortener], 2026-08-07) — None leaves
        # long URLs in replies, and a failed mint does the same; never
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
        return (
            "These are the tools you were given. Their schemas say what each one "
            "takes; this says when to reach for it.\n" + "\n".join(lines) + "\n"
            + clock +
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
    ) -> tuple[ToolSpec, ...]:
        """The registered tools as plain `ToolSpec`s, closed over this tag's
        dependencies — before any Strands adaptation.

        `transit_source`/`route_source`/`vision_source`/`blob_store`/
        `vision_cache`/`search_source`/`reminders`/`reminder_caps`/`clock` are
        NOT in this signature and must not become one: each is the registry's
        own long-lived collaborator (or, for the cache, its own per-process
        state), not something a tag brings with it, so every caller of
        `specs()`/`build()` is unchanged by their arrival. `reminder_caps` and
        `clock` qualify for the same reason `ai_peer` does — both are
        process-lifetime VALUES, fixed by the deployment, not by the turn.

        `fire_turn` IS in this signature and does not violate that rule: it is a
        per-turn VALUE, like `question` and `conversation_id`, not a
        registry-owned collaborator. The heartbeat's fire path passes True; see
        `nora.agent.tools.build_tool_specs` for what reads it."""
        return build_tool_specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, transit_source=self._transit, route_source=self._route,
            vision_source=self._vision, blob_store=self._blobs,
            vision_cache=self._vision_cache,
            search_source=self._search,
            presence_source=self._presence, presence_circles=self._circles,
            refresher=self._refresher,
            shortener=self._shortener,
            question=question, context_window=context_window,
            ai_peer=self._ai_peer, fire_turn=fire_turn,
            reminders=self._reminders, reminder_caps=self._caps, clock=self._clock,
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
            fire_turn=fire_turn,
        )
        tools = [adapt(spec) for spec in specs]
        for name in allowed_mcp:
            group = self._mcp_tools.get(name)
            if group is not None:
                tools.extend(group.tools)
        return tools
