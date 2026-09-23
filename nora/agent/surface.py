"""The per-turn tool surface — what ONE occasion's agent may see, and how.

Every loop that hands tools to a model — a tag (`nora.engagement.pipeline`),
a fire turn (`nora.app.fire`: participation, proactive act, reminders) and
every Dreamer adaptor (`nora.agent.dreamer.RegistryToolSource`) — gets its
tools from `ToolRegistry.surface()`, and from nowhere else. Before this module
the question «which tools does this occasion see» was answered in three
places: a platform filter and a Dreamer-only gate inside `build_tool_specs`,
the MCP allowlist inside `ToolRegistry.build`, and a name filter the Dreamer
applied to `build()`'s output after the fact. Three answers to one question is
how an occasion ends up advertised a tool it cannot call.

Two value objects and one per-turn holder:

- `ToolScope` — WHICH tools this occasion may see. Pure: a platform, whether
  the Dreamer built it, and an optional grant ceiling. Nothing here is Strands.
- `ToolShelf` — the tools held OUT of the model's `tools` array this turn,
  which `load_tools` registers on demand (design/tool-catalogue/02). Per turn by
  construction: it is created inside `surface()` and dies with the agent that
  was handed it, so a loaded schema can never leak into another room's turn or
  silently regrow the resident set over the process's life.
- `ToolSurface` — the result: the resident tools, the shelf, and the catalogue
  lines, all derived from ONE admitted set. The catalogue lists exactly the
  tools the scope admits, and `load_tools` can fetch exactly the tools on the
  shelf, so the catalogue cannot name a tool the turn cannot reach.

This module does not import `strands` (ADR 0008): the tools it holds are
opaque objects the registry adapted, and registration goes through the agent
the framework hands `load_tools` at call time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

#: The music verbs. Discord-only by construction — their bodies gate on the
#: platform too, but shipping them elsewhere costs prompt tokens for a tool
#: that can only answer `unavailable` there (2026-09-16 operator decision).
DISCORD_ONLY_TOOLS: frozenset[str] = frozenset({
    "play_music", "skip_music", "top_music", "stop_music", "music_status",
    "set_music_volume",
})

#: Admitted only when a Dreamer adaptor built the set (#350):
#: `search_memory_workspace` returns verbatim lines from Honcho's WHOLE
#: workspace, every unit, and a room could otherwise surface words said by
#: people in none of its units — the crossing `send_to` refuses (R-49).
DREAMER_ONLY_TOOLS: frozenset[str] = frozenset({"search_memory_workspace"})


@dataclass(frozen=True)
class ToolScope:
    """Which tools one occasion may see.

    `platform` is the room's platform, or `None` for the DECLARED table — a
    caller asking «what is registered» (`ToolRegistry.specs(room=None)`, the
    portal's catalog) rather than «what does this room see», which admits
    everything.

    `dreamer` is True when a Dreamer adaptor built this set (`dream_adaptor`
    is non-empty). `search_memory_workspace` is admitted only then (#350): it
    returns verbatim lines from Honcho's WHOLE workspace, every unit, and a
    room could otherwise surface words from people in none of its units.

    `grant` is a ceiling by name, or `None` for no ceiling. The Dreamer passes
    its adaptor's granted tools intersected with what it may hold
    (read-only, plus the acts a home-room grant allows) — the filter that used
    to run on `build()`'s output, now part of the one scope.
    """

    platform: str | None
    dreamer: bool = False
    grant: frozenset[str] | None = None

    def admits(self, name: str) -> bool:
        if self.grant is not None and name not in self.grant:
            return False
        if self.platform is None:
            return True
        if name in DISCORD_ONLY_TOOLS and self.platform != "discord":
            return False
        return not (name in DREAMER_ONLY_TOOLS and not self.dreamer)


class ToolShelf:
    """The tools held out of this turn's `tools` array, loadable by name.

    Mutable only between `surface()` building the tools and handing them over:
    `load_tools` is built in the same pass as the tools it will fetch, so the
    shelf is created empty, closed over by `load_tools`, and stocked once the
    held set is known. Nothing restocks it afterwards.
    """

    def __init__(self) -> None:
        self._held: dict[str, Any] = {}

    def stock(self, held: Mapping[str, Any]) -> None:
        self._held = dict(held)

    def get(self, name: str) -> Any | None:
        return self._held.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._held)

    def __bool__(self) -> bool:
        return bool(self._held)


class ToolSurface:
    """One occasion's tools for one turn.

    `tools` is what `Agent(tools=...)` receives: the resident tools, then
    `load_tools` when anything is on the shelf, then the admitted MCP groups.
    `catalogue` is the model-facing index — one line per admitted native tool,
    `*` marking the resident ones — rendered lazily, because nothing reads it
    until the catalogue block is placed in a prompt, and rendering it
    introspects every tool.
    """

    def __init__(
        self,
        *,
        scope: ToolScope,
        tools: Iterable[Any],
        resident: Iterable[str],
        shelf: ToolShelf,
        admitted: Iterable[str],
        render: Callable[[Iterable[str], frozenset[str]], list[str]],
    ) -> None:
        self.scope = scope
        self.tools = list(tools)
        self.resident = frozenset(resident)
        self.shelf = shelf
        self.admitted = frozenset(admitted)
        self._render = render

    @cached_property
    def catalogue(self) -> str:
        return "\n".join(self._render(self.admitted, self.resident))

    @property
    def prompt_catalogue(self) -> str:
        """The catalogue as the PROMPT should carry it: the index when anything
        is deferred, nothing when every admitted tool is already in the
        `tools` array — there the schemas already say it all, and a second
        copy is what DEC-150 deleted the capability block for."""
        return self.catalogue if self.shelf else ""

    def limits(self, base: Mapping[str, Any]) -> dict[str, Any]:
        """The loop's R-8 caps for THIS surface.

        A deferred turn spends one model call on `load_tools` before the call
        that uses what it loaded, and `turns` is a shared ceiling: left alone,
        that call comes out of the real work — the inert-under-a-shared-pass
        failure (`per-tool-budget-is-inert-under-a-shared-pass-ceiling`). So
        the ceiling rises by one when anything is on the shelf. One, not the
        measured 0.8 mean rounded: a turn either loads or does not, and the
        loader's own `budget` bounds how many of its calls a turn may make.
        The token caps are left alone — a deferred turn's prompt is SMALLER,
        which is the point of deferring.
        """
        limits = dict(base)
        if self.shelf and "turns" in limits:
            limits["turns"] = int(limits["turns"]) + 1
        return limits


#: `load_tools`' per-call name cap (design/tool-catalogue/02). Its budget caps
#: CALLS, not tokens: without this, one `load_tools([...every name...])` would
#: register the whole shelf and undo the change in a single call. 4 covers the
#: measured p90 of 2 cold tools per turn with headroom; the observed maximum,
#: 7, is two calls.
LOAD_TOOLS_MAX_NAMES = 4


def load_onto(agent: Any, shelf: ToolShelf, names: Iterable[str]) -> dict:
    """Register the named shelf tools on `agent`'s own tool registry.

    Strands re-reads `get_all_tool_specs()` on every model call and dispatches
    from `dynamic_tools` first, so a tool registered here is in the NEXT
    call's `tools` array natively and dispatchable — the model is never asked
    to call a tool it was not given (design/tool-catalogue/02, verified by
    spike against 1.50.2).

    R-27's shape. `ok` when at least one name is usable afterwards — a partial
    resolve is still `ok`, because the tools that resolved are registered and
    reporting that as a failure would throw away work that succeeded.
    `nothing_found` when no name resolved: the catalogue is in front of her, so
    that is an invented or mistyped name, and re-reading the catalogue is the
    next act, not retrying the string. `unavailable` when there is no registry
    to load onto — distinct from `nothing_found` by conduct.md's own rule.
    """
    registry = getattr(agent, "tool_registry", None)
    if registry is None:
        return {"status": "unavailable", "loaded": [], "already_loaded": [],
                "unknown": [], "refused": []}
    wanted = list(dict.fromkeys(names))
    asked, refused = wanted[:LOAD_TOOLS_MAX_NAMES], wanted[LOAD_TOOLS_MAX_NAMES:]
    loaded: list[str] = []
    already: list[str] = []
    unknown: list[str] = []
    for name in asked:
        if name in registry.registry or name in registry.dynamic_tools:
            # A resident tool, or one an earlier call this turn loaded.
            # `register_dynamic_tool` raises on a duplicate, and asking twice
            # is a wasted round trip, not an error.
            already.append(name)
            continue
        tool = shelf.get(name)
        if tool is None:
            unknown.append(name)
            continue
        registry.register_dynamic_tool(tool)
        loaded.append(name)
    status = "ok" if loaded or already else "nothing_found"
    return {"status": status, "loaded": loaded, "already_loaded": already,
            "unknown": unknown, "refused": refused}
