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

from collections.abc import Mapping
from typing import Any

from ..domain.ports import BlobStore
from ..infra.routes import RouteClient
from ..infra.search import SearchClient
from ..infra.transit import TransitClient
from ..infra.vision import VisionCache, VisionClient
from .ports import ToolSpec, adapt
from .room import Room
from .tools import NATIVE_TOOL_BUDGETS, NATIVE_TOOLS, build_tool_specs
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

    def budgets(self) -> Mapping[str, int]:
        """The per-tool call-count ceiling `ReplyGuard` enforces under R-7."""
        return dict(NATIVE_TOOL_BUDGETS)

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
    ) -> tuple[ToolSpec, ...]:
        """The registered tools as plain `ToolSpec`s, closed over this tag's
        dependencies — before any Strands adaptation.

        `transit_source`/`route_source`/`vision_source`/`blob_store`/
        `vision_cache`/`search_source` are NOT in this signature and must not
        become one: each is the registry's own long-lived collaborator (or, for
        the cache, its own per-process state), not something a tag brings with
        it, so every caller of `specs()`/`build()` is unchanged by their
        arrival."""
        return build_tool_specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, transit_source=self._transit, route_source=self._route,
            vision_source=self._vision, blob_store=self._blobs,
            vision_cache=self._vision_cache,
            search_source=self._search,
            presence_source=self._presence, presence_circles=self._circles,
            question=question, context_window=context_window,
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
    ) -> list[Any]:
        """The registered tools, adapted to what `Agent(tools=...)` wants —
        what `nora.engagement.pipeline.EngagementPipeline` calls in place of
        the old direct `nora.agent.tools.build_tools` import."""
        specs = self.specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
        )
        return [adapt(spec) for spec in specs]
