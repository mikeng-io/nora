"""The Cognition session loop — the async half `nora.domain.cognition_session`'s
own docstring names as "deferred past 3a-i" (design/cognition-loop/
30-cognition.md; migration step 3a-ii).

**What this module wires, and nothing else.** `[cognition] session_mode`
governs it (`nora.config.CognitionConfig`): `"off"` means this module is
never called at all — the composition root simply does not register
`CognitionSessionRunner.wake_hook` as the wake evaluator's hook, so `"off"`
makes literally zero model calls by construction, not by a runtime check
inside a call nobody skipped (`tests/unit/test_cognition_session_runner.py`
still asserts the belt-and-braces check below, in case a future composition
root wires the hook unconditionally). `"shadow"` runs one session per
would-wake the evaluator (`nora.app.wake.WakeEvaluator`) records.

**A wake has one fate, and the brakes read it** (20 §3, DEC-242;
`nora.domain.stimuli`'s state machine). The evaluator writes the wake row
`tracked`; from then until its ONE `cognition_wake_outcome` row the wake is
OPEN (its unit in flight, its range claimed), and with no outcome past
`session_timeout_seconds` it is EXPIRED, read as failed. Every path out of
`on_wake` writes that outcome, keyed by the WAKE, so an exit before the
session row exists (a missing home room, `build_situation` or
`record_session` raising, a cancel before the first step) is recorded
`failed`/`cancelled` like any other; a kill, or an outcome write that itself
fails, leaves the wake OPEN until it expires, which is the same fate, later.
It ends exactly one way: `closed` (the model loop returned and every item was
closed or returned to the next agenda with a strike), written in ONE
transaction with those carry rows, or `failed` / `timeout` / `cancelled`,
with none. Only `closed` advances the unit's watermark; the others leave their
stimuli pending («a failed session does not advance it»), and a failure is
RAISED after it is recorded, never returned as if it were a session that ran.
The session's wall clock is `budget.timeout_seconds` (30 §5), enforced here by
cancelling the model loop, and held at least `SESSION_TIMEOUT_MARGIN_SECONDS`
under the wake's own timeout (the constructor refuses otherwise) so a session
records its outcome before its wake can expire; `shutdown` cancels the
sessions still in flight and records them `cancelled`.

**What a session gets** (30-cognition.md §1-§2): the situation and agenda
(`nora.domain.cognition_session.build_situation`, already pure and already
shipped), the read tools its rooms permit (built by the CALLER — the
composition root, from the existing `nora.agent.dreamer` tool-source
machinery every other adaptor already uses; this module does not rebuild
disclosure, lineage or withdrawal, it is handed the finished tool list and
REFUSES any tool not on `read_only_tools`), skills injected per agenda kind
present, and ONLY the dry-run write/pace/remind-mirror verbs as tools — see
`_build_tools` below, and
`tests/behavioral/test_cognition_session_writes_nothing_live.py` for the
runtime guard this buys. Every verb is declared with named, typed parameters
and a docstring (ADR 0015): a `**kwargs` closure reaches the model as one
untyped `kwargs` parameter, which is not a tool.

**An agenda with no items is no session** (30-cognition.md §1): `on_wake`
returns before building any tool or calling `agent_runner` at all. The ONE
call site in this whole module that can make a model call is the single
`await self._agent_runner.run(...)` inside `_run_session` — everything
before it is pure bookkeeping, which is what makes "off, or an empty
agenda, makes zero model calls" a property of the control flow rather than
a claim about what a mock was not called with.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any, Literal, Protocol

from nora.agent.cognition_dry_run import (
    dry_run_event_propose,
    dry_run_event_revise,
    dry_run_event_set_status,
    dry_run_note_create,
    dry_run_note_update,
    dry_run_standing_revise,
)
from nora.agent.cognition_pace import (
    dry_run_wake_at,
    dry_run_wake_cancel,
    dry_run_wake_fire,
    dry_run_wake_move,
)
from nora.agent.cognition_remind_mirror import (
    dry_run_remind,
    dry_run_remind_cancel,
    dry_run_remind_move,
)
from nora.agent.fetch_watch import Fetched, watch_reads
from nora.domain.cognition_ground import Grounding, RecordItem, rollup_status
from nora.domain.cognition_pace import (
    DEFAULT_MAX_SELF_WAKES_PER_DAY,
    PENDING_WINDOW_SECONDS,
    PaceAnswer,
    PendingSelfWake,
)
from nora.domain.cognition_remind import PendingMirroredRemind, RemindAnswer
from nora.domain.cognition_render import RenderBudget, render_agenda, room_key, room_label
from nora.domain.cognition_session import (
    Agenda,
    AgendaItem,
    CloseOutcome,
    Room,
    Situation,
    build_situation,
    close_or_strike,
)
from nora.domain.gatherings import Gathering, GatheringStatus
from nora.domain.note_body import NORA_NAMES, Shown, ShownMessage
from nora.domain.ports import (
    CognitionDryRunStore,
    NewCarry,
    NewCognitionSession,
    NewCognitionSessionClose,
    NewWakeOutcome,
    RecordedOutcome,
    StimulusStore,
)
from nora.domain.standing import StandingHead
from nora.domain.stimuli import (
    LIMIT_STOP_REASONS,
    NO_CAUSE,
    AgendaError,
    SessionLimitError,
    Stimulus,
    UnitView,
    WakeHook,
    classify_failure,
    estimate_tokens_lower_bound,
    is_context_overflow,
    prove_overflow_is_the_agenda,
)

log = logging.getLogger("nora.app.cognition_session")

#: `established_by`'s prefix for every Register write cognition makes in
#: shadow — code-stamped provenance, never taken from a model argument
#: (the same reason `nora.agent.tools.TAG_PATH_PREFIX` exists for the tag
#: path: "who wrote it" is not something the writer gets to assert).
COGNITION_PATH_PREFIX = "cognition:"

#: The verbs a session holds itself. A read tool may not share one of these
#: names: it would shadow (or be shadowed by) a dry-run verb.
SESSION_VERB_NAMES: frozenset[str] = frozenset({
    "close", "note_create", "note_update", "event_propose", "event_revise",
    "event_set_status", "standing_revise", "wake_at", "wake_move", "wake_cancel",
    "remind", "remind_move", "remind_cancel",
})

_DAY_MS = 24 * 3600 * 1000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class NotReadOnlyToolError(ValueError):
    """`read_tools_for` handed the session a tool that is not on the
    read-only allowlist (or that shadows one of the session's own verbs)."""


class SessionTimeoutError(Exception):
    """The session's wall clock (`AdaptorBudget.timeout_seconds`) ran out and
    the model loop was cancelled. Its own class so a `TimeoutError` a store
    raised is never mistaken for it."""


class SessionExpiredError(Exception):
    """The session ran to its end, but its wake had already expired and a
    later wake had taken its stimuli: the store recorded it `timeout` and
    wrote none of its carries (`StimulusStore.record_outcome`)."""


class MissingHomeRoomError(ValueError):
    """A unit the evaluator may wake has no home room: refused when the runner
    is built, not discovered one wake at a time."""


#: How far under the wake's own `session_timeout_seconds` the session's wall
#: clock must stay: the time between the wake row and the model loop (the
#: situation, the session row, the tools) and after it (the settle write). A
#: session still running when its wake expires would close into a range a
#: later wake may already hold (the store refuses that close; this keeps it
#: from happening in the ordinary case).
SESSION_TIMEOUT_MARGIN_SECONDS = 60.0


def check_session_timeouts(budget_timeout_seconds: float, session_timeout_seconds: float) -> None:
    """Refuse a session wall clock that does not end at least
    `SESSION_TIMEOUT_MARGIN_SECONDS` before the wake's timeout."""
    if budget_timeout_seconds > session_timeout_seconds - SESSION_TIMEOUT_MARGIN_SECONDS:
        raise ValueError(
            f"the session budget's timeout ({budget_timeout_seconds:g}s) must end at least "
            f"{SESSION_TIMEOUT_MARGIN_SECONDS:g}s before [cognition] session_timeout_seconds "
            f"({session_timeout_seconds:g}s), or a session can outlive its wake",
        )


def check_home_rooms(units: Collection[str], home_rooms: Mapping[str, Room]) -> None:
    """Every unit the evaluator may wake has a home room."""
    missing = sorted(u for u in units if u not in home_rooms)
    if missing:
        raise MissingHomeRoomError(f"no home room configured for unit(s) {missing}")


def default_mint_ref() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class AdaptorBudget:
    """The Cognition session's own ceiling — the same shape
    `nora.agent.dreamer.AdaptorBudget` already is for every other adaptor,
    restated here so this module never imports `nora.agent.dreamer` (see
    `tests/architecture/test_cognition_shadow_cannot_write_or_send.py`'s
    forbidden-prefix list, which already names that module)."""

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float
    #: `nora.agent.dreamer.AdaptorBudget`'s two per-pass ceilings on tool
    #: reads (issue #160, design 43 §4): the memory cluster and every verb
    #: together. `None` is no ceiling, which is every caller's behaviour
    #: before these existed.
    max_memory_calls: int | None = None
    max_tool_calls: int | None = None


@dataclass(frozen=True)
class CognitionRunResult:
    """What one model call returned. `stop_reason` is Strands' own (R-23);
    `None` on a run this module never made (belt-and-braces callers only)."""

    stop_reason: str | None = None


class CognitionAgentRunner(Protocol):
    """The test seam (CLAUDE.md's policy: a constructor argument, never an
    `_fn` hook). `nora.app.cognition_agent_runner.LiveCognitionAgentRunner`
    is the production implementation, wrapping `nora.agent.factory.
    create_dream_agent`; `tests/behavioral/
    test_cognition_session_writes_nothing_live.py`'s `ScriptedAgentRunner`
    drives a fixed sequence of tool calls with no model and no Strands.

    **It RAISES on failure.** A model loop that errors or runs out its clock
    is not a session that returned; an implementation that swallowed either
    into a normal `CognitionRunResult` would make a failed session read as one
    that ran, close its items `waiting` with a strike, and advance the
    watermark past stimuli nobody looked at."""

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult: ...


@dataclass(frozen=True)
class CognitionSessionOutcome:
    session_id: int
    items: int
    closed: tuple[CloseOutcome, ...]
    stop_reason: str | None


# ---------------------------------------------------------------------------
# What the runner needs from the world, each a narrow, explicit seam — never
# a live store handed through whole. "the session factory takes only the
# dry-run store type" (review follow-up 5c): `CognitionSessionRunner`'s own
# `__init__` below never accepts anything typed as a live NoteBook,
# GatheringStore or StandingStore — only these READ-ONLY callables, each
# returning plain data. A caller wiring a live store in has to narrow it
# to one of these shapes itself; there is no parameter here it could widen.
# ---------------------------------------------------------------------------

OpenNoteTitles = Callable[[str], Awaitable[Sequence[str]]]
OpenEvents = Callable[[str, str], Awaitable[Sequence[Gathering]]]
#: `(platform, conversation_id)` -> the room's Standing head WITH the id of the
#: `standing_revision` row that is it (`SqlStandingStore.read_head`): the token a
#: `standing_revise` call asserts as its base (M6, DEC-250).
PriorStanding = Callable[[str, str], Awaitable[StandingHead | None]]
#: `(platform, conversation_id)` -> the room's roster, roster name -> person id,
#: or None when it cannot be read (an ERROR, never an empty room): what a note's
#: `## Owed` owner is checked against (M3).
RosterOf = Callable[[str, str], Awaitable[Mapping[str, str] | None]]
#: `(unit, since_ms)`: the unit's pending self-wakes, reading only refs with a
#: transition at or after `since_ms` (`CognitionDryRunStore.pending_self_wakes`).
PendingSelfWakes = Callable[[str, int], Awaitable[Sequence[PendingSelfWake]]]
PendingMirroredReminds = Callable[[str], Awaitable[Sequence[PendingMirroredRemind]]]
SelfWakesCreatedSince = Callable[[str, int], Awaitable[int]]
#: `(unit, the agenda's rooms)`: the read tools for those rooms. One room: that
#: room's tools. Several: ONE tool per name, each taking a `room` argument that
#: routes the read through the item's own room and refuses a `room` value not
#: on the agenda (DEC-231: each item's reads resolve through its room), built
#: by the caller. That names the room a read is BOUND to, not the only room it
#: reads: `record_query scope="all_rooms"` and `search_memory_workspace` reach
#: the unit's other rooms, which the unit-wide read boundary allows.
ReadToolsFor = Callable[[str, Sequence[Room]], list[Any]]
#: `(unit, agenda items, the claimed stimuli)` -> what she is shown beside the
#: agenda (`nora.domain.cognition_ground`): the Record rows that raised each
#: stimulus and the Register index lines. RAISES when it cannot read: a session
#: that could not be grounded must not run on the bare counts.
Grounder = Callable[[str, Sequence[AgendaItem], Sequence[Stimulus]], Awaitable[Grounding]]
SkillsFor = Callable[[Sequence[str]], str]
#: The versions (content digests) of the skills `SkillsFor` would inject for
#: these agenda kinds, recorded on the session row (30 §1: "the skill versions
#: injected").
SkillVersionsFor = Callable[[Sequence[str]], Mapping[str, object]]
#: `(text, sha256)` of the session's instruction, read PER SESSION: the Dreamer
#: objective hot-reloads (DEC-128), and a text captured at construction would
#: keep serving an objective the operator has already edited.
DoctrineSource = Callable[[], tuple[str, str | None]]


@dataclass
class _RunState:
    """Mutable bookkeeping for exactly one `on_wake` call — held on the
    stack, never on `self`, so two concurrent sessions (up to
    `max_concurrent_sessions`) never share it."""

    session_id: int
    wake_id: int
    unit: str
    now_ms: int
    items_by_index: dict[int, AgendaItem]
    #: `"platform:conversation_id"` -> the room, for every room on the agenda.
    rooms: dict[str, tuple[str, str]]
    #: The Record rows the session was SHOWN (the grounding's `records`): what
    #: a note body may cite, each checked for its author (M3).
    records: tuple[RecordItem, ...] = ()
    #: What the session's read tools returned that a note may cite (m4).
    fetched: Fetched = field(default_factory=Fetched)
    #: `(room key, agenda item or None)` of every write this session made that
    #: would commit: what `close(reason="written")` is backed by, per item (B3).
    #: A write that named no item is `(room, None)`.
    written: set[tuple[str, int | None]] = field(default_factory=set)
    closed_indices: set[int] = field(default_factory=set)
    closes: list[CloseOutcome] = field(default_factory=list)
    #: The line of the prompt that says which room(s) the read tools resolve
    #: through (`_build_tools`).
    reads_note: str = ""


_room_key = room_key


def _room_of(room: str, state: _RunState) -> tuple[str, str] | dict[str, object]:
    """The room a write is about. One room on the agenda: it is that room and
    `room` may be left out. Several: the model MUST say which, and a room not
    on the agenda is refused — never guessed from the first item, which would
    file a write into a room its own agenda never named (R-21)."""
    if room:
        found = state.rooms.get(room)
        if found is None:
            return {
                "status": "refused", "reason": "unknown_room",
                "detail": f"{room!r} is not a room on this agenda: "
                          f"{sorted(state.rooms)}",
            }
        return found
    if len(state.rooms) == 1:
        return next(iter(state.rooms.values()))
    return {
        "status": "refused", "reason": "room_required",
        "detail": f"this agenda spans {len(state.rooms)} rooms; name the one this "
                  f"write is about as `room`, one of {sorted(state.rooms)}",
    }


def _write_target(
    room: str, item: int | None, state: _RunState,
) -> tuple[tuple[str, str], int | None] | dict[str, object]:
    """The room a write is about and the agenda item it is for (B3). `item`
    is optional: given, it must be an item on the agenda, and it fixes the room
    (a `room` that names another is refused, never guessed between). Left out,
    the room is `_room_of`'s."""
    if item is None:
        resolved = _room_of(room, state)
        return resolved if isinstance(resolved, dict) else (resolved, None)
    found = state.items_by_index.get(item)
    if found is None:
        return {"status": "refused", "reason": "no_such_item",
                "detail": f"{item} is not an item of this agenda"}
    key = _room_key(found.room)
    if room and room != key:
        return {"status": "refused", "reason": "item_room_mismatch",
                "detail": f"item {item} is in {key}, not {room}"}
    return (found.room.platform, found.room.conversation_id), item


def _off_agenda(state: _RunState) -> dict[str, object]:
    """A reminder whose room is not on today's agenda is not this session's to
    move or cancel: the same refusal a write to such a room gets (R-21)."""
    return {
        "status": "refused", "reason": "unknown_room",
        "detail": f"that reminder's room is not a room on this agenda: {sorted(state.rooms)}",
    }


def _written_for(index: int, item: AgendaItem, state: _RunState) -> bool:
    """Whether a write that would commit backs `index`'s `written` close: one
    tagged with this item, or an untagged write in its room when it holds the
    only item there (B3)."""
    key = _room_key(item.room)
    if (key, index) in state.written:
        return True
    alone = sum(1 for other in state.items_by_index.values()
                if _room_key(other.room) == key) == 1
    return alone and (key, None) in state.written


def _parse_when(text: str) -> datetime | None:
    """ISO 8601 with an offset, or None."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _when_ms(text: str) -> int | None:
    """Epoch ms of an ISO 8601 time WITH an offset, or None (naive, empty or
    unparseable): every time a session verb takes is one time base (M5)."""
    parsed = _parse_when(text)
    # Integer arithmetic: `timestamp() * 1000` can land a millisecond short.
    return None if parsed is None else (parsed - _EPOCH) // timedelta(milliseconds=1)


def _bad_time(field_name: str) -> dict[str, object]:
    return {
        "status": "refused", "reason": "bad_time",
        "detail": f"{field_name} must be ISO 8601 with an offset, such as "
                  "2026-10-03T19:40+08:00; a time with no offset is refused",
    }


_ISO_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_OWED_HEADING = re.compile(r"(?mi)^\s*#+\s*owed\b")


def _day_number(text: str) -> int | None:
    """`YYYYMMDD` of an ISO date `YYYY-MM-DD` (a real day), or None: a note's
    anchor is a date in the session's one time base, stored as the number the
    note tier holds (M5)."""
    if not _ISO_DAY.match(text):
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day.year * 10_000 + day.month * 100 + day.day


def _verb_result(write: Any) -> dict[str, object]:
    if write.outcome == "refused":
        return {
            "status": "refused", "reason": write.refusal_reason,
            "detail": write.refusal_detail,
        }
    return {"status": "ok"}


def _answer(answer: PaceAnswer | RemindAnswer, **ok: object) -> dict[str, object]:
    """A recorded pace or remind call, as the tool's answer: the refusal WITH
    its reason when it was refused, so the model can act on it."""
    if answer.refusal_reason is not None:
        return {
            "status": "refused", "reason": answer.refusal_reason,
            "detail": answer.refusal_detail,
        }
    return {"status": "ok", **ok}


class CognitionSessionRunner:
    def __init__(
        self, *, dry_run_store: CognitionDryRunStore, stimulus_store: StimulusStore,
        agent_runner: CognitionAgentRunner, home_rooms: Mapping[str, Room],
        read_tools_for: ReadToolsFor, skill_text_for: SkillsFor, grounder: Grounder,
        doctrine_text: str, doctrine_sha256: str | None,
        open_note_titles: OpenNoteTitles, open_events: OpenEvents,
        prior_standing: PriorStanding, pending_self_wakes: PendingSelfWakes,
        pending_mirrored_reminds: PendingMirroredReminds,
        session_mode: str, budget: AdaptorBudget, cooldown_seconds: float,
        units: Collection[str], session_timeout_seconds: float,
        max_items: int, max_strikes: int, max_self_wakes: int = 5,
        max_self_wakes_per_day: int = DEFAULT_MAX_SELF_WAKES_PER_DAY,
        self_wakes_created_since: SelfWakesCreatedSince | None = None,
        read_only_tools: frozenset[str] = frozenset(),
        model_id: str | None = None, model_route: str | None = None,
        metrics: Any = None, mint_ref: Callable[[], str] = default_mint_ref,
        clock: Callable[[], float] = time.time,
        doctrine_source: DoctrineSource | None = None,
        skill_versions_for: SkillVersionsFor | None = None,
        tz: tzinfo = UTC, render_budget: RenderBudget | None = None,
        model_window_tokens: int | None = None, overflow_window_fraction: float = 0.8,
        person_known: Callable[[str], bool] | None = None,
        roster_of: RosterOf | None = None,
        nora_names: frozenset[str] = NORA_NAMES,
    ) -> None:
        """`doctrine_source`, when given, is read at the start of EVERY session
        and replaces `doctrine_text`/`doctrine_sha256` (which then serve only
        as the constructor's fixed fallback for a caller with a fixed text):
        the production composition passes the Dreamer's hot-reloading
        objective through it. `skill_versions_for` records what
        `skill_text_for` injected.

        `read_only_tools` is the allowlist `read_tools_for`'s result is
        held to (the composition passes the registry's own read-only set,
        `ToolRegistry.read_only_tools()`); empty, the default, refuses every
        read tool: a session with no declared read surface has none.
        `self_wakes_created_since`: how many `wake_at` calls the unit made
        since an instant (the per-day cap); None reads the dry-run store.

        `units` (every unit the evaluator may wake) must each have a home room
        and `budget.timeout_seconds` must end `SESSION_TIMEOUT_MARGIN_SECONDS`
        before `session_timeout_seconds` (the evaluator's
        `WakePolicy.session_timeout_seconds`, the wake's own clock): both are
        refused HERE, at startup, not discovered per wake.

        `person_known(person_id)` is the roster question a Rollup stimulus is
        judged readable by (`nora.domain.cognition_ground.rollup_status`); None
        knows everyone, so only a ref that does not parse is unreadable.

        `roster_of` and `nora_names` are what a note write is held to with the
        SAME validator the tag path runs (`nora.domain.note_body.parse_note_body`):
        the room's roster (an `## Owed` owner must be on it; None here: no roster
        is known, so every owner is refused) and the names Nora goes by (a line
        may not be hers, nor owed by her).

        `model_window_tokens` (None: unknown) and `overflow_window_fraction`
        are what a context overflow is PROVEN against (DEC-244): without a
        known window no overflow is ever the agenda's, and every one is
        `infra`."""
        check_home_rooms(units, home_rooms)
        check_session_timeouts(budget.timeout_seconds, session_timeout_seconds)
        self._store = dry_run_store
        self._stimuli = stimulus_store
        self._agent_runner = agent_runner
        self._home_rooms = home_rooms
        self._read_tools_for = read_tools_for
        self._grounder = grounder
        self._person_known = person_known or (lambda _person_id: True)
        self._render_budget = render_budget or RenderBudget()
        self._read_only_tools = read_only_tools
        self._skill_text_for = skill_text_for
        self._doctrine_text = doctrine_text
        self._doctrine_sha256 = doctrine_sha256
        self._doctrine_source = doctrine_source
        self._tz = tz
        self._skill_versions_for = skill_versions_for
        self._open_note_titles = open_note_titles
        self._open_events = open_events
        self._prior_standing = prior_standing
        self._roster_of = roster_of
        self._nora_names = frozenset(nora_names)
        self._pending_self_wakes = pending_self_wakes
        self._pending_mirrored_reminds = pending_mirrored_reminds
        # "off" makes this belt-and-braces only — see the module docstring;
        # the real gate is the composition root never wiring the hook.
        self._session_mode = session_mode
        self._budget = budget
        self._cooldown_seconds = cooldown_seconds
        self._max_items = max_items
        self._max_strikes = max_strikes
        self._max_self_wakes = max_self_wakes
        self._max_self_wakes_per_day = max_self_wakes_per_day
        self._self_wakes_created_since = self_wakes_created_since
        self._model_id = model_id
        self._model_route = model_route
        self._metrics = metrics
        self._mint_ref = mint_ref
        self._clock = clock
        self._window_tokens = model_window_tokens
        self._window_fraction = overflow_window_fraction
        #: Unit -> the stimulus ids of the agenda that last overflowed the
        #: model's window WITH the size evidence (in memory: a restart forgets
        #: it, which fails toward `infra`). A `closed` session clears it.
        self._overflowed: dict[str, frozenset[int]] = {}
        #: Wake id -> the task running its session, for `shutdown`.
        self._tasks: dict[int, asyncio.Task[Any]] = {}
        #: Outcome writes shielded from a second cancel (a `shutdown` and the
        #: evaluator's `cancel_in_flight` both cancelling one task), held so
        #: they are not garbage-collected while they finish.
        self._finishing: set[asyncio.Task[Any]] = set()
        #: One lock per unit around a pace or remind call's read-check-write:
        #: the model may issue two in one turn, and the pending cap and the
        #: dedupe rule are only sound if each sees the other's row.
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def wake_hook(self) -> WakeHook:
        """The evaluator's `on_wake`, typed by the ONE Protocol both sides
        use (`nora.domain.stimuli.WakeHook`); mypy holds this line to it."""
        return self.on_wake

    def _inc(self, name: str, **labels: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, **labels)

    def _lock(self, unit: str) -> asyncio.Lock:
        return self._locks.setdefault(unit, asyncio.Lock())

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def on_wake(
        self, *, unit: str, view: UnitView, now_ms: int, wake_id: int,
    ) -> CognitionSessionOutcome | None:
        """Called by the wake evaluator's hook on every would-wake it
        records (`WakeHook`). Returns `None` on every path that makes no model
        call; RAISES, after recording the wake's outcome, on every path that
        did not close. Every path writes the wake's outcome (or, if that write
        fails, leaves the wake OPEN until it expires): nothing here returns
        with the wake's stimuli consumed and unseen."""
        if self._session_mode != "shadow":
            # Belt and braces only (the module docstring): a wake no session
            # will run is released, not left to expire and re-wake forever.
            await self._finish(wake_id, unit, None, "failed", "session_mode is not shadow")
            return None
        task = asyncio.current_task()
        if task is not None:
            self._tasks[wake_id] = task
        session_id: int | None = None
        try:
            home_room = self._home_rooms.get(unit)
            if home_room is None:
                self._inc("nora_cognition_session_skipped_total", reason="no_home_room")
                raise MissingHomeRoomError(f"no home room configured for unit {unit!r}")
            # WHEN SHE LAST LOOKED is when the unit's last session ENDED, not the
            # newest message (`view.last_activity_ms` is how active the room
            # is): a clock line that says «you last looked» from a person's
            # message time tells her the wrong span (#B2, DEC-245). None: no
            # earlier session on record, or one that never ended.
            last_looked_ms = (
                None if view.last_session is None else view.last_session.ended_at_ms
            )
            # A Rollup stimulus a session cannot READ is not an agenda item
            # (DEC-245): until `rollup_read` exists a session judges from the
            # ref's fields alone, and an item it cannot read is one it would
            # answer wrong at a rate nothing reports. Readability is decided
            # from the stimulus and the roster (`rollup_status`), NEVER from
            # what the grounder built: a readable rollup the agenda cut at
            # `max_items` has no grounding line and is DEFERRED like any item.
            # Two kinds of unreadable, two fates, both RECORDED on the session
            # row (`agenda.skipped_stimulus_ids`) and counted:
            #   malformed   the ref can never be read: consumed.
            #   roster_miss a person the roster does not know YET: RETAINED, as a
            #               carry row with `waits`, so it is never consumed
            #               unseen and never raises the `rollup` trigger (a
            #               waiting stimulus does not wake), and it is an agenda
            #               item again in the first session after the roster
            #               knows the person.
            malformed: set[int] = set()
            retained: set[int] = set()
            for s in view.pending:
                if s.kind != "rollup":
                    continue
                status = rollup_status(s, self._person_known)
                if status == "malformed":
                    malformed.add(s.id)
                elif status == "roster_miss":
                    retained.add(s.id)
            skipped = frozenset(malformed | retained)
            pending = [s for s in view.pending if s.id not in skipped]
            if malformed:
                self._inc("nora_cognition_stimuli_skipped_total", reason="unreadable_rollup")
            if retained:
                self._inc("nora_cognition_stimuli_skipped_total", reason="roster_miss_rollup")
            retain_carries = [
                NewCarry(
                    unit=unit, stimulus_id=sid, strike=view.carry.get(sid, 0),
                    state="carried", session_id=None, waits=True,
                )
                for sid in sorted(retained)
            ]
            situation = build_situation(
                unit=unit, pending=pending, home_room=home_room, now_ms=now_ms,
                last_session_ms=last_looked_ms, max_items=self._max_items,
                strikes=view.carry,
            )
            if situation.agenda.is_empty:
                self._inc("nora_cognition_session_skipped_total", reason="empty_agenda")
                await self._finish(
                    wake_id, unit, None, "closed",
                    "empty agenda" if not skipped else
                    f"empty agenda: {len(skipped)} rollup stimulus(es) unreadable",
                    carries=retain_carries,
                )
                return None

            doctrine_text, doctrine_sha256 = self._doctrine()
            kinds = sorted({item.kind for item in situation.agenda.items})
            skill_versions = (
                {} if self._skill_versions_for is None
                else dict(self._skill_versions_for(kinds))
            )
            # GROUNDED AFTER THE SKIP, BEFORE ANYTHING IS RECORDED (30 §1, #637
            # review major 2): the agenda is final, so the grounding's per-item
            # index (her own sends) is the index the render reads, and an item
            # promoted from `deferred` by a skip is grounded. A read that
            # fails raises here, so the wake is recorded `failed` and its
            # stimuli stay pending: a session never runs on bare counts it
            # cannot judge from.
            grounding = await self._grounder(unit, situation.agenda.items, pending)
            session = NewCognitionSession(
                unit=unit, mode="shadow", started_at_ms=now_ms,
                from_stimulus_id=min(s.id for s in view.pending),
                through_stimulus_id=max(s.id for s in view.pending),
                situation={**situation.as_dict(), "grounding": grounding.as_dict()},
                agenda={
                    "items": [i.as_dict() for i in situation.agenda.items],
                    "deferred_stimulus_ids": list(situation.agenda.deferred_stimulus_ids),
                    "skipped_stimulus_ids": sorted(skipped),
                    "retained_stimulus_ids": sorted(retained),
                },
                doctrine_sha256=doctrine_sha256, skill_versions=skill_versions,
                config_revision_id=None, model_id=self._model_id,
                model_route=self._model_route, wake_id=wake_id,
            )
            session_id = await self._store.record_session(
                session=session, recorded_at_ms=now_ms,
            )
            self._inc("nora_cognition_session_total", mode="shadow")
            return await self._run_session(
                unit=unit, view=view, situation=situation, session_id=session_id,
                wake_id=wake_id, now_ms=now_ms, doctrine_text=doctrine_text,
                grounding=grounding, retained=sorted(retained),
            )
        except asyncio.CancelledError:
            await self._shielded_finish(
                wake_id, unit, session_id, "cancelled", "the session task was cancelled",
            )
            raise
        except SessionExpiredError:
            raise
        except SessionTimeoutError as exc:
            await self._finish(wake_id, unit, session_id, "timeout", str(exc))
            raise
        except Exception as exc:
            log.exception("cognition session failed (unit=%s wake=%s)", unit, wake_id)
            await self._finish(
                wake_id, unit, session_id, "failed", f"{type(exc).__name__}: {exc}"[:500],
                cause=classify_failure(exc),
            )
            raise
        finally:
            self._tasks.pop(wake_id, None)

    async def probe(
        self, *, unit: str, summary: str, now_ms: int, room: Room | None = None,
    ) -> CognitionRunResult:
        """One session over ONE hand-written agenda item: the seam the
        `tool_selection_cognition` corpus drives (`evals.rebuilt.entry`), whose
        cases are agenda-item sentences and not stimuli. It builds the same
        tools, holds the same instruction and calls the same agent runner as a
        wake's session, and it stops there: no wake, so no outcome, no carry
        and no fired self-wake; the session row and any dry-run rows go to the
        database it was handed (a sandbox's, in an eval). `session_mode` off
        refuses, so a probe cannot make the model call a deployment turned
        off."""
        if self._session_mode != "shadow":
            raise RuntimeError("probe needs session_mode = \"shadow\"")
        home = self._home_rooms.get(unit)
        if home is None:
            raise MissingHomeRoomError(f"no home room configured for unit {unit!r}")
        item = AgendaItem(
            kind="message", room=room or home, summary=summary, stimulus_ids=(),
            cues=(), surprise=0.0,
        )
        situation = Situation(
            unit=unit, now_ms=now_ms, last_session_ms=None, agenda=Agenda(items=(item,)),
        )
        doctrine_text, doctrine_sha256 = self._doctrine()
        session_id = await self._store.record_session(
            session=NewCognitionSession(
                unit=unit, mode="shadow", started_at_ms=now_ms, from_stimulus_id=0,
                through_stimulus_id=0, situation=situation.as_dict(),
                agenda={"items": [item.as_dict()], "deferred_stimulus_ids": []},
                doctrine_sha256=doctrine_sha256, skill_versions={},
                model_id=self._model_id, model_route=self._model_route, wake_id=None,
            ),
            recorded_at_ms=now_ms,
        )
        state = _RunState(
            session_id=session_id, wake_id=0, unit=unit, now_ms=now_ms,
            items_by_index={0: item},
            rooms={_room_key(item.room): (item.room.platform, item.room.conversation_id)},
        )
        tools = await self._build_tools(unit=unit, state=state, now_ms=now_ms)
        return await self._agent_runner.run(
            system_prompt=doctrine_text + self._skill_text_for([item.kind]),
            prompt=render_agenda(
                [item], reads_note=state.reads_note, now_ms=now_ms,
                last_session_ms=None, tz=self._tz, budget=self._render_budget,
            ),
            tools=tools, budget=self._budget,
            trace_attributes={"cognition.unit": unit, "cognition.session_id": str(session_id)},
        )

    def _doctrine(self) -> tuple[str, str | None]:
        if self._doctrine_source is not None:
            return self._doctrine_source()
        return self._doctrine_text, self._doctrine_sha256

    async def shutdown(self) -> None:
        """Cancel every session still in flight and wait for it to record
        itself `cancelled`. A session must not vanish with its wake still
        reading «in flight» when it could have said otherwise."""
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._finishing:
            await asyncio.gather(*tuple(self._finishing), return_exceptions=True)

    async def _shielded_finish(
        self, wake_id: int, unit: str, session_id: int | None, outcome: str, detail: str,
    ) -> None:
        """`_finish`, shielded: a SECOND cancel (a `shutdown` and the
        evaluator's `cancel_in_flight` both cancelling this task) interrupts
        the wait, never the write."""
        write = asyncio.ensure_future(self._finish(wake_id, unit, session_id, outcome, detail))
        self._finishing.add(write)
        write.add_done_callback(self._finishing.discard)
        await asyncio.shield(write)

    async def _finish(
        self, wake_id: int, unit: str, session_id: int | None, outcome: str, detail: str,
        carries: Sequence[NewCarry] = (), cause: str = "infra",
    ) -> RecordedOutcome | None:
        """Record the wake's outcome, at the REAL clock: the cooldown runs
        from here, not from the wake, so a long session is not already
        «cooled» when it finishes. A failure outcome whose write fails is
        logged and left: the wake stays OPEN until it expires, which reads as
        failed, and it never advances the watermark. A `closed` write that
        fails RAISES (its caller records `failed` instead)."""
        ended_ms = self._now_ms()
        try:
            recorded = await self._stimuli.record_outcome(
                outcome=NewWakeOutcome(
                    wake_id=wake_id, unit=unit, outcome=outcome, at_ms=ended_ms,
                    detail=detail, session_id=session_id,
                    # A session that closed or was cancelled did not FAIL: it has
                    # no cause (m2), never `infra`.
                    cause=cause if outcome in ("failed", "timeout") else NO_CAUSE,
                ),
                carries=carries, recorded_at_ms=ended_ms,
            )
        except Exception:
            if outcome == "closed":
                raise
            log.exception("could not record the outcome of cognition wake %s", wake_id)
            self._inc("nora_errors_total", stage="cognition_session")
            return None
        if recorded.written:
            self._inc("nora_cognition_session_ended_total", outcome=recorded.outcome)
        return recorded

    async def _run_session(
        self, *, unit: str, view: UnitView, situation: Situation, session_id: int,
        wake_id: int, now_ms: int, doctrine_text: str, grounding: Grounding,
        retained: Sequence[int] = (),
    ) -> CognitionSessionOutcome:
        items = situation.agenda.items
        state = _RunState(
            session_id=session_id, wake_id=wake_id, unit=unit, now_ms=now_ms,
            items_by_index=dict(enumerate(items)),
            rooms={_room_key(i.room): (i.room.platform, i.room.conversation_id) for i in items},
            # Her own sends in an item's window are SHOWN too (D1, DEC-245), marked
            # as hers, so a note citing one is refused `cites_nora`, not `unknown_id`.
            records=(
                *grounding.records.values(),
                *(r for sends in grounding.own_sends.values() for r in sends),
            ),
        )
        tools = await self._build_tools(
            unit=unit, state=state, now_ms=now_ms, labels=grounding.room_labels,
        )
        kinds = sorted({item.kind for item in items})
        system_prompt = doctrine_text + self._skill_text_for(kinds)
        prompt = render_agenda(
            items, stimuli={s.id: s for s in view.pending}, grounding=grounding,
            reads_note=state.reads_note, now_ms=now_ms,
            last_session_ms=situation.last_session_ms, tz=self._tz,
            budget=self._render_budget,
        )
        try:
            result = await asyncio.wait_for(
                self._run_agent(
                    unit=unit, pending_ids=frozenset(s.id for s in view.pending),
                    system_prompt=system_prompt, prompt=prompt, tools=tools,
                    trace_attributes={
                        "cognition.unit": unit, "cognition.session_id": str(session_id),
                        # The agenda's size, for the trace (no call ceiling
                        # scales by it: DEC-247).
                        "cognition.items": str(len(items)),
                    },
                ),
                timeout=self._budget.timeout_seconds,
            )
        except TimeoutError as exc:
            raise SessionTimeoutError(
                f"the session ran past its {self._budget.timeout_seconds:g}s wall clock",
            ) from exc

        if result.stop_reason in LIMIT_STOP_REASONS:
            # The loop RETURNED, but it was cut off at a bound (DEC-247), so it
            # never decided what to do with the items it had not reached: a
            # FAILED session, cause `infra`. No close, no strike, no carry (a
            # `_settle` here would strike a human's message for the model's
            # runaway), its stimuli stay pending and the failure backoff
            # applies. Writes it already made stay as recorded.
            self._inc("nora_cognition_session_limit_total", limit=result.stop_reason)
            raise SessionLimitError(result.stop_reason or "")

        carries = await self._settle(state=state, situation=situation, view=view)
        # A Rollup stimulus the roster cannot read YET is retained, not consumed
        # (see `on_wake`): it waits, and returns as an item once it is readable.
        carries.extend(
            NewCarry(
                unit=unit, stimulus_id=sid, strike=view.carry.get(sid, 0),
                state="carried", session_id=session_id, waits=True,
            )
            for sid in retained
        )
        recorded = await self._finish(
            wake_id, unit, session_id, "closed", result.stop_reason or "", carries=carries,
        )
        if recorded is None or recorded.outcome != "closed":
            raise SessionExpiredError(
                f"wake {wake_id} was recorded "
                f"{'nothing' if recorded is None else recorded.outcome!r}, not closed: "
                "it expired and a later wake took its stimuli",
            )
        self._overflowed.pop(unit, None)
        for carry in carries:
            self._inc("nora_cognition_carry_total", state=carry.state)
        await self._record_fired(state=state, situation=situation, view=view)
        return CognitionSessionOutcome(
            session_id=session_id, items=len(items), closed=tuple(state.closes),
            stop_reason=result.stop_reason,
        )

    async def _run_agent(
        self, *, unit: str, pending_ids: frozenset[int], system_prompt: str, prompt: str,
        tools: list[Any], trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        """The model loop, with ONE translation: a context overflow PROVEN to be
        the agenda's (`prove_overflow_is_the_agenda`: no transport evidence, the
        prompt at least `overflow_window_fraction` of a KNOWN window, and the
        same agenda, or one it contains, overflowed on an earlier attempt)
        becomes an `AgendaError`, the only thing `classify_failure` calls
        `agenda`. Anything else propagates as raised and is `infra`."""
        try:
            return await self._agent_runner.run(
                system_prompt=system_prompt, prompt=prompt, tools=tools,
                budget=self._budget, trace_attributes=trace_attributes,
            )
        except Exception as exc:
            if not is_context_overflow(exc):
                # Any other failure (an outage, a cut reply) breaks the run:
                # "twice in a row" means consecutive overflows.
                self._overflowed.pop(unit, None)
                raise
            previous = self._overflowed.get(unit)
            estimate = estimate_tokens_lower_bound(system_prompt, prompt)

            def proves(*, repeated: bool, error: Exception = exc) -> bool:
                return prove_overflow_is_the_agenda(
                    error, estimated_tokens=estimate, window_tokens=self._window_tokens,
                    window_fraction=self._window_fraction, previously_overflowed=repeated,
                )

            # THIS overflow is evidence (a size fault on a big prompt, no
            # transport sign) whether or not it is the second one.
            if proves(repeated=True):
                self._overflowed[unit] = pending_ids
            else:
                self._overflowed.pop(unit, None)
            if proves(repeated=previous is not None and previous <= pending_ids):
                raise AgendaError(
                    f"the agenda overflowed the model window twice (about {estimate} "
                    f"tokens against {self._window_tokens}): {exc}",
                ) from exc
            raise

    async def _settle(
        self, *, state: _RunState, situation: Situation, view: UnitView,
    ) -> list[NewCarry]:
        """What a session that ENDED (not one cut off at a bound: `_run_session`
        raises `SessionLimitError` before this, DEC-247) leaves behind (30 §2):
        every item the model did not close closes `waiting` with a strike (or `struck` at
        `max_strikes`); a waiting or struck item's stimuli, and the ones past
        `max_items`, are RETURNED to the next agenda as carry rows, because the
        watermark now moves past them. The carries are RETURNED, not written:
        they land in the same transaction as the wake's `closed` outcome, or
        not at all. A strike is the ITEM's (every stimulus of an item shares
        it, `build_agenda` groups by it), so a fresh stimulus never inherits
        the strike of a returning one."""
        items = situation.agenda.items
        for index, item in enumerate(items):
            if index in state.closed_indices:
                continue
            outcome = close_or_strike(
                item, item_index=index, reason="waiting", max_strikes=self._max_strikes,
            )
            await self._record_close(state, outcome)

        carries: list[NewCarry] = []

        def carry(sid: int, strike: int, carry_state: str, *, waits: bool = False) -> None:
            carries.append(NewCarry(
                unit=state.unit, stimulus_id=sid, strike=strike, state=carry_state,
                session_id=state.session_id, waits=waits,
            ))

        by_index = {c.item_index: c for c in state.closes}
        for index, item in enumerate(items):
            close = by_index[index]
            for sid in item.stimulus_ids:
                if close.reason == "waiting":
                    carry(sid, close.strike, "carried", waits=True)
                elif close.reason == "struck":
                    carry(sid, close.strike, "struck")
                elif sid in view.carry:
                    carry(sid, view.carry[sid], "released")

        for sid in situation.agenda.deferred_stimulus_ids:
            strike = view.carry.get(sid, 0) + 1
            carry(sid, strike, "struck" if strike >= self._max_strikes else "carried")
        return carries

    async def _record_fired(
        self, *, state: _RunState, situation: Situation, view: UnitView,
    ) -> None:
        """A self-wake on the agenda of a session that CLOSED is recorded
        `fired` (it frees its pending slot). After the close, never before: a
        session that did not close has not seen it, and its stimulus is
        pending again. A failed write is logged; the wake then reads pending
        until its ref leaves the ledger's read window."""
        by_id = {s.id: s for s in view.pending}
        fired: set[str] = set()
        for item in situation.agenda.items:
            if item.kind != "self":
                continue
            for sid in item.stimulus_ids:
                row = by_id.get(sid)
                if row is not None and row.kind == "self" and row.item not in fired:
                    fired.add(row.item)
                    try:
                        await dry_run_wake_fire(
                            ledger=self._store, session_id=state.session_id,
                            unit=state.unit, wake_ref=row.item, now_ms=self._now_ms(),
                        )
                    except Exception:
                        log.exception("could not record self-wake %s fired", row.item)
                        self._inc("nora_errors_total", stage="cognition_session")

    async def _record_close(self, state: _RunState, outcome: CloseOutcome) -> None:
        await self._store.record_close(
            close=NewCognitionSessionClose(
                session_id=state.session_id, item_index=outcome.item_index,
                kind=outcome.kind, reason=outcome.reason, strike=outcome.strike,
            ),
            recorded_at_ms=state.now_ms,
        )
        state.closed_indices.add(outcome.item_index)
        state.closes.append(outcome)

    # -- tools --------------------------------------------------------------

    def _guarded_read_tools(self, unit: str, rooms: Sequence[Room]) -> list[Any]:
        """`read_tools_for`'s result, refused unless every tool is on the
        read-only allowlist and none shadows a session verb. The session's
        ceiling is «may look, may not write»; a read-tool source that ever
        returned a write is stopped here, not trusted."""
        tools = self._read_tools_for(unit, rooms)
        seen: set[str] = set()
        for tool in tools:
            name = getattr(tool, "tool_name", None) or getattr(tool, "__name__", None)
            if isinstance(name, str):
                if name in seen:
                    raise NotReadOnlyToolError(f"read tool {name!r} was handed twice")
                seen.add(name)
            if not isinstance(name, str) or name not in self._read_only_tools:
                raise NotReadOnlyToolError(
                    f"tool {name!r} is not on the read-only allowlist; a cognition "
                    "session holds read tools and its own dry-run verbs only",
                )
            if name in SESSION_VERB_NAMES:
                raise NotReadOnlyToolError(f"read tool {name!r} shadows a session verb")
        return tools

    async def _build_tools(
        self, *, unit: str, state: _RunState, now_ms: int,
        labels: Mapping[str, str] | None = None,
    ) -> list[Any]:
        # EACH ITEM'S READS RESOLVE THROUGH ITS OWN ROOM (30 §1, DEC-231, R-21;
        # #637 review, major 3). One room on the agenda: that room's tools, as
        # ever. Several: `read_tools_for` returns ONE tool per name (a tool name
        # is one tool: Strands keeps the LAST of two silently, which read one
        # room's Record through another room's binding without a word), and
        # each takes a `room` argument routing the read through that room's
        # own build; a `room` value not on the agenda is refused. The unit is the
        # boundary the session may read across (DEC-231: `record_query
        # scope="all_rooms"` and `search_memory_workspace` read the unit's other
        # rooms), the `room` argument picks the binding (roster, lineage) a read
        # resolves through. The agenda's rooms are never replaced by the unit's
        # home room.
        rooms = list(dict.fromkeys(item.room for item in state.items_by_index.values()))
        keys = [_room_key(r) for r in rooms]
        # The prompt calls a room by its LABEL (m5); the id is what a tool's
        # `room` argument takes, so it appears here, once, beside the label it
        # belongs to, and nowhere in an item.
        named = [room_label(r, labels or {}) for r in rooms]
        state.reads_note = (
            f"Your read tools look through room {named[0]}."
            if len(keys) == 1
            else "Your read tools take a `room`, one of: "
            + ", ".join(f"`{k}` ({n})" for k, n in zip(keys, named, strict=True))
            + ". Each read resolves through the room you name and shows only what that "
            "room may be shown; a room that is not listed is refused."
        )
        # The reads that hand out citable rows are watched: what they return is
        # what a note may cite besides the agenda's own rows (m4).
        read_tools = watch_reads(self._guarded_read_tools(unit, rooms), state.fetched, keys)

        async def shown_for(
            platform: str, conversation_id: str, body: str,
        ) -> Shown | dict[str, object]:
            """What a write in this room may cite and name, built from what the
            session was actually shown and fetched, so the tag path's own
            validator runs on it (M3, m4): the rows of THIS room the agenda
            raised (id, author, person, whether they are Nora's), the rows
            `record_query` returned, the ids an existing note cites, the Rollup
            rows read, the room's roster and Nora's names. An unreadable
            roster is `unavailable`, never an empty room that refuses every
            owner."""
            here = Room(platform, conversation_id)
            fetched = state.fetched.of(_room_key(here))
            rendered: dict[int, ShownMessage] = {
                r.message_id: ShownMessage(
                    id=r.message_id, sender=r.author, person_id=r.person_id,
                    is_nora=r.own or r.author in self._nora_names,
                )
                for r in state.records
                if r.room == here and r.kind in ("message", "edit") and r.text is not None
            }
            for n, sender in fetched.messages.items():
                rendered.setdefault(n, ShownMessage(
                    id=n, sender=sender, person_id=None, is_nora=sender in self._nora_names,
                ))
            roster: Mapping[str, str] = {}
            if self._roster_of is not None and _OWED_HEADING.search(body):
                got = await self._roster_of(platform, conversation_id)
                if got is None:
                    return {
                        "status": "unavailable", "reason": "roster",
                        "detail": "this room's roster could not be read, so who a line is "
                                  "owed by cannot be checked; say so and try again later",
                    }
                roster = got
            return Shown(
                rendered=tuple(rendered.values()),
                carried=frozenset(fetched.carried_ids),
                roster=roster, nora_names=self._nora_names,
                rollup_keys=frozenset(fetched.rollup_keys),
            )

        def wrote(
            platform: str, conversation_id: str, write: Any, item: int | None,
        ) -> dict[str, object]:
            """The verb's answer, and what backs a `written` close: only a
            write that would commit counts, and it backs the item it names
            (B3)."""
            if write.outcome == "would_commit":
                state.written.add((_room_key(Room(platform, conversation_id)), item))
            return _verb_result(write)

        established_by = f"{COGNITION_PATH_PREFIX}{unit}"

        async def close(
            item_index: int, reason: Literal["written", "no_change", "waiting", "covered"],
        ) -> dict[str, object]:
            """Close one agenda item. Call this when you have dealt with an item
            or decided it needs nothing now. An item you leave open, or close
            `waiting`, comes back on the next agenda with a strike, and code
            closes it for good after a few.

            Returns `status`: ok or refused, with a `reason` when refused
            (`written` with no write of yours for that item is refused).

            Args:
                item_index: The item's number in the agenda.
                reason: `written` when a write you made for it covers it (one
                    that was not refused, and named this item when its room
                    holds several), `no_change` when nothing about it
                    needs to change, `waiting` when it cannot be settled yet,
                    `covered` when another item already handles it.
            """
            if item_index in state.closed_indices:
                return {"status": "refused", "reason": "already_closed"}
            item = state.items_by_index.get(item_index)
            if item is None:
                return {"status": "refused", "reason": "no_such_item"}
            if reason == "written" and not _written_for(item_index, item, state):
                # A close is measured against what was written, never against the
                # model's say-so (B3): `written` needs a write this session made
                # FOR this item that code did not refuse: one that names it, or,
                # when it is the only item in its room, the room's write.
                return {
                    "status": "refused", "reason": "no_write_for_item",
                    "detail": "no write of yours for this item was accepted in this "
                              "session (name it as the write's `item`); write it first, or "
                              "close it `no_change`, `waiting` or `covered`",
                }
            try:
                outcome = close_or_strike(
                    item, item_index=item_index, reason=reason,
                    max_strikes=self._max_strikes,
                )
            except ValueError as exc:
                return {"status": "refused", "reason": "bad_reason", "detail": str(exc)}
            await self._record_close(state, outcome)
            return {"status": "ok"}

        async def note_create(
            title: str, description: str, uncertainty: str, closing_condition: str,
            body: str, anchor_date: str | None = None,
            anchor_entities: list[str] | None = None, room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Start carrying an open question that nothing here settles yet.
            Call this when a room is chewing on something that outlives this
            pass.

            A note is tracked uncertainty (whose wedding is when, what the trip
            still lacks, who has not paid), never a settled fact or a
            description of a person. If a note already covers it, update that
            one instead. It MUST have a `closing_condition` (what would settle
            it) and an anchor (`anchor_date`, `anchor_entities` or both), or it
            is refused. In this run the write is recorded, not committed.

            Returns `status`: ok or refused, with a `reason` and `detail` when
            refused.

            Args:
                title: A short handle, at most 60 characters, that names the
                    thing.
                description: One line, at most 200 characters: what is
                    undecided and what would settle it.
                uncertainty: What exactly is not known yet, in a sentence.
                closing_condition: What would settle it.
                body: What is known so far, in sections: ## Decided and ## Open
                    always, then ## Owed, ## Proposed or ## Ruled out when one
                    has a line. Each line is one "- " item ending in the ids of
                    the messages it rests on, as [<id>, <id>]: a message you
                    were shown or read, or a Rollup row you read, cited by
                    rollup(kind|subject|from/to) from its row. A body that
                    breaks this is refused with the rule it broke.
                anchor_date: Optional. The calendar day it is about, as an ISO
                    date (year-month-day).
                anchor_entities: Optional. The people or things it is about,
                    named the way the room names them.
                room: Optional when the agenda is one room, required when it
                    spans several. The room this note is about, as
                    `platform:conversation_id` from the agenda.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            anchor_day: int | None = None
            if anchor_date:
                anchor_day = _day_number(anchor_date)
                if anchor_day is None:
                    return {"status": "refused", "reason": "bad_anchor_date",
                            "detail": "anchor_date must be an ISO date, year-month-day"}
            shown = await shown_for(platform, conversation_id, body)
            if isinstance(shown, dict):
                return shown
            open_titles = tuple(await self._open_note_titles(unit))
            write = await dry_run_note_create(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id,
                open_titles=open_titles, now_ms=now_ms, established_by=established_by,
                shown=shown, title=title, description=description,
                uncertainty=uncertainty, closing_condition=closing_condition, body=body,
                anchor_date=anchor_day, anchor_entity_ids=tuple(anchor_entities or ()),
            )
            return wrote(platform, conversation_id, write, item)

        async def note_update(
            note_id: str, body: str, parent_revision_id: str,
            entity_ids: list[str] | None = None, room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Revise a note. Call this when a note's content changes: a detail
            settles, a plan moves. Give back the WHOLE note with your change
            folded in: `body` replaces the current contents, and the old
            revision is kept. In this run the write is recorded, not committed.

            Returns `status`: ok or refused, with a `reason` and `detail` when
            refused.

            Args:
                note_id: The note's id.
                body: The whole note after your change, in note_create's
                    sections, every line ending in the message ids it rests on,
                    as [<id>, <id>], or a Rollup row you read, as
                    rollup(kind|subject|from/to).
                parent_revision_id: The note's head revision id your last read
                    of it showed. A stale one is refused.
                entity_ids: Optional. The people or things the note is about.
                room: Optional when the agenda is one room, required when it
                    spans several. The room this note is about, as
                    `platform:conversation_id` from the agenda.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            shown = await shown_for(platform, conversation_id, body)
            if isinstance(shown, dict):
                return shown
            write = await dry_run_note_update(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, now_ms=now_ms,
                established_by=established_by, shown=shown, note_id=note_id, body=body,
                parent_revision_id=parent_revision_id,
                entity_ids=tuple(entity_ids or ()),
            )
            return wrote(platform, conversation_id, write, item)

        async def event_propose(
            title: str, reasoning: Literal["told", "deduced", "abduced"],
            confidence: Literal["low", "medium", "high"], cited_ids: list[str],
            when: str = "", venue_text: str = "",
            status: Literal["staged", "proposed"] = "proposed", room: str = "",
            item: int | None = None,
        ) -> dict[str, object]:
            """Propose a NEW gathering in a room, returning its id. Call this
            when a room wants to arrange something that is not open yet. If an
            event is already open (proposed) there, revise it with event_revise
            instead of creating a second one. In this run the write is
            recorded, not committed.

            Every field you give rests on the rows you cite. An `abduced` value
            (your best explanation of something nobody said) is written as
            abduced and is put to the room as a question, and a yes to it later
            does not make it told.

            Returns `status`: ok or refused, with a `reason` and `detail` when
            refused.

            Args:
                title: What the gathering is, in their words.
                reasoning: How you reached what you give: `told` (a person said
                    it, or members agreed among themselves), `deduced` (it
                    follows from rows you hold) or `abduced`.
                confidence: How sure you are: low, medium or high.
                cited_ids: The rows it rests on, at least one: message ids you
                    were shown or read, as digits, and Rollup rows you read, as
                    rollup(kind|subject|from/to).
                when: Optional. ISO 8601 with an offset, the first moment it is
                    for. Work out relative time yourself.
                venue_text: Optional. The place, the way it was said.
                status: `proposed` when the room is settling it, `staged` when
                    you only suspect it is an event.
                room: Optional when the agenda is one room, required when it
                    spans several. The room the gathering is in, as
                    `platform:conversation_id` from the agenda.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            due = _parse_when(when) if when else None
            if when and due is None:
                return {"status": "refused", "reason": "bad_when",
                        "detail": "when must be ISO 8601 with an offset"}
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            created = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
            # The backstop counts PROPOSED events only: a staged one is a guess
            # and a confirmed one is settled, neither is a second open proposal.
            proposed = [
                e for e in await self._open_events(unit, conversation_id)
                if e.status is GatheringStatus.PROPOSED
            ]
            write = await dry_run_event_propose(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, now_ms=now_ms,
                shown=shown, reasoning=reasoning, confidence=confidence,
                cited_ids=cited_ids, open_proposed_events=tuple(proposed),
                id=uuid.uuid4(), title=title, created_by=established_by, when=due,
                venue_text=venue_text or None, venue_place_id=None, venue_name=None,
                venue_address=None, venue_rating=None, venue_price_level="",
                venue_maps_url=None, venue_resolved_at=None,
                status=GatheringStatus(status), reminder_id=None, created_at=created,
            )
            return wrote(platform, conversation_id, write, item)

        async def _current_event(
            conversation_id: str, event_id: str,
        ) -> Gathering | None:
            # Every event still open (staged, proposed, confirmed), so a
            # confirmed one can move on to happened or cancelled (M4).
            open_events = await self._open_events(unit, conversation_id)
            return next((e for e in open_events if str(e.id) == event_id), None)

        async def event_revise(
            event_id: str, reasoning: Literal["told", "deduced", "abduced"],
            confidence: Literal["low", "medium", "high"], cited_ids: list[str],
            when: str = "", venue_text: str = "",
            base_revision_id: str = "", room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Change the time or venue of an event that is still open in a
            room (staged, proposed or confirmed). Call this when an open
            event's details are settled or change. In this run the write is
            recorded, not committed.

            What you change rests on the rows you cite, and an `abduced` value
            is written as abduced and put as a question. One write has one
            reasoning: if the time and the place rest on different evidence,
            make two writes.

            Returns `status`: ok or refused, with a `reason` and `detail` when
            refused.

            Args:
                event_id: The event's id.
                reasoning: How you reached what you change: `told`, `deduced`
                    or `abduced`, as event_propose says.
                confidence: How sure you are: low, medium or high.
                cited_ids: The rows it rests on, at least one: message ids as
                    digits, Rollup rows as rollup(kind|subject|from/to), each
                    one you were shown or read.
                when: Optional. ISO 8601 with an offset.
                venue_text: Optional. The place, the way it was said.
                base_revision_id: Optional. The event's `head_revision_id` your
                    last read of it showed. Leave it out to write regardless
                    of what changed since.
                room: Optional when the agenda is one room, required when it
                    spans several. The event's room, as
                    `platform:conversation_id` from the agenda.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            changes: dict[str, Any] = {}
            if when:
                due = _parse_when(when)
                if due is None:
                    return {"status": "refused", "reason": "bad_when",
                            "detail": "when must be ISO 8601 with an offset"}
                changes["when"] = due
            if venue_text:
                changes["venue_text"] = venue_text
            if not changes:
                return {"status": "refused", "reason": "nothing_to_change",
                        "detail": "give a `when` or a `venue_text`"}
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            current = await _current_event(conversation_id, event_id)
            write = await dry_run_event_revise(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, current=current,
                base_revision_id=base_revision_id or None, now_ms=now_ms, shown=shown,
                reasoning=reasoning, confidence=confidence, cited_ids=cited_ids,
                **changes,
            )
            return wrote(platform, conversation_id, write, item)

        async def event_set_status(
            event_id: str, status: Literal["confirmed", "cancelled", "happened", "lapsed"],
            reasoning: Literal["told", "deduced", "abduced"],
            confidence: Literal["low", "medium", "high"], cited_ids: list[str],
            base_revision_id: str = "", room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Move an event that is still open in a room (staged, proposed or
            confirmed) to a new state. Call this when the room confirms it,
            says it happened, or says it is off. In this run the write is
            recorded, not committed.

            The move rests on the rows you cite; an `abduced` one is written as
            abduced and put as a question ("did it happen?"). A move to `lapsed`
            (the date passed with nothing agreed) may cite nothing, since the
            evidence is the clock. Only a status before `happened` moves: an
            event that happened, was cancelled or lapsed stays as it is.

            Returns `status`: ok or refused, with a `reason` and `detail` when
            refused.

            Args:
                event_id: The event's id.
                status: The state to move it to.
                reasoning: How you reached it: `told`, `deduced` or `abduced`,
                    as event_propose says.
                confidence: How sure you are: low, medium or high.
                cited_ids: The rows it rests on: message ids as digits, Rollup
                    rows as rollup(kind|subject|from/to), each one you were
                    shown or read.
                base_revision_id: Optional. The event's `head_revision_id` your
                    last read of it showed. Leave it out to write regardless
                    of what changed since.
                room: Optional when the agenda is one room, required when it
                    spans several. The event's room, as
                    `platform:conversation_id` from the agenda.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            try:
                parsed = GatheringStatus(status)
            except ValueError:
                return {"status": "refused", "reason": "bad_status",
                        "detail": "use confirmed, cancelled, happened or lapsed"}
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            current = await _current_event(conversation_id, event_id)
            write = await dry_run_event_set_status(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, current=current,
                status=parsed, base_revision_id=base_revision_id or None, now_ms=now_ms,
                shown=shown, reasoning=reasoning, confidence=confidence, cited_ids=cited_ids,
            )
            return wrote(platform, conversation_id, write, item)

        async def standing_revise(
            where_it_stands: str, hold_open: str | None = None, i_owe: str | None = None,
            mode: Literal["deduced", "abduced", "said"] = "deduced",
            base_revision_id: str | None = None, room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Revise a room's Standing: where it stands right now. Call this
            when what a room has settled, or is holding open, has changed. Give
            the whole position, not a delta. In this run the write is recorded,
            not committed.

            Returns `status`: ok or refused, with a `reason` and `detail` when
            refused.

            Args:
                where_it_stands: The position, in a short paragraph. Required.
                hold_open: Optional. What you are deliberately holding open.
                i_owe: Optional. What you owe the room.
                mode: How you came to it: `deduced` from what was said,
                    `abduced` as a best guess, `said` when it rests only on
                    your own earlier words.
                base_revision_id: Optional. The `revision` standing_read showed
                    for this room's Standing. A stale one is refused.
                room: Optional when the agenda is one room, required when it
                    spans several. The room, as `platform:conversation_id`
                    from the agenda.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            head = await self._prior_standing(platform, conversation_id)
            write = await dry_run_standing_revise(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id,
                prior=None if head is None else head.standing,
                prior_revision_id=None if head is None else head.revision_id,
                where_it_stands=where_it_stands, hold_open=hold_open, i_owe=i_owe,
                mode=mode, base_revision_id=base_revision_id, now_ms=now_ms,
            )
            return wrote(platform, conversation_id, write, item)

        async def wake_at(
            subject_rows: list[str], at: str, why: str,
        ) -> dict[str, object]:
            """Ask for your own next look at some rows, at a time you name. Call
            this at the end of a pass when something will be worth checking
            again later ("the event is at 20:00, look at 19:40"). Nothing is
            sent: it only wakes you. A new one that shares a subject row with a
            pending one replaces it.

            Returns `status`: ok (with the `wake_id` to move or cancel it) or
            refused (too soon, too far, no subject, too many pending or too
            many today).

            Args:
                subject_rows: The rows to look at again, at least one.
                at: When, ISO 8601 with an offset. No sooner than the cooldown
                    from now and no later than seven days.
                why: The reason you ask, in a sentence.
            """
            at_ms = _when_ms(at)
            if at_ms is None:
                return _bad_time("at")
            async with self._lock(unit):
                # The REAL clock, not the session's start: a session can run
                # for minutes, and the per-day window and the horizon are
                # about when this call is made.
                call_ms = self._now_ms()
                pending = await self._pending(unit, call_ms)
                created_today = await self._created_since(unit, call_ms - _DAY_MS)
                wake_ref = self._mint_ref()
                answer = await dry_run_wake_at(
                    ledger=self._store, stimuli=self._stimuli, session_id=state.session_id,
                    unit=unit, subject_rows=subject_rows, at_ms=at_ms, why=why,
                    now_ms=call_ms, wake_ref=wake_ref,
                    cooldown_seconds=self._cooldown_seconds, pending=pending,
                    max_self_wakes=self._max_self_wakes, created_today=created_today,
                    max_self_wakes_per_day=self._max_self_wakes_per_day,
                )
            return _answer(answer, wake_id=wake_ref)

        async def wake_move(wake_id: str, at: str, why: str) -> dict[str, object]:
            """Move a self-wake you set to a new time. Call this when the time
            you chose no longer fits. The old time no longer wakes you.

            Returns `status`: ok or refused (not found, not yours, too soon or
            too far).

            Args:
                wake_id: The id `wake_at` returned.
                at: The new time, ISO 8601 with an offset.
                why: The reason you move it.
            """
            at_ms = _when_ms(at)
            if at_ms is None:
                return _bad_time("at")
            async with self._lock(unit):
                call_ms = self._now_ms()
                pending = await self._pending(unit, call_ms)
                answer = await dry_run_wake_move(
                    ledger=self._store, stimuli=self._stimuli,
                    session_id=state.session_id, unit=unit, wake_ref=wake_id, at_ms=at_ms,
                    why=why, now_ms=call_ms, cooldown_seconds=self._cooldown_seconds,
                    pending=pending, created_by_session_id=None,
                )
            return _answer(answer)

        async def wake_cancel(wake_id: str, why: str) -> dict[str, object]:
            """Cancel a self-wake you set. Call this when there is no longer
            anything to look at. It will not wake you.

            Returns `status`: ok or refused (not found or not yours).

            Args:
                wake_id: The id `wake_at` returned.
                why: The reason you cancel it.
            """
            async with self._lock(unit):
                call_ms = self._now_ms()
                pending = await self._pending(unit, call_ms)
                answer = await dry_run_wake_cancel(
                    ledger=self._store, stimuli=self._stimuli,
                    session_id=state.session_id, unit=unit, wake_ref=wake_id, why=why,
                    now_ms=call_ms, pending=pending, created_by_session_id=None,
                )
            return _answer(answer)

        async def remind(
            intent: Literal["tell", "ask"], subject_rows: list[str], at: str,
            room: str = "",
        ) -> dict[str, object]:
            """Reach a room at a time: a reminder that says something to it or
            asks it something. Call this when a room should hear from you and
            not before. In this run it is recorded and held, never sent.

            Returns `status`: ok (with the `reminder_id`) or refused (no
            citation, a bad intent, a room not on the agenda, a bad time or an
            identical one already pending).

            Args:
                intent: `tell` to say something, `ask` to ask something.
                subject_rows: The rows it rests on, at least one.
                at: When to reach the room, ISO 8601 with an offset.
                room: Optional when the agenda is one room, required when it
                    spans several. The room to reach, as
                    `platform:conversation_id` from the agenda.
            """
            resolved = _room_of(room, state)
            if isinstance(resolved, dict):
                return resolved
            room_platform, room_conversation_id = resolved
            at_ms = _when_ms(at)
            if at_ms is None:
                return _bad_time("at")
            async with self._lock(unit):
                pending = await self._pending_mirrored_reminds(unit)
                ref = self._mint_ref()
                answer = await dry_run_remind(
                    ledger=self._store, session_id=state.session_id, unit=unit,
                    reminder_ref=ref, platform=room_platform,
                    conversation_id=room_conversation_id, intent=intent,
                    subject_rows=subject_rows, at_ms=at_ms, now_ms=now_ms, pending=pending,
                )
            return _answer(answer, reminder_id=ref)

        async def remind_move(
            reminder_id: str, at: str, why: str,
        ) -> dict[str, object]:
            """Move a reminder you set to a new time. Call this when the time
            you chose no longer fits. Only your own.

            Returns `status`: ok or refused (not found, not yours, or its room is
            not on this agenda).

            Args:
                reminder_id: The id `remind` returned.
                at: The new time, ISO 8601 with an offset.
                why: The reason you move it.
            """
            at_ms = _when_ms(at)
            if at_ms is None:
                return _bad_time("at")
            async with self._lock(unit):
                pending = await self._pending_mirrored_reminds(unit)
                platform, conversation_id = next(
                    ((p.platform, p.conversation_id) for p in pending
                     if p.reminder_ref == reminder_id), ("", ""),
                )
                if platform and f"{platform}:{conversation_id}" not in state.rooms:
                    return _off_agenda(state)
                answer = await dry_run_remind_move(
                    ledger=self._store, session_id=state.session_id, unit=unit,
                    reminder_ref=reminder_id, at_ms=at_ms, why=why, platform=platform,
                    conversation_id=conversation_id, now_ms=now_ms, pending=pending,
                    created_by_session_id=None,
                )
            return _answer(answer)

        async def remind_cancel(reminder_id: str, why: str) -> dict[str, object]:
            """Cancel a reminder you set. Call this when the room no longer
            needs to hear it. Only your own.

            Returns `status`: ok or refused (not found, not yours, or its room is
            not on this agenda).

            Args:
                reminder_id: The id `remind` returned.
                why: The reason you cancel it.
            """
            async with self._lock(unit):
                pending = await self._pending_mirrored_reminds(unit)
                platform, conversation_id = next(
                    ((p.platform, p.conversation_id) for p in pending
                     if p.reminder_ref == reminder_id), ("", ""),
                )
                if platform and f"{platform}:{conversation_id}" not in state.rooms:
                    return _off_agenda(state)
                answer = await dry_run_remind_cancel(
                    ledger=self._store, session_id=state.session_id, unit=unit,
                    reminder_ref=reminder_id, why=why, platform=platform,
                    conversation_id=conversation_id, now_ms=now_ms, pending=pending,
                    created_by_session_id=None,
                )
            return _answer(answer)

        return [
            *read_tools, close, note_create, note_update, event_propose,
            event_revise, event_set_status, standing_revise, wake_at, wake_move,
            wake_cancel, remind, remind_move, remind_cancel,
        ]

    # `created_by_session_id=None` on every move and cancel above, on purpose:
    # the pending seams return only rows COGNITION wrote (a person's own
    # reminder is never among them), so «own» already holds, and passing this
    # session's id would refuse a self-wake set by an EARLIER session, which is
    # the normal case («she keeps, moves or cancels them», 20 §3): a wake set
    # last session could never be cancelled this one.

    async def _pending(self, unit: str, now_ms: int) -> Sequence[PendingSelfWake]:
        """The unit's pending self-wakes, read over a BOUNDED window (a ref
        silent for longer than the horizon plus a day is past due)."""
        return await self._pending_self_wakes(
            unit, now_ms - int(PENDING_WINDOW_SECONDS * 1000),
        )

    async def _created_since(self, unit: str, since_ms: int) -> int:
        if self._self_wakes_created_since is not None:
            return await self._self_wakes_created_since(unit, since_ms)
        return await self._store.count_self_wakes_created(unit=unit, since_ms=since_ms)


def now_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
