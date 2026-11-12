"""Building the Cognition session for the composition root (DEC-244).
Everything the runner needs that is not itself the runner: which read tools it
holds, what it is told, its budget, and how it is stopped.

`[cognition] enabled = false` (the shipped default) builds NOTHING here:
`build_cognition_runner` is not called, the model source is not touched and
`nora.app.cognition_agent_runner` is not imported (it is imported inside the
function, so importing this module costs nothing). `true` builds exactly one
`SubconsciousExecutor` — the `CognitionSessionRunner` hosted as a Dreamer
adaptor, whose hook the wake evaluator is handed and which runs every
admitted wake through `DreamerRuntime.run` (node 30) — and
`require_session_hook` refuses to boot if that did not happen.

**What she reads**: the tools of the read catalogue that EXIST and are
read-only, less what a flag withholds. `COGNITION_READ_CATALOGUE` is spelled out
here rather than derived from `read_only_tools()`, because a session that
quietly gained every read tool the registry ever adds is a change nobody decided;
`read_surface` says which of the named tools are wired and which are missing (a
named tool the tree does not have is a fact the boot log states, never a silent
gap). The public enrichment reads (`web_*`, the social providers', `place_info`)
ARE in it: they fetch public content, never the unit's own speech, so the tag
path's having them first is no reason the session stays blind to them.

**Who they read through.** The read tools are built by the SAME
`RegistryToolSource` every other Dreamer adaptor reads through, under
`cognition_grant()` (a real room, no act verb), so lineage, the roster
disclosure guard and the record read's own room rules are the ones the tag
path already runs; this module builds no read of its own.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent.dreamer import (
    AdaptorGrant,
    DreamToolSource,
    cognition_grant,
)
from nora.agent.room import Room as AgentRoom
from nora.domain.cognition_session import COGNITION_ADAPTOR, Room
from nora.domain.gatherings import (
    OPEN_STATUSES,
    Gathering,
    GatheringStore,
    event_in_scope,
)
from nora.domain.live import LiveMapping
from nora.domain.ports import CognitionStore, StimulusStore
from nora.domain.provenance import instruction_sha256
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
    # the typed source-object reads (platform-observations/exposure.md)
    "poll_list", "poll_read", "room_state_read", "group_members",
    # the tiers' reads that already exist
    "where_been", "routine_read", "note_read", "note_search",
    # memory
    "about", "ask_memory", "search_memory", "search_memory_workspace",
    # the graph surface's (DEC-316): built instead of the Honcho memory verbs
    # above while `[memory.index] read`, never beside them
    "memory_search", "memory_read",
    # the room's own rows, roster and people (platform-observations/exposure.md)
    "room_read", "load_transcript", "who_said", "lookup_person",
    # the stored-object reads: a media object, a shared file, the
    # generated-image library
    "media_fetch", "media_search", "file_fetch",
    # the public enrichment reads (their providers live on the tag path too;
    # an unwired one is `withheld` or answers `unavailable`, never absent)
    "web_search", "web_fetch", "place_info",
    "instagram_read", "instagram_search", "x_read", "x_search",
})
#: Named deliberately ABSENT — an exclusion is a decision the catalogue
#: states, not a gap it hides:
#:
#: Document list/read/revise are bound separately in the session through its
#: explicit shared agenda-room capability. The generic requester-scoped reader
#: in this catalogue must not borrow a human identity.
#: * `linkedin_read`/`linkedin_search`: the existing unasked-turn rule protects
#:   profile-view notifications and search quota; Cognition has no asked turn.

#: The side-effect audit of every tool in the catalogue
#: (`tests/unit/test_cognition_wiring.py` holds its keys equal to
#: `COGNITION_READ_CATALOGUE`, so a tool added to the catalogue without an
#: audited line fails the gate). These are native reads; ComprehendRead may
#: separately serialize fetched Record evidence, but no Notebook judgement or act.
#:
#: Counters (`nora_tool_calls_total`, `nora_recall_total`, `nora_room_lookups_
#: total`) are observability, read by a person and never by a decision, and are
#: kept. A Honcho read verb only ever ROUTES (`HonchoClient._route` ensures the
#: unit's workspace marker and scope once per process, the identical idempotent
#: get-or-create the tag path performs) and answers; no message and no
#: conclusion is written.
READ_TOOL_AUDIT: Mapping[str, str] = {
    "group_members": "Scoped Room.members read through the agenda room registry; "
                     "existing roster availability and audience checks; counters only.",
    "record_query": "SQL read of messages, edits, reactions, deletes and the media "
                    "index (`MediaJoin.enrich`); counters only. No write.",
    "poll_list": "SQL SELECT of `platform_object_state` heads of this room's "
                 "scope, kind poll; counters only. No write.",
    "poll_read": "SQL SELECT of one `platform_object_state` head by its scoped "
                 "digest; counters only. No write.",
    "room_state_read": "SQL SELECT of `platform_object_state` heads of this "
                       "room's scope, kinds room/membership/pin; counters only. "
                       "No write.",
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
    "room_read": "Unit room resolution and bounded SQL transcript/media reads; "
                 "counters only. No write.",
    "load_transcript": "SQL read of recorded rows through `ledger.span_rows`/"
                       "`store.recent`, rendered through the shared media join; "
                       "counters only. No write.",
    "who_said": "In-memory read of the shown transcript window; counters only. "
                "No write.",
    "lookup_person": "Person registry read (`persons`); counters only. No write.",
    "media_fetch": "Stored media-object reads and optional blob/vision fetch; "
                   "counters only. No write.",
    "media_search": "SQL read of the generated-image library (`media_store`); "
                    "counters only. No write.",
    "file_fetch": "Stored shared-file read (`external_files`); counters only. "
                  "No write.",
    "place_info": "Geocoder place lookup; counters only. No write.",
    "web_search": "Search provider query; result screening and provenance at "
                  "`ComprehendRead`; counters only. No write.",
    "web_fetch": "One page fetch; the bounded text lands as a `web_page` Record "
                 "source via `ComprehendRead`; counters only. No write.",
    "instagram_read": "Instagram read through the social provider; counters "
                      "only. No write.",
    "instagram_search": "Instagram search through the social provider; counters "
                        "only. No write.",
    "x_read": "X read through the social provider; counters only. No write.",
    "x_search": "X search through the social provider; counters only. No write.",
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


#: `CognitionSessionRunner`'s `read_tools_for` collaborator: `(unit, rooms,
#: *, clock=None)`. `clock` binds every built tool's `now_fn` to a session
#: instant — the historical lane's evidence time — rather than the process's
#: wall clock; a live session passes `None`.
ReadTools = Callable[..., list[Any]]


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
    built by the same source and laid under one tool per name that takes an
    `agenda_room` argument (`nora.agent.room_routing`), so each read resolves
    through the room it names; an `agenda_room` argument that is not on the
    agenda is refused. That binds WHICH room a read is resolved through (its
    roster and lineage), not which rooms' data a tool may reach: `record_query
    scope="all_rooms"` and `search_memory_workspace` read the unit's OTHER
    rooms too, because the unit is the read boundary (DEC-231). The unit's home
    room is never substituted for an item's room.

    `clock` is forwarded to the source's per-occasion bind (the registry's
    injected clock, swapped by copy the way `with_memory_search` swaps the
    reader): every `now_fn()` the built tools call then reads that instant.
    `on_rows` is the transcript rows' evidence callback (what the reads hand
    out, kept for citation); both are forwarded only when given — they are
    optional on the source protocol, and an argument a narrower source does
    not declare is never sent."""
    allowance = grant if grant is not None else cognition_grant()
    try:
        _build_params: Mapping[str, inspect.Parameter] = inspect.signature(
            source.build).parameters
    except (TypeError, ValueError):
        _build_params = {}
    _accepts_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        for p in _build_params.values())

    def build_for(
        room: Room, clock: Callable[[], datetime] | None = None,
        on_rows: Callable[..., None] | None = None,
    ) -> list[Any]:
        extra: dict[str, Any] = {
            **({"clock": clock}
               if clock is not None and (_accepts_kw or "clock" in _build_params)
               else {}),
            **({"on_rows": on_rows}
               if on_rows is not None and (_accepts_kw or "on_rows" in _build_params)
               else {}),
        }
        return source.build(
            key=room.conversation_id, names=names, unit=None,
            adaptor=COGNITION_ADAPTOR_NAME, grant=allowance, **extra,
        )

    def build(
        unit: str, rooms: Sequence[Room], *,
        clock: Callable[[], datetime] | None = None,
        on_rows: Callable[..., None] | None = None,
    ) -> list[Any]:
        if len(rooms) == 1:
            return build_for(rooms[0], clock, on_rows)
        from nora.agent.room_routing import route_reads

        return route_reads({
            f"{room.platform}:{room.conversation_id}": build_for(room, clock, on_rows)
            for room in rooms
        })

    return build


class SkillNotInstalledError(LookupError):
    """Cognition's notebook guidance is not in the corpus. At startup the
    composition turns it into a `StartupError`; on a live hot reload that deleted
    or renamed the file, the session records a `failed` wake, leaves its stimuli
    pending and never runs without it."""


REGISTER_SKILL = "cognition-register"


def cognition_instruction(instructions: Any) -> tuple[str, str]:
    """The session's whole instruction and its digest: the `cognition` objective
    alone, with no shared Dreamer doctrine and no wrapper tag around it."""
    body = instructions.objective_body(COGNITION_OBJECTIVE).strip()
    return body, instruction_sha256(body)


@dataclass(frozen=True)
class SessionGuidance:
    """Resolve notebook guidance once per session; context assembly gates its tools."""

    resolve: Callable[[str], Any]

    def resolved(self) -> tuple[str, dict[str, object]]:
        skill = self.resolve(REGISTER_SKILL)
        if skill is None:
            raise SkillNotInstalledError(f"session skill {REGISTER_SKILL!r} is not installed")
        return skill.instructions, {REGISTER_SKILL: skill.sha256}

    def text(self) -> str:
        return self.resolved()[0]

    def versions(self) -> dict[str, object]:
        return self.resolved()[1]


def session_guidance(instructions: Any) -> SessionGuidance:
    return SessionGuidance(resolve=instructions.skill)


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
    """Stops the sessions at shutdown, once. The session tasks live on the
    Dreamer lane (`SubconsciousExecutor` → `DreamerRuntime.dispatch`), so
    the wait and the cancel go through the executor to their owner; the
    runner's `shutdown` then waits for the cancelled sessions' outcome
    writes. The second call of the whole thing is a no-op, so a process
    that reaches shutdown twice does not cancel twice.

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
        # The lane owns the session tasks: wait and cancel there when the
        # runner exposes it (the executor delegates to `DreamerRuntime`),
        # falling back to the evaluator's own hook-task bookkeeping for a
        # wiring that never went through dispatch.
        wait = getattr(self._runner, "wait_in_flight", None)
        if wait is not None:
            await wait(self._grace)
        else:
            await self._evaluator.wait_in_flight(self._grace)
        cancel = getattr(self._runner, "cancel_in_flight", None)
        if cancel is not None:
            await cancel()
        else:
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



def context_screen(
    *, content_checks: Any, doctrine_store: Callable[[], Any], canary: Any,
) -> Callable[[str], str | None]:
    """The cognition read's screen: the reply scan plus the doctrine canary.
    `doctrine_store` is a getter — it is built after the screen is bound."""
    def screen(text: str) -> str | None:
        from nora.agent.egress import LeakGuard, scan_reply

        return scan_reply(text, content_checks=content_checks) or LeakGuard(
            doctrine_store().get(applies_to="cognition"), canary
        ).check(text)
    return screen


def shared_document_room(
    *, allowlist: Any, persons: Any,
) -> Callable[[str, str, str], bool]:
    """A room the unit's own identified persons share, for document reads."""
    def shared(unit: str, platform: str, cid: str) -> bool:
        c = allowlist.conversation(platform, cid)
        return bool(c is not None and c.unit == unit and c.persons
                    and c.persons <= persons.by_person.keys())
    return shared


def build_context_reader(
    *,
    engine: Any,
    allowlist: Any,
    persons: Any,
    lineage: Any,
    presence_reader: Any,
    audit: Any,
    rosters: Any,
    registry: Any,
    media_join: Any,
    record_chars: int,
    documents_context: Any,
    screen: Callable[[str], str | None],
    content_checks: Any,
) -> Any:
    """The shared-context reader the grounder calls per agenda room."""
    import functools

    from nora.agent.dream_screen import text_offence
    from nora.app.cognition_context import CognitionContextReader
    from nora.infra.sql.lessons import SqlLessonStore
    from nora.infra.sql.profile import SqlProfileStore
    from nora.infra.sql.record_query import SqlRecordQuery
    from nora.language.lesson_render import ActiveLessons

    profiles = SqlProfileStore(
        engine,
        screen=functools.partial(text_offence, content_checks=content_checks),
    )
    return CognitionContextReader(
        policy=allowlist, persons=persons, lineage=lineage,
        records=SqlRecordQuery(engine), directory=presence_reader, audit=audit,
        profiles=profiles,
        lessons=ActiveLessons(SqlLessonStore(engine)), media_join=media_join,
        room_for=lambda platform, cid: AgentRoom(
            platform=platform, conversation_id=cid,
            roster_source=rosters.get(platform), persons=persons,
            self_ids=registry.self_aliases()),
        screen=screen, record_chars=record_chars, documents=documents_context,
    )

def build_cognition_runner(
    *,
    cfg: Any,
    tag_trace_store: Any = None, memory: Any = None,
    models: Any,
    retry: Any, metrics: Any,
    tools: DreamToolSource,
    instructions: Any,
    store: CognitionStore,
    stimulus_store: StimulusStore,
    note_titles: Callable[[str], Awaitable[Sequence[str]]],
    notes: Any, events: Any,
    standing: Any,
    reminders: Any,
    ai_peer: str,
    room_proactive: Callable[[str, str], bool],
    reminder_caps: ReminderCaps,
    open_events_in: Callable[[str, str, str], Awaitable[Sequence[Gathering]]],
    standing_of: Callable[[str, str], Awaitable[StandingHead | None]],
    roster_of: Callable[[str, str], Awaitable[Mapping[str, str] | None]] | None = None,
    nora_names: frozenset[str] = frozenset(),
    homes: Mapping[str, Any], units: Sequence[str],
    skills: SessionGuidance,
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
    standing_fold: Any = None,
    rooms_of_unit: Callable[[str], Sequence[tuple[str, str]]] | None = None,
    observations: Any = None,
    documents: Any = None, documents_context: Any = None,
    runtime: Any = None,
    # `CognitionLessons`; None ([lessons] disabled) offers no lesson verbs.
    lessons: Any = None,
) -> Any:
    """The session executor: a `SubconsciousExecutor` whose hook the wake
    evaluator is handed, running each admitted wake through
    `DreamerRuntime.run`. Imported lazily: `DreamerAgentRunner` (and with it
    Strands) is loaded only when a session will actually run. `runtime` is
    the composition's one `DreamerRuntime`; `models`/`retry` are kept for
    callers that build the runner shape directly in tests."""
    from nora.app.cognition_agent_runner import DreamerAgentRunner
    from nora.app.cognition_session import CognitionSessionRunner
    from nora.app.subconscious import SubconsciousExecutor

    if runtime is None:
        raise ValueError(
            "build_cognition_runner requires the composition's DreamerRuntime: "
            "a session executes through runtime.run, not a sibling runner",
        )
    agent_runner = DreamerAgentRunner(
        runtime=runtime, metrics=metrics,
        # The READ tools' argument names only. The registry's table also holds
        # the tag path's `note_create`, `note_revise` and `remind`, whose
        # arguments differ from the session's own verbs of the same names
        # (`room`, `at`, ...), and R-99 would veto every call to them.
        # Plus `agenda_room` and `room`: the routed tools of an agenda spanning
        # several rooms take `agenda_room` (`nora.agent.room_routing`), and a
        # routed `room_read` still takes its own `room` destination — R-99
        # would veto an argument the table does not list.
        param_names={
            n: frozenset(names) | {"agenda_room", "room"}
            for n, names in tools.tool_param_names().items() if n in surface.wired
        },
        result_caps=result_caps,
    )

    def doctrine() -> tuple[str, str | None]:
        return cognition_instruction(instructions)

    # Read once here so a missing or malformed objective, or a session skill that
    # is not installed, is a startup failure with its name, not the first wake's
    # (`DreamerInstructions` raises `InstructionError`, `SessionGuidance` raises
    # `SkillNotInstalledError`; the composition turns both into a `StartupError`).
    text, sha = doctrine()
    skills.resolved()
    home_rooms = LiveMapping(lambda: _by_home_room(homes))

    async def open_events(
        unit: str, platform: str, conversation_id: str,
    ) -> Sequence[Gathering]:
        return await open_events_in(unit, platform, conversation_id)

    async def prior(platform: str, conversation_id: str) -> StandingHead | None:
        return await standing_of(platform, conversation_id)

    runner = CognitionSessionRunner(
        tag_trace_store=tag_trace_store, memory=memory,
        store=store, stimulus_store=stimulus_store,
        notes=notes, events=events, standing=standing, reminders=reminders,
        ledger=ledger, shared=shared,
        ai_peer=ai_peer, memos=memos,
        agent_runner=agent_runner, home_rooms=home_rooms,
        read_tools_for=read_tools_for(tools, names=surface.wired),
        read_only_tools=surface.wired,
        skill_text_for=skills.text, skill_versions_for=skills.versions,
        skills_for=skills.resolved, grounder=grounder, render_budget=render_budget,
        person_known=person_known, documents=documents, documents_context=documents_context,
        doctrine_text=text, doctrine_sha256=sha, doctrine_source=doctrine,
        open_note_titles=note_titles, open_events=open_events,
        prior_standing=prior,
        rooms_of_unit=rooms_of_unit,
        standing_fold=standing_fold,
        budget=budget, cooldown_seconds=cfg.cognition.cooldown_seconds,
        units=list(units), session_timeout_seconds=cfg.cognition.session_timeout_seconds,
        max_items=cfg.cognition.max_items,
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
        # The observation Record `object_refs` validates a retained source
        # against: no store, no ref write (`unavailable`, never accepted
        # unchecked).
        observations=observations,
        lessons=lessons,
    )
    # Cognition hosted as a Dreamer adaptor (node 30): the evaluator's hook
    # is the executor's, and every admitted wake executes inside
    # `runtime.run(adaptor=…)` — admission, cancellation and the model call
    # have the one owner every other subconscious adaptor has.
    return SubconsciousExecutor(runtime=runtime, runner=runner)


__all__ = [
    "COGNITION_ADAPTOR_NAME",
    "COGNITION_OBJECTIVE",
    "COGNITION_READ_CATALOGUE",
    "REGISTER_SKILL",
    "CognitionShutdown",
    "ReadSurface",
    "SessionGuidance",
    "SkillNotInstalledError",
    "build_cognition_runner",
    "cognition_budget",
    "cognition_instruction",
    "read_surface",
    "read_tools_for",
    "require_session_hook",
    "units_with_home_rooms",
    "upcoming_reader",
]
