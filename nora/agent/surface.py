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
#:
#: `rollup_read` supersedes `where_been`'s 90-day cap (25 §5), and is also a
#: tag-path tool (`TAG_READABLE_LOOP_TOOLS`, below). `standing_read` shows her
#: own held position to the session that revises it (30 §2). `character_read`
#: (self-growth C4) shows the unit's accounts; it joins the tag path only after
#: a `tool_selection` run (`nora-tool`). Neither of those two is a tag-path
#: tool, so a reply neither pays their prompt nor can route to them, and the
#: Relevance Gate's capability digest does not offer them as something she can
#: serve (`ToolRegistry.capability_digest`).
DREAMER_LOOP_TOOLS: frozenset[str] = frozenset(
    {"rollup_read", "standing_read", "character_read"})

#: Cognition-loop tools a person's TAG turn may also call (unified-memory step 0,
#: 2026-10-15). `rollup_read` joins the tag path because «where have we been
#: together lately» had no other way to reach the outing rows: the recall
#: journeys rebuilt them from four transcript loads and placed an older outing in
#: the asked week. The read is the same SELECT under the same lineage for the
#: room the tool is bound to; nothing here widens what a row may show. Not a fire
#: turn's and not an adaptor's (`dreamer.py` still refuses every
#: `DREAMER_LOOP_TOOLS` name an adaptor declares).
TAG_READABLE_LOOP_TOOLS: frozenset[str] = frozenset({"rollup_read"})

DREAMER_ONLY_TOOLS: frozenset[str] = frozenset({"search_memory_workspace"}) | (
    DREAMER_LOOP_TOOLS - TAG_READABLE_LOOP_TOOLS)

#: Tag-turn tools only (design/subagent §3): the four `subagent_*` tools are a person's
#: tool for handing work to a background worker, and `video_create` starts a clip delivered
#: to the person who asked, so a fire turn, a Dreamer pass, a cognition
#: session and the worker itself (which would recurse) are never offered them.
TAG_ONLY_TOOLS: frozenset[str] = frozenset({
    "subagent_create", "subagent_list", "subagent_read", "subagent_cancel",
    # design/video-generation: a clip is delivered to the person who asked, in their room.
    "video_create",
})


#: The sub-agent worker's own tools (design/subagent §4d): offered to a worker and to nothing
#: else. The mirror of `TAG_ONLY_TOOLS`: a tag turn must not hold the worker's checkpoint, and
#: no occasion but a worker's may.
WORKER_ONLY_TOOLS: frozenset[str] = frozenset({"checkpoint_update"})


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

    `tag_turn` is False for every occasion that is not a person's tag: a fire turn, a Dreamer
    adaptor, and the sub-agent worker. `TAG_ONLY_TOOLS` are refused there. `worker` is True
    only for the sub-agent worker, the one occasion `WORKER_ONLY_TOOLS` are admitted to.
    """

    platform: str | None
    dreamer: bool = False
    grant: frozenset[str] | None = None
    tag_turn: bool = True
    worker: bool = False

    def admits(self, name: str) -> bool:
        if self.grant is not None and name not in self.grant:
            return False
        if name in TAG_ONLY_TOOLS and not self.tag_turn:
            return False
        if name in WORKER_ONLY_TOOLS and not self.worker:
            return False
        if self.platform is None:
            return True
        if name in DISCORD_ONLY_TOOLS and self.platform != "discord":
            return False
        if name in TAG_READABLE_LOOP_TOOLS:
            return self.dreamer or (self.tag_turn and not self.worker)
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
        hooks: Iterable[Any] = (),
    ) -> None:
        self.scope = scope
        self.tools = list(tools)
        self.resident = frozenset(resident)
        self.shelf = shelf
        self.admitted = frozenset(admitted)
        self._render = render
        #: Hook providers the agent this surface is handed to must register —
        #: `ShelfLoader` when anything is shelved, nothing otherwise. Opaque
        #: here (this module imports no `strands`); built by the registry.
        self.hooks = tuple(hooks)

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


#: How to report each `load_tools` outcome (ADR 0015 §3). A non-ok status always
#: carries its note; an `ok` carries one clause per list that is non-empty, so a
#: clean load stays the bare result.
LOAD_TOOLS_UNAVAILABLE_NOTE = (
    "Loading isn't possible right now. Say so rather than guessing what the tool "
    "would have said."
)
_LOAD_NOTHING_FOUND_NOTE = (
    "None of those names is in your catalogue. Read it again rather than "
    "retrying the same names."
)


def _load_ok_note(unknown: list[str], refused: list[str]) -> str:
    parts = []
    if unknown:
        parts.append(
            "The unknown names are not in your catalogue: read it again rather "
            "than retrying them."
        )
    if refused:
        parts.append(
            f"The refused names were over the limit of {LOAD_TOOLS_MAX_NAMES}: "
            "ask for those in your next call."
        )
    return " ".join(parts)


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
                "unknown": [], "refused": [], "note": LOAD_TOOLS_UNAVAILABLE_NOTE}
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
    result: dict = {"status": status, "loaded": loaded, "already_loaded": already,
                    "unknown": unknown, "refused": refused}
    note = (_load_ok_note(unknown, refused) if status == "ok"
            else _LOAD_NOTHING_FOUND_NOTE)
    if note:
        result["note"] = note
    return result
