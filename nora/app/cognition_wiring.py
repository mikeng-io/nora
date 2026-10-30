"""Building the Cognition session for the composition root (DEC-244).
Everything the runner needs that is not itself the runner: which read tools it
holds, what it is told, its budget, and how it is stopped.

`[cognition] enabled = false` (the shipped default) builds NOTHING here:
`build_cognition_runner` is not called, the model source is not touched and
`nora.app.cognition_agent_runner` is not imported (it is imported inside the
function, so importing this module costs nothing). `true` builds exactly one
`CognitionSessionRunner` whose hook the wake evaluator is handed, and
`require_session_hook` refuses to boot if that did not happen.

**What she reads**: the tools of the read catalogue that EXIST and are
read-only, less what a flag withholds. `COGNITION_READ_CATALOGUE` is spelled out
here rather than derived from `read_only_tools()`, because a session that
quietly gained every read tool the registry ever adds is a change nobody decided;
`read_surface` says which of the named tools are wired and which are missing (a
named tool the tree does not have is a fact the boot log states, never a silent
gap). The live social reads (`instagram_*`, `x_*`, `linkedin_*`) are not in it:
they stay on the tag path.

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
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent.dreamer import (
    AdaptorGrant,
    DreamToolSource,
    cognition_grant,
)
from nora.domain.cognition_session import COGNITION_ADAPTOR, Room
from nora.domain.gatherings import (
    OPEN_STATUSES,
    Gathering,
    GatheringStore,
    event_in_scope,
)
from nora.domain.live import LiveMapping
from nora.domain.note_body import NORA_NAMES
from nora.domain.ports import CognitionStore, StimulusStore
from nora.domain.reminders import ReminderCaps
from nora.domain.standing import StandingHead

log = logging.getLogger("nora.app.cognition_wiring")

#: The Dreamer adaptor name (its budget key, its guard label, its trace name)
#: and the objective file the session's instruction is read from
#: (`nora/dreamer/objectives/cognition.md`).
COGNITION_ADAPTOR_NAME = COGNITION_ADAPTOR
COGNITION_OBJECTIVE = "cognition"

#: The read tools named for a session. Some do not exist in the
#: tree yet (`read_surface` reports which); the rest are read-only rows of the
#: tool table. `social_read` is the map's `social_items(person, interval)`
#: under ADR 0015 §1's `<domain>_<verb>` name.
COGNITION_READ_CATALOGUE: frozenset[str] = frozenset({
    # the warehouse queries
    "record_query", "rollup_read", "event_search", "memo_read", "character_read",
    "standing_read", "social_read",
    # the tiers' reads that already exist
    "where_been", "routine_read", "note_read", "note_search",
    # memory
    "about", "ask_memory", "search_memory", "search_memory_workspace",
    # the graph surface's (DEC-316): built instead of the Honcho memory verbs
    # above while `[memory.index] read`, never beside them
    "memory_search", "memory_read",
})

#: The side-effect audit of every tool in the catalogue
#: (`tests/unit/test_cognition_wiring.py` holds its keys equal to
#: `COGNITION_READ_CATALOGUE`, so a tool added to the catalogue without an
#: audited line fails the gate): a READ tool writes nothing, so a session's
#: writes are only its own verbs'.
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
    "rollup_read": "SQL SELECT of the CURRENT rollup build's rows (`rollup_build` "
                   "status=current) and derived places; counters only. It starts, "
                   "advances and switches no build, and folds nothing. No write.",
    "event_search": "SQL read of the unit's gatherings; counters only. No write.",
    "memo_read": "SQL SELECT of one memo of the unit and its revisions "
                 "(`SqlMemoStore.get`, `revisions`); counters only. No write.",
    "character_read": "SQL SELECT of `culture` heads (`CultureReader.head`) of the "
                      "unit's own rooms; counters only. It folds and revises nothing. "
                      "No write.",
    "standing_read": "SQL SELECT of one `standing` head and the newest "
                     "`standing_revision` row (its id only); counters only. It folds, "
                     "revises and migrates nothing. No write.",
    "social_read": "SQL SELECT of one person's `social_item` rows and their media's "
                   "`media_objects` readings (`SqlSocialStreamStore.items_of`); "
                   "counters only. It fetches nothing from the network and "
                   "perceives nothing. No write.",
    "where_been": "SQL read of derived presence (stays, trips, places) and the "
                  "newest raw fix; counters only. No write, and no refresh of the "
                  "live fix (that is `where_is`, not held).",
    "routine_read": "SQL read of derived presence; counters only. No write.",
    "note_read": "SQL read of a note and its head; its CAS anchor is a closure "
                 "local to the build. No write.",
    "note_search": "SQL read of note bodies; counters only. No write.",
    "about": "Honcho `peer_card` + `representation` reads; counters only.",
    "ask_memory": "Honcho `chat` (dialectic reasoning, server side): a read verb; "
                  "what Honcho does inside it is not observable from this repo. "
                  "KEPT, flagged; counters only.",
    "search_memory": "Honcho session `search` read; counters only.",
    "search_memory_workspace": "Honcho workspace `search` read, narrowed to the "
                               "unit's scope; counters only.",
    "memory_read": "Scoped SQL graph, node fields and evidence reads; counters only. No write.",
    "memory_search": "SQL reads of the memory index and graph (`SqlMemoryIndex`, which "
                     "writes nothing) and one query embedding; counters only. "
                     "No write.",
}

#: Tools withheld while their flag is off. `record_query` is offered only with
#: `[record_query] enabled` (the registry withholds it itself when it has no
#: Record read; `character_read` likewise under `[culture] enabled`; naming
#: them here keeps the boot log honest about them).
FLAGGED_READS: Mapping[str, str] = {
    "record_query": "record_query.enabled",
    "character_read": "culture.enabled",
}


@dataclass(frozen=True)
class ReadSurface:
    """What the session's read tools are, and what they are not.

    `wired`: in the catalogue, in the tool table, read-only, not withheld.
    `missing`: named by the design and absent from the tool table (or not
    read-only): the design's tool does not exist yet. `withheld`: exists, held
    back by a flag that is off, or not offered by the registry (the memory
    surface it did not select, DEC-316)."""

    wired: frozenset[str]
    missing: frozenset[str]
    withheld: frozenset[str]


def read_surface(
    *, read_only: frozenset[str], enabled_flags: Collection[str],
    registry_withheld: frozenset[str],
) -> ReadSurface:
    """The catalogue against the registry's read-only set, the flags that
    are on (`enabled_flags` holds the FLAGGED_READS values that are true),
    and what the registry itself does not offer (`ToolRegistry.
    withheld_tools()`). REQUIRED, so the memory surface (DEC-316) has one
    source, the registry that builds the tools: a caller cannot leave it out
    and have the catalogue claim the Honcho verbs with the graph's built."""
    present = COGNITION_READ_CATALOGUE & read_only
    withheld = frozenset(
        name for name, flag in FLAGGED_READS.items()
        if name in present and flag not in enabled_flags
    ) | (present & registry_withheld)
    return ReadSurface(
        wired=present - withheld,
        missing=COGNITION_READ_CATALOGUE - read_only,
        withheld=withheld,
    )


def cognition_budget(b: Any) -> Any:
    """`[dreamer.budgets.cognition]` (a `DreamBudgetConfig`) as the session's
    own `AdaptorBudget`: rounds, tokens, output tokens and the wall clock, and
    nothing else. The session has no per-tool, memory or pass-wide call
    ceiling (DEC-247); the config refuses a block that sets one, so no field
    is dropped here in silence (`tests/unit/test_cognition_wiring.py` walks
    every field)."""
    from nora.app.cognition_session import AdaptorBudget

    return AdaptorBudget(
        turns=b.turns, total_tokens=b.total_tokens, output_tokens=b.output_tokens,
        timeout_seconds=b.timeout_seconds,
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


class SkillNotInstalledError(LookupError):
    """A skill the agenda table maps is not in the corpus. At startup the
    composition turns it into a `StartupError`; on a live hot reload that deleted
    or renamed the file, the session that needed it records a `failed` wake
    (cause `infra`, so the unit backs off and no message is struck) and never
    runs without it."""


class PreludeMissingError(RuntimeError):
    """The `cognition` objective resolved WITHOUT its prelude: the file is gone,
    or its `applies_to` no longer names `cognition`. The loader serves the
    shared Dreamer doctrine to an objective that has no prelude, and that text
    says «Nobody is waiting for you… You look; you do not act», which is false
    of a session whose verbs write the notebook. Raised at startup, and again at
    the start of every session, so a hot reload cannot swap it in unseen."""


@dataclass(frozen=True)
class AgendaSkills:
    """Skills injected per agenda kind: after the doctrine, in a fixed
    order, so the prefix stays cached and only the skills vary. `by_kind` maps
    an agenda kind to a skill name; `resolve` reads a skill by name (`Skill.
    instructions`, `.sha256`) and is NOT memoised, so an edit on the bind mount
    is live at the next session. A kind with no entry injects nothing. The
    production table is `AGENDA_SKILLS`, built by `agenda_skills`."""

    by_kind: Mapping[str, str]
    resolve: Callable[[str], Any]

    def _names(self, kinds: Sequence[str]) -> list[str]:
        return sorted({self.by_kind[k] for k in kinds if k in self.by_kind})

    def resolved_for(self, kinds: Sequence[str]) -> tuple[str, dict[str, object]]:
        """The text injected and the versions recorded for these kinds, from ONE
        resolution of each skill: the hash a session row records is over the text
        the model reads, even when the corpus hot-reloads while the session is
        being set up."""
        text: list[str] = []
        versions: dict[str, object] = {}
        for name in self._names(kinds):
            skill = self.resolve(name)
            if skill is None:
                raise SkillNotInstalledError(f"agenda skill {name!r} is not installed")
            text.append(f"\n\n# Skill: {name}\n\n{skill.instructions}\n")
            versions[name] = skill.sha256
        return "".join(text), versions

    def text_for(self, kinds: Sequence[str]) -> str:
        return self.resolved_for(kinds)[0]

    def versions_for(self, kinds: Sequence[str]) -> dict[str, object]:
        return self.resolved_for(kinds)[1]


#: The prelude `cognition` reads in place of the shared Dreamer doctrine
#: (`nora/dreamer/preludes/`); the startup check and every session hold the
#: objective's resolved versions to name it.
COGNITION_PRELUDE = "cognition-prelude"

#: The Register skill: the note, event and reminder judgement, in
#: `nora/dreamer/skills/`.
REGISTER_SKILL = "cognition-register"

#: Agenda kind -> the skill injected for it. ONE skill for every mapped kind, so
#: every session that has an agenda reads the same system prompt and its prefix
#: stays cached whichever kinds its agenda holds
#: (`tests/unit/test_cognition_agenda_skills.py`). `time` is mapped: the daily
#: floors wake a session that reviews the notebook (`note_set_status`, `note_merge`)
#: and decides what to raise. `social` and `play` have no producer.
AGENDA_SKILLS: Mapping[str, str] = MappingProxyType({
    "message": REGISTER_SKILL, "rollup": REGISTER_SKILL, "self": REGISTER_SKILL,
    "time": REGISTER_SKILL,
})


def floors_ready() -> bool:
    """True once a skill ships for the `time` agenda kind: a floor then wakes a
    session. Read at CALL time over `AGENDA_SKILLS`, so the composition root (through
    the `AgendaSkills` it builds) and the wake evaluator's age gauge follow the ONE
    table, and a test that replaces it reaches both."""
    return "time" in AGENDA_SKILLS


def agenda_skills(instructions: Any) -> AgendaSkills:
    """The production table over the Dreamer's instruction store: each skill is
    read through it per session, so an edit on the bind mount is live at the
    next session and every load is in the corpus's revision history."""
    return AgendaSkills(by_kind=AGENDA_SKILLS, resolve=instructions.skill)


#: How long a stopping process lets a session that is already running finish
#: before it cancels it. A session is a model loop of minutes to an hour, so the
#: grace is for one in its last moments, not for waiting out one that is not:
#: whatever is still running at the deadline is cancelled, records its wake
#: `cancelled` (a restart) and releases its unit. `docker-compose.yml`'s
#: `stop_grace_period` for the `nora` service must exceed this by the time the
#: rest of shutdown needs (adapters, the cancel's own database write, closing
#: the client and the store): `tests/architecture/test_the_stop_grace_covers_the_shutdown.py`
#: holds the margin.
SESSION_SHUTDOWN_GRACE_SECONDS = 30.0


class CognitionShutdown:
    """Stops the sessions at shutdown, once. The evaluator's background tasks
    ARE the sessions' tasks, so `cancel_in_flight` and the runner's `shutdown`
    both cancel the same tasks: each is called and the second call of the whole
    thing is a no-op, so a process that reaches shutdown twice does not cancel
    twice.

    The wake loop is already stopped by the time this runs (`App.run` cancels
    every background task before `App.shutdown`), so no NEW wake starts while
    the sessions already running are given `grace_seconds` to finish."""

    def __init__(self, *, evaluator: Any, runner: Any, grace_seconds: float = 0.0) -> None:
        self._evaluator = evaluator
        self._runner = runner
        self._grace = grace_seconds
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def __call__(self) -> None:
        if self._done:
            return
        self._done = True
        await self._evaluator.wait_in_flight(self._grace)
        await self._evaluator.cancel_in_flight()
        await self._runner.shutdown()


def require_session_hook(
    *, enabled: bool, runner: object | None, evaluator: object | None,
    session_units: Collection[str] | None = None,
) -> None:
    """The boot check: `[cognition] enabled` built a runner AND an evaluator
    that holds its hook AND at least one unit a session can run for
    (`session_units`, the units with a home room; `None` skips that half).
    Raises `RuntimeError` (the composition root turns it into its
    `StartupError`) rather than boot a loop that wakes and runs nothing."""
    if not enabled:
        return
    if session_units is not None and not session_units:
        raise RuntimeError(
            "[cognition] enabled but no unit can run a session: none names a "
            "`home_room = true` room in policy.toml",
        )
    if runner is None:
        raise RuntimeError("[cognition] enabled but no session runner was built")
    if evaluator is None or not getattr(evaluator, "has_wake_hook", False):
        raise RuntimeError(
            "[cognition] enabled but the wake evaluator holds no session hook",
        )


def _by_home_room(
    homes: Mapping[str, Any],
) -> dict[str, Room]:
    """Unit -> its home room, from `Policy.home_rooms()`."""
    return {unit: Room(conv.platform, conv.primary_id) for unit, conv in homes.items()}


def units_with_home_rooms(
    units: Sequence[str], homes: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """`(can_run_sessions, cannot)`: a unit with no home room cannot start a
    session (the runner refuses it at startup, `check_home_rooms`), so the
    evaluator does not wake it; it is named, loudly, at boot and on a gauge."""
    wakeable = [u for u in units if u in homes]
    return wakeable, [u for u in units if u not in homes]


def open_events_reader(
    store: GatheringStore,
) -> Callable[[str, str, str], Awaitable[Sequence[Gathering]]]:
    """A room's events a session may still move, and is shown: every status
    before `happened` (`OPEN_STATUSES`: staged, proposed, confirmed), so a
    confirmed event can reach `happened` or `cancelled`. The unit AND platform
    are passed with the room because a conversation id alone is only unique
    within one unit's and one platform's id space.

    The read is scoped by the row's persisted owner (`event_in_scope`); a row
    with no stored platform is quarantined, never attributed from the current
    `policy.toml` assignment."""
    async def read(unit: str, platform: str, conversation_id: str) -> Sequence[Gathering]:
        return [
            e for e in await store.list_for_conversation(
                conversation_id=conversation_id, unit_id=unit, platform=platform,
            )
            if e.status in OPEN_STATUSES
            and event_in_scope(
                e, unit_id=unit, conversation_id=conversation_id, platform=platform,
            )
        ]
    return read


def upcoming_reader(
    read_events: Callable[[str, str, str], Awaitable[Sequence[Gathering]]],
    unit_rooms: Callable[[str], Collection[tuple[str, str, Collection[str]]]],
    *, days: float,
    room_proactive: Callable[[str, str], bool] = lambda platform, conversation_id: True,
) -> Callable[[str, int], Awaitable[Sequence[tuple[str, str]]]]:
    """The wake evaluator's `upcoming` seam (DEC-303): the unit's rooms holding
    an event a session may still move (`open_events_reader`) whose start falls
    between now and `days` ahead.

    `unit_rooms(unit)` gives each room as `(platform, primary_id, id forms)`:
    events are read under every id form the room has had, and the room is
    named ONCE, by the id a send goes to (a retired form fails to send,
    `Conversation.primary_id`). A room Nora may not reach unasked
    (`room_proactive` false) is skipped: the look ahead exists for a reminder
    `remind` would refuse there."""
    horizon_ms = int(days * 86_400_000)

    async def read(unit: str, now_ms: int) -> Sequence[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for platform, primary_id, forms in sorted(unit_rooms(unit), key=lambda r: r[:2]):
            if not room_proactive(platform, primary_id):
                continue
            for conversation_id in sorted({primary_id, *forms}):
                starts = [
                    int(e.when.timestamp() * 1000)
                    for e in await read_events(unit, platform, conversation_id)
                    if e.when is not None
                ]
                if any(now_ms <= at <= now_ms + horizon_ms for at in starts):
                    found.append((platform, primary_id))
                    break
        return found
    return read


def build_cognition_runner(
    *,
    cfg: Any,
    tag_trace_store: Any = None, memory: Any = None,
    models: Any,
    retry: Any,
    metrics: Any,
    tools: DreamToolSource,
    instructions: Any,
    store: CognitionStore,
    stimulus_store: StimulusStore,
    note_titles: Callable[[str], Awaitable[Sequence[str]]],
    notes: Any,
    events: Any,
    standing: Any,
    reminders: Any,
    ai_peer: str,
    room_proactive: Callable[[str, str], bool],
    reminder_caps: ReminderCaps,
    open_events_in: Callable[[str, str, str], Awaitable[Sequence[Gathering]]],
    standing_of: Callable[[str, str], Awaitable[StandingHead | None]],
    roster_of: Callable[[str, str], Awaitable[Mapping[str, str] | None]] | None = None,
    nora_names: frozenset[str] = NORA_NAMES,
    homes: Mapping[str, Any],
    units: Sequence[str],
    skills: AgendaSkills,
    grounder: Any,
    person_known: Callable[[str], bool] | None = None,
    render_budget: Any,
    surface: ReadSurface,
    clock: Callable[[], float],
    model_id: str,
    model_route: str,
    budget: Any,
    result_caps: Mapping[str, int],
    memos: Any = None,
    ledger: Any = None,
    shared: Any = None,
    rooms_of_unit: Callable[[str], Sequence[tuple[str, str]]] | None = None,
    resolve_row: Callable[[str, int], Awaitable[tuple[str, str] | None]] | None = None,
) -> Any:
    """The session runner. Imported lazily: `LiveCognitionAgentRunner` (and
    with it Strands) is loaded only when a session will actually run."""
    from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
    from nora.app.cognition_session import CognitionSessionRunner

    agent_runner = LiveCognitionAgentRunner(
        models=models, retry=retry, metrics=metrics,
        # The READ tools' argument names only. The registry's table also holds
        # the tag path's `note_create`, `note_revise` and `remind`, whose
        # arguments differ from the session's own verbs of the same names
        # (`room`, `at`, ...), and R-99 would veto every call to them.
        # Plus `room`: the routed tools of an agenda spanning several rooms
        # take it (`nora.agent.room_routing`), and R-99 would veto an
        # argument the table does not list.
        param_names={
            n: frozenset(names) | {"room"}
            for n, names in tools.tool_param_names().items() if n in surface.wired
        },
        result_caps=result_caps,
    )

    def doctrine() -> tuple[str, str | None]:
        loaded = instructions.for_objective(COGNITION_OBJECTIVE)
        # A prelude that is gone, or whose `applies_to` no longer names this
        # objective, is NOT an error to the loader: the objective then reads the
        # shared doctrine (a judge's «you look; you do not act»). Refused here,
        # at startup and at the start of every session, with its name.
        if COGNITION_PRELUDE not in loaded.versions:
            raise PreludeMissingError(
                f"the {COGNITION_OBJECTIVE!r} objective resolved without its prelude "
                f"{COGNITION_PRELUDE!r} (resolved: {', '.join(sorted(loaded.versions))}): "
                "it would read the shared Dreamer doctrine, which says a judge looks and "
                "does not act"
            )
        return loaded.text, loaded.sha256

    # Read once here so a missing or malformed objective, a missing prelude, or a
    # mapped skill that is not installed, is a startup failure with its name, not
    # the first wake's (`DreamerInstructions` raises `InstructionError`,
    # `doctrine` raises `PreludeMissingError`, `AgendaSkills` raises
    # `SkillNotInstalledError`; the composition turns all three into a
    # `StartupError`).
    text, sha = doctrine()
    skills.resolved_for(sorted(skills.by_kind))
    home_rooms = LiveMapping(lambda: _by_home_room(homes))

    async def open_events(
        unit: str, platform: str, conversation_id: str,
    ) -> Sequence[Gathering]:
        return await open_events_in(unit, platform, conversation_id)

    async def prior(platform: str, conversation_id: str) -> StandingHead | None:
        return await standing_of(platform, conversation_id)

    return CognitionSessionRunner(
        tag_trace_store=tag_trace_store, memory=memory,
        store=store, stimulus_store=stimulus_store,
        notes=notes, events=events, standing=standing, reminders=reminders,
        ledger=ledger, shared=shared,
        ai_peer=ai_peer, memos=memos,
        agent_runner=agent_runner, home_rooms=home_rooms,
        read_tools_for=read_tools_for(tools, names=surface.wired),
        read_only_tools=surface.wired,
        skill_text_for=skills.text_for, skill_versions_for=skills.versions_for,
        skills_for=skills.resolved_for, grounder=grounder, render_budget=render_budget,
        person_known=person_known,
        doctrine_text=text, doctrine_sha256=sha, doctrine_source=doctrine,
        open_note_titles=note_titles, open_events=open_events,
        prior_standing=prior,
        rooms_of_unit=rooms_of_unit,
        resolve_row=resolve_row,
        budget=budget, cooldown_seconds=cfg.cognition.cooldown_seconds,
        units=list(units), session_timeout_seconds=cfg.cognition.session_timeout_seconds,
        max_items=cfg.cognition.max_items, max_strikes=cfg.cognition.max_strikes,
        max_self_wakes=cfg.cognition.max_self_wakes,
        max_self_wakes_per_day=cfg.cognition.max_self_wakes_per_day,
        room_proactive=room_proactive,
        # The tag `remind` tool's own limits (`ReminderCaps`, from `[reminders]`),
        # never a second set.
        max_pending_per_requester=reminder_caps.max_pending_per_requester,
        remind_min_lead_seconds=reminder_caps.min_lead_seconds,
        remind_max_horizon_days=reminder_caps.max_horizon_days,
        model_id=model_id, model_route=model_route, metrics=metrics, clock=clock,
        tz=ZoneInfo(cfg.cognition.timezone),
        model_window_tokens=cfg.cognition.model_window_tokens,
        overflow_window_fraction=cfg.cognition.overflow_window_fraction,
        roster_of=roster_of, nora_names=nora_names,
        content_checks=cfg.content_checks.enabled,
    )


__all__ = [
    "AGENDA_SKILLS",
    "COGNITION_ADAPTOR_NAME",
    "COGNITION_OBJECTIVE",
    "COGNITION_PRELUDE",
    "COGNITION_READ_CATALOGUE",
    "REGISTER_SKILL",
    "AgendaSkills",
    "CognitionShutdown",
    "PreludeMissingError",
    "ReadSurface",
    "SkillNotInstalledError",
    "build_cognition_runner",
    "cognition_budget",
    "floors_ready",
    "read_surface",
    "read_tools_for",
    "require_session_hook",
    "units_with_home_rooms",
    "upcoming_reader",
]
