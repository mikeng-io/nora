"""Building the shadow Cognition session for the composition root (migration
step 3a-iii, DEC-244). Everything the runner needs that is not itself the
runner: which read tools it holds, what it is told, its budget, and how it is
stopped.

`[cognition] session_mode = "off"` (the shipped default) builds NOTHING here:
`build_cognition_runner` is not called, the model source is not touched and
`nora.app.cognition_agent_runner` is not imported (it is imported inside the
function, so importing this module costs nothing). `"shadow"` builds exactly
one `CognitionSessionRunner` whose hook the wake evaluator is handed, and
`require_session_hook` refuses to boot if that did not happen: a session mode
that asked for sessions and built an evaluator that runs none would record
would-wakes for a week and look healthy.

**What she reads** (30-cognition.md §2, 15-map.md §4): the tools of the
design's read catalogue that EXIST and are read-only, less what a flag
withholds. `COGNITION_READ_CATALOGUE` is spelled out here rather than derived
from `read_only_tools()`, because the design names the catalogue and a session
that quietly gained every read tool the registry ever adds is a change nobody
decided; `read_surface` says which of the named tools are wired and which are
missing (a tool the design names and the tree does not have yet is a fact the
boot log states, never a silent gap). The live social reads (`instagram_*`,
`x_*`, `linkedin_*`) are not in it (20 §2: they stay on the tag path).

**Who they read through.** The read tools are built by the SAME
`RegistryToolSource` every other Dreamer adaptor reads through, under
`cognition_grant()` (a real room, no act verb), so lineage, the roster
disclosure guard and the record read's own room rules are the ones the tag
path already runs; this module builds no read of its own.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent.dreamer import (
    AdaptorGrant,
    DreamToolSource,
    cognition_grant,
)
from nora.domain.cognition_session import COGNITION_ADAPTOR, Room
from nora.domain.gatherings import Gathering
from nora.domain.ports import CognitionDryRunStore, StimulusStore
from nora.domain.standing import Standing

log = logging.getLogger("nora.app.cognition_wiring")

#: The Dreamer adaptor name (its budget key, its guard label, its trace name)
#: and the objective file the session's instruction is read from
#: (`nora/dreamer/objectives/cognition.md`).
COGNITION_ADAPTOR_NAME = COGNITION_ADAPTOR
COGNITION_OBJECTIVE = "cognition"

#: The read tools 30 §2 and 15 §4 name for a session. Some do not exist in the
#: tree yet (`read_surface` reports which); the rest are read-only rows of the
#: tool table.
COGNITION_READ_CATALOGUE: frozenset[str] = frozenset({
    # the warehouse queries (15 §4)
    "record_query", "rollup_read", "event_search", "memo_read", "character_read",
    "standing_read", "social_items",
    # the tiers' reads that already exist
    "where_been", "routine_read", "note_read", "note_search",
    # memory
    "about", "ask_memory", "search_memory", "search_memory_workspace",
})

#: The side-effect audit of every tool in the catalogue (#637 review, blocker;
#: `tests/unit/test_cognition_wiring.py` holds its keys equal to
#: `COGNITION_READ_CATALOGUE`, so a tool added to the catalogue without an
#: audited line fails the gate). «Shadow writes nothing live» is a claim about
#: EVERY tool the session holds, not about the write verbs it was built with:
#: the blocker was a READ tool (`note_search`) that upserted `note_candidate`
#: and bumped `nora_note_candidates_total{trigger="miss"}` on a miss, the
#: table the live note curator reads. `tests/infra/test_a_cognition_sessions_
#: read_tools_write_nothing.py` runs every wired tool through the real registry
#: against real Postgres and asserts no non-cognition table gained a row.
#:
#: Counters (`nora_tool_calls_total`, `nora_recall_total`, `nora_room_lookups_
#: total`) are observability, read by a person and never by a decision, and are
#: kept. A Honcho read verb only ever ROUTES (`HonchoClient._route` ensures the
#: unit's workspace marker and scope once per process, the identical idempotent
#: get-or-create the tag path performs) and answers; no message and no
#: conclusion is written.
READ_TOOL_AUDIT: Mapping[str, str] = {
    "record_query": "SQL read of messages, edits, reactions, deletes and the media "
                    "index (`MediaJoin.enrich`); counters only. No write.",
    "rollup_read": "not built yet; audit it when it lands (a Rollup read must not "
                   "trigger a rebuild).",
    "event_search": "SQL read of the unit's gatherings; counters only. No write.",
    "memo_read": "not built yet; audit it when it lands.",
    "character_read": "not built yet; audit it when it lands.",
    "standing_read": "not built yet; audit it when it lands (a Standing read must "
                     "not fold).",
    "social_items": "not built yet; audit it when it lands (an ingested-items read "
                    "must not fetch from the network).",
    "where_been": "SQL read of derived presence (stays, trips, places) and the "
                  "newest raw fix; counters only. No write, and no refresh of the "
                  "live fix (that is `where_is`, not held).",
    "routine_read": "SQL read of derived presence; counters only. No write.",
    "note_read": "SQL read of a note and its head; its CAS anchor is a closure "
                 "local to the build. No write.",
    "note_search": "SQL read of note bodies. ON A MISS it upserted `note_candidate` "
                   "and counted `nora_note_candidates_total{trigger=miss}`: "
                   "NEUTRALISED under `dream_adaptor == cognition` (no write, no "
                   "count).",
    "about": "Honcho `peer_card` + `representation` reads; counters only.",
    "ask_memory": "Honcho `chat` (dialectic reasoning, server side): a read verb; "
                  "what Honcho does inside it is not observable from this repo. "
                  "KEPT, flagged; counters only.",
    "search_memory": "Honcho session `search` read; counters only.",
    "search_memory_workspace": "Honcho workspace `search` read, narrowed to the "
                               "unit's scope; counters only.",
}

#: Tools withheld while their flag is off. `record_query` is offered only with
#: `[record_query] enabled` (the registry withholds it itself when it has no
#: Record read; naming it here keeps the boot log honest about it).
FLAGGED_READS: Mapping[str, str] = {"record_query": "record_query.enabled"}


@dataclass(frozen=True)
class ReadSurface:
    """What the session's read tools are, and what they are not.

    `wired`: in the catalogue, in the tool table, read-only, not withheld.
    `missing`: named by the design and absent from the tool table (or not
    read-only): the design's tool does not exist yet. `withheld`: exists, held
    back by a flag that is off."""

    wired: frozenset[str]
    missing: frozenset[str]
    withheld: frozenset[str]


def read_surface(*, read_only: frozenset[str], enabled_flags: Collection[str]) -> ReadSurface:
    """The catalogue against the registry's read-only set and the flags that
    are on (`enabled_flags` holds the FLAGGED_READS values that are true)."""
    present = COGNITION_READ_CATALOGUE & read_only
    withheld = frozenset(
        name for name, flag in FLAGGED_READS.items()
        if name in present and flag not in enabled_flags
    )
    return ReadSurface(
        wired=present - withheld,
        missing=COGNITION_READ_CATALOGUE - read_only,
        withheld=withheld,
    )


def read_tool_budgets(
    names: Collection[str], *, table: Mapping[str, int],
    overrides: Mapping[str, int] | None = None,
    adaptor_overrides: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Each read tool's per-pass call ceiling, three layers, widest first (the
    tool table, the deployment's `[llm.budgets]`, this adaptor's
    `[dreamer.budgets.cognition.tool_calls]`), the same order
    `nora.agent.dreamer._tool_budgets` applies. A tool with no table row is
    absent, and absent is default-deny in the guard."""
    out: dict[str, int] = {}
    for name in sorted(names):
        if name not in table:
            continue
        out[name] = (adaptor_overrides or {}).get(name, (overrides or {}).get(name, table[name]))
    return out


def check_tool_call_overrides(
    tool_calls: Mapping[str, int], *, held: Collection[str],
) -> None:
    """A per-tool ceiling for a tool the session does not hold is a boot
    failure, not a silent no-op (`tool_call_faults`' argument: the operator's
    raise would be inert and nothing would say so)."""
    unheld = sorted(set(tool_calls) - set(held))
    if unheld:
        raise ValueError(
            f"[dreamer.budgets.cognition.tool_calls] names tools the session does not "
            f"hold: {', '.join(unheld)}"
        )


def cognition_budget(b: Any) -> Any:
    """`[dreamer.budgets.cognition]` (a `DreamBudgetConfig`) as the session's
    own `AdaptorBudget`: the same numbers across the layer line, the two
    per-pass read ceilings included (`tests/unit/test_cognition_wiring.py`
    walks every field, so a field added to one side is not dropped)."""
    from nora.app.cognition_session import AdaptorBudget

    return AdaptorBudget(
        turns=b.turns, total_tokens=b.total_tokens, output_tokens=b.output_tokens,
        timeout_seconds=b.timeout_seconds, max_memory_calls=b.max_memory_calls,
        max_tool_calls=b.max_tool_calls,
    )


ReadTools = Callable[[str, Sequence[Room]], list[Any]]


def read_tools_for(
    source: DreamToolSource, *, names: frozenset[str],
    grant: AdaptorGrant | None = None,
) -> ReadTools:
    """`CognitionSessionRunner`'s `read_tools_for`: the catalogue's tools built
    for the agenda's rooms, through the registry's own tool source. A room is
    a scope key (the conversation id), so the source resolves the real room,
    its roster and its lineage exactly as it does for a scope-keyed adaptor; a
    room the policy does not name gets the closed shape, whose every answer is
    `unavailable`, never an empty answer read as «nothing there».

    ONE room: that room's tools, untouched. SEVERAL: each room's tools are
    built by the same source and laid under one tool per name that takes a
    `room` argument (`nora.agent.room_routing`), so each read resolves
    through the room it names; a `room` argument that is not on the agenda is
    refused. That binds WHICH room a read is resolved through (its roster and
    lineage), not which rooms' data a tool may reach: `record_query
    scope="all_rooms"` and `search_memory_workspace` read the unit's OTHER
    rooms too, because the unit is the read boundary (DEC-231). The unit's home
    room is never substituted for an item's room."""
    allowance = grant if grant is not None else cognition_grant()

    def build_for(room: Room) -> list[Any]:
        return source.build(
            key=room.conversation_id, names=names, unit=None,
            adaptor=COGNITION_ADAPTOR_NAME, grant=allowance,
        )

    def build(unit: str, rooms: Sequence[Room]) -> list[Any]:
        if len(rooms) == 1:
            return build_for(rooms[0])
        from nora.agent.room_routing import route_reads

        return route_reads({
            f"{room.platform}:{room.conversation_id}": build_for(room) for room in rooms
        })

    return build


@dataclass(frozen=True)
class AgendaSkills:
    """Skills injected per agenda kind (30 §4): after the doctrine, in a fixed
    order, so the prefix stays cached and only the skills vary. `by_kind` maps
    an agenda kind to a skill name; `resolve` reads a skill by name (`Skill.
    instructions`, `.sha256`). A kind with no entry injects nothing, and no
    skill ships for any kind in step 3a-iii (the replaced adaptors' objectives
    become the skills as each retires): the mechanism is here so the session
    row records what was injected, and the table is where each lands."""

    by_kind: Mapping[str, str]
    resolve: Callable[[str], Any]

    def _names(self, kinds: Sequence[str]) -> list[str]:
        return sorted({self.by_kind[k] for k in kinds if k in self.by_kind})

    def text_for(self, kinds: Sequence[str]) -> str:
        parts = []
        for name in self._names(kinds):
            skill = self.resolve(name)
            if skill is None:
                raise LookupError(f"agenda skill {name!r} is not installed")
            parts.append(f"\n\n# Skill: {name}\n\n{skill.instructions}\n")
        return "".join(parts)

    def versions_for(self, kinds: Sequence[str]) -> dict[str, object]:
        out: dict[str, object] = {}
        for name in self._names(kinds):
            skill = self.resolve(name)
            if skill is None:
                raise LookupError(f"agenda skill {name!r} is not installed")
            out[name] = skill.sha256
        return out


class CognitionShutdown:
    """Stops the sessions at shutdown, once. The evaluator's background tasks
    ARE the sessions' tasks, so `cancel_in_flight` and the runner's `shutdown`
    both cancel the same tasks: each is called (the design's open choice (a))
    and the second call of the whole thing is a no-op, so a process that
    reaches shutdown twice does not cancel twice."""

    def __init__(self, *, evaluator: Any, runner: Any) -> None:
        self._evaluator = evaluator
        self._runner = runner
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def __call__(self) -> None:
        if self._done:
            return
        self._done = True
        await self._evaluator.cancel_in_flight()
        await self._runner.shutdown()


def require_session_hook(
    *, session_mode: str, runner: object | None, evaluator: object | None,
    session_units: Collection[str] | None = None,
) -> None:
    """The boot check (#627 review, minor 4; #637 review, minor 1):
    `session_mode = "shadow"` built a runner AND an evaluator that holds its
    hook AND at least one unit a session can run for (`session_units`, the
    units with a home room; `None` skips that half). Raises `RuntimeError` (the
    composition root turns it into its `StartupError`) rather than run a
    shadow week that records would-wakes and starts no session."""
    if session_mode != "shadow":
        return
    if session_units is not None and not session_units:
        raise RuntimeError(
            "[cognition] session_mode = \"shadow\" but no unit can run a session: "
            "none names a `home_room = true` room in policy.toml, so every wake "
            "would be recorded and no session run",
        )
    if runner is None:
        raise RuntimeError(
            "[cognition] session_mode = \"shadow\" but no session runner was built",
        )
    if evaluator is None or not getattr(evaluator, "has_wake_hook", False):
        raise RuntimeError(
            "[cognition] session_mode = \"shadow\" but the wake evaluator holds no "
            "session hook, so would-wakes would be recorded and no session run",
        )


def _by_home_room(
    homes: Mapping[str, Any],
) -> dict[str, Room]:
    """Unit -> its home room, from `Policy.home_rooms()` (39 D2)."""
    return {unit: Room(conv.platform, conv.primary_id) for unit, conv in homes.items()}


def units_with_home_rooms(
    units: Sequence[str], homes: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """`(can_run_sessions, cannot)`: a unit with no home room cannot start a
    session (the runner refuses it at startup, `check_home_rooms`). It stays in
    the wake evaluator, which keeps recording its would-wakes as it does with
    sessions off (they are the shadow week's evidence for the unit), and is
    named, loudly, at boot and on a gauge rather than failing every wake or
    vanishing from the record."""
    wakeable = [u for u in units if u in homes]
    return wakeable, [u for u in units if u not in homes]


def build_cognition_runner(
    *,
    cfg: Any,
    models: Any,
    retry: Any,
    metrics: Any,
    tools: DreamToolSource,
    instructions: Any,
    registry_table: Mapping[str, int],
    dry_run_store: CognitionDryRunStore,
    stimulus_store: StimulusStore,
    note_titles: Callable[[str], Awaitable[Sequence[str]]],
    proposed_events_in: Callable[[str], Awaitable[Sequence[Gathering]]],
    standing_of: Callable[[str, str], Awaitable[Standing | None]],
    homes: Mapping[str, Any],
    units: Sequence[str],
    skills: AgendaSkills,
    grounder: Any,
    render_budget: Any,
    surface: ReadSurface,
    clock: Callable[[], float],
    model_id: str,
    model_route: str,
    budget: Any,
    tool_calls: Mapping[str, int],
    llm_budgets: Mapping[str, int],
    result_caps: Mapping[str, int],
    memory_tools: frozenset[str],
) -> Any:
    """The shadow runner. Imported lazily: `LiveCognitionAgentRunner` (and
    with it Strands) is loaded only when a session will actually run."""
    from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
    from nora.app.cognition_session import CognitionSessionRunner

    check_tool_call_overrides(tool_calls, held=surface.wired)
    budgets = read_tool_budgets(
        surface.wired, table=registry_table, overrides=llm_budgets,
        adaptor_overrides=tool_calls,
    )
    agent_runner = LiveCognitionAgentRunner(
        models=models, retry=retry, metrics=metrics,
        read_tool_budgets=budgets,
        # The READ tools' argument names only. The registry's table also holds
        # the tag path's `note_create`, `note_update` and `remind`, whose
        # arguments differ from the session's own verbs of the same names
        # (`room`, `at_ms`, ...), and R-99 would veto every call to them.
        # Plus `room`: the routed tools of an agenda spanning several rooms
        # take it (`nora.agent.room_routing`), and R-99 would veto an
        # argument the table does not list.
        param_names={
            n: frozenset(names) | {"room"}
            for n, names in tools.tool_param_names().items() if n in surface.wired
        },
        result_caps=result_caps, memory_tools=memory_tools,
    )

    def doctrine() -> tuple[str, str | None]:
        loaded = instructions.for_objective(COGNITION_OBJECTIVE)
        return loaded.text, loaded.sha256

    # Read once here so a missing or malformed objective is a startup failure
    # with its file named, not the first wake's (`DreamerInstructions` raises).
    text, sha = doctrine()
    home_rooms = _by_home_room(homes)

    async def pending_reminds(unit: str) -> Any:
        return await dry_run_store.pending_mirrored_reminds(
            unit=unit, now_ms=int(clock() * 1000),
        )

    async def open_events(unit: str, conversation_id: str) -> Sequence[Gathering]:
        return await proposed_events_in(conversation_id)

    async def prior(platform: str, conversation_id: str) -> Standing | None:
        return await standing_of(platform, conversation_id)

    async def self_wakes(unit: str, since_ms: int) -> Any:
        return await dry_run_store.pending_self_wakes(unit=unit, since_ms=since_ms)

    return CognitionSessionRunner(
        dry_run_store=dry_run_store, stimulus_store=stimulus_store,
        agent_runner=agent_runner, home_rooms=home_rooms,
        read_tools_for=read_tools_for(tools, names=surface.wired),
        read_only_tools=surface.wired,
        skill_text_for=skills.text_for, skill_versions_for=skills.versions_for,
        grounder=grounder, render_budget=render_budget,
        doctrine_text=text, doctrine_sha256=sha, doctrine_source=doctrine,
        open_note_titles=note_titles, open_proposed_events=open_events,
        prior_standing=prior, pending_self_wakes=self_wakes,
        pending_mirrored_reminds=pending_reminds, session_mode="shadow",
        budget=budget, cooldown_seconds=cfg.cognition.cooldown_seconds,
        units=list(units), session_timeout_seconds=cfg.cognition.session_timeout_seconds,
        max_items=cfg.cognition.max_items, max_strikes=cfg.cognition.max_strikes,
        max_self_wakes=cfg.cognition.max_self_wakes,
        max_self_wakes_per_day=cfg.cognition.max_self_wakes_per_day,
        model_id=model_id, model_route=model_route, metrics=metrics, clock=clock,
        tz=ZoneInfo(cfg.cognition.timezone),
    )


__all__ = [
    "COGNITION_ADAPTOR_NAME", "COGNITION_OBJECTIVE", "COGNITION_READ_CATALOGUE",
    "AgendaSkills", "CognitionShutdown", "ReadSurface", "build_cognition_runner",
    "cognition_budget", "read_surface", "read_tool_budgets", "read_tools_for",
    "require_session_hook", "units_with_home_rooms",
]
