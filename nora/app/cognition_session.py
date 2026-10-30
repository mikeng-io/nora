"""The Cognition session loop: the
background writer of the notebook, the events, a room's Standing and Nora's own
reminders.

**What this module wires.** `[cognition] enabled` governs it
(`nora.config.CognitionConfig`): off, the composition root never registers
`CognitionSessionRunner.wake_hook`, so no session runs and no model call is
made. On, one session runs per wake the evaluator (`nora.app.wake.WakeEvaluator`)
records.

**A wake has one fate, and the brakes read it** (DEC-242;
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
The session's wall clock is `budget.timeout_seconds`, enforced here by
cancelling the model loop, and held at least `SESSION_TIMEOUT_MARGIN_SECONDS`
under the wake's own timeout (the constructor refuses otherwise) so a session
records its outcome before its wake can expire; `shutdown` cancels the
sessions still in flight and records them `cancelled`.

**What a session gets**: the situation and agenda
(`nora.domain.cognition_session.build_situation`, pure), the read tools its
rooms permit (built by the CALLER — the composition root, from the existing
`nora.agent.dreamer` tool-source
machinery every other adaptor already uses; this module does not rebuild
disclosure, lineage or withdrawal, it is handed the finished tool list and
REFUSES any tool not on `read_only_tools`), skills injected per agenda kind
present, and its own write, pace and remind verbs (`_build_tools`). Each write
verb runs the validator the tag path runs (`parse_note_body`, the event tier's
modes and citations, `standing_revise`'s checks) and then commits through the
tier's own live store call, stamped `cognition:<unit>`. Every verb is declared
with named, typed parameters and a docstring (ADR 0015): a `**kwargs` closure
reaches the model as one untyped `kwargs` parameter, which is not a tool.

**An agenda with no items is no session**: `on_wake`
returns before building any tool or calling `agent_runner` at all. The ONE
call site in this whole module that can make a model call is the single
`await self._agent_runner.run(...)` inside `_run_session` — everything
before it is pure bookkeeping, so an empty agenda makes zero model calls by
the control flow.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any, Literal, Protocol

from nora.agent import cognition_pace as pace
from nora.agent.dream_screen import offence
from nora.agent.fetch_watch import Fetched, GraphFetched, watch_reads
from nora.agent.turn_recording import record_requirement
from nora.app.proactive_screen import CANCELLED_NOTE_RETIRED
from nora.domain.cognition_ground import Grounding, RecordItem, rollup_status
from nora.domain.cognition_pace import (
    DEFAULT_MAX_SELF_WAKES_PER_DAY,
    PENDING_WINDOW_SECONDS,
    PaceAnswer,
    PendingSelfWake,
)
from nora.domain.cognition_remind import (
    PendingRemind,
    RemindContext,
    check_remind,
    check_remind_time,
)
from nora.domain.cognition_session import (
    Agenda,
    AgendaItem,
    CloseOutcome,
    Room,
    Situation,
    build_situation,
    close_or_strike,
)
from nora.domain.gatherings import (
    EVENT_PARTS,
    AmbiguousOpenGatheringsError,
    FieldMeta,
    Gathering,
    GatheringCancelledError,
    GatheringNotFoundError,
    GatheringStatus,
    InvalidEventFieldModeError,
    StaleEventRevisionError,
    field_mode,
)
from nora.domain.lineage import (
    message as message_lineage,
)
from nora.domain.memo import (
    MEMO_BODY_MAX_CHARS,
    MEMO_TITLE_MAX_CHARS,
    Memo,
    MemoWithdrawnError,
    StaleMemoError,
    citations_of,
    parse_memo_body,
    screened_parts,
    title_carries_value,
)
from nora.domain.note_body import (
    NORA_NAMES,
    NoteBodyError,
    Shown,
    ShownMessage,
    check_citations,
    cited_ids_in,
    parse_note_body,
)
from nora.domain.notes import (
    NoteSupersedeReason,
    StaleNoteHeadError,
    default_revisit_at,
    has_duplicate_open_title,
    latest_anchor,
)
from nora.domain.ports import (
    CognitionStore,
    NewCarry,
    NewCognitionSession,
    NewCognitionSessionClose,
    NewWakeOutcome,
    RecordedOutcome,
    StimulusStore,
)
from nora.domain.reminders import (
    ConflictingProactiveActError,
    DuplicateProactiveNoteError,
    DuplicateProactiveSubjectError,
    DuplicateReminderError,
    Reminder,
    ReminderState,
)
from nora.domain.rollup import room_key as record_room_key
from nora.domain.shared_state import SharedView
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
    restart_detail,
)
from nora.domain.turn_record import DisclosureRequirement, cited_turn_lineage, turn_handles_in
from nora.infra.sql.standing_revise import standing_revise as live_standing_revise
from nora.language.cognition_render import (
    RenderBudget,
    render_agenda,
    room_key,
    room_label,
    speaker_of,
)

log = logging.getLogger("nora.app.cognition_session")

#: `established_by`'s prefix for every write cognition makes (a note revision,
#: an event's `created_by`, a reminder's payload) — code-stamped provenance,
#: never taken from a model argument
#: (the same reason `nora.agent.tools.TAG_PATH_PREFIX` exists for the tag
#: path: "who wrote it" is not something the writer gets to assert).
COGNITION_PATH_PREFIX = "cognition:"

#: The verbs a session holds itself. A read tool may not share one of these
#: names: it would shadow (or be shadowed by) a session verb.
SESSION_VERB_NAMES: frozenset[str] = frozenset({
    "close", "note_create", "note_revise", "note_set_status", "note_merge",
    "memo_create", "memo_revise", "memo_set_status", "event_propose", "event_revise",
    "event_set_status", "standing_revise",
    "wake_at", "wake_move", "wake_cancel", "remind", "remind_move", "remind_cancel",
})

_DAY_MS = 24 * 3600 * 1000
_PACE_VERBS = frozenset({"wake_at", "wake_move", "wake_cancel"})
#: What a verb says when a store or a read it needed failed. A write may have
#: landed part-way, so «nothing was written» would be false, and a retry on that
#: word could write it twice.
_UNFINISHED = (
    "I couldn't finish that just now, so it may or may not have taken effect: don't "
    "assume either, and leave it for the next pass."
)
#: The live note store's compare-and-swap refused (`StaleNoteHeadError`):
#: `unavailable`, the tag path's own answer, never `refused`.
_STALE_NOTE: dict[str, object] = {
    "status": "unavailable", "reason": "stale_head",
    "detail": "This note changed under you, so read it again and redo your change "
              "against what it says now. Nothing has been written.",
}
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
    restated here so this module does not import `nora.agent.dreamer`."""

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float
    #: `nora.agent.dreamer.AdaptorBudget`'s two per-pass ceilings on tool
    #: reads: the memory cluster and every verb together. `None` is no ceiling.
    max_memory_calls: int | None = None
    max_tool_calls: int | None = None


@dataclass(frozen=True)
class CognitionRunResult:
    """What one model call returned. `stop_reason` is Strands' own (R-23);
    `None` on a run this module never made (belt-and-braces callers only)."""

    stop_reason: str | None = None
    #: Tokens the whole loop spent (every cycle, the model's own reading of its
    #: usage); `None` when the runner could not read it, never a zero.
    total_tokens: int | None = None
    messages: tuple[dict[str, Any], ...] = ()
    requirements: dict[str, Any] = field(default_factory=dict)


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
# What the runner reads from the world, each a narrow seam returning plain data.
# Its writes go to the tiers' own stores, handed in whole (`notes`, `events`,
# `standing`, `reminders`).
# ---------------------------------------------------------------------------

OpenNoteTitles = Callable[[str], Awaitable[Sequence[str]]]
OpenEvents = Callable[[str, str, str], Awaitable[Sequence[Gathering]]]
#: `(platform, conversation_id)` -> the room's Standing head WITH the id of the
#: `standing_revision` row that is it (`SqlStandingStore.read_head`): the token a
#: `standing_revise` call asserts as its base (DEC-250).
PriorStanding = Callable[[str, str], Awaitable[StandingHead | None]]
#: `(platform, conversation_id)` -> the room's roster, roster name -> person id,
#: or None when it cannot be read (an ERROR, never an empty room): what a note's
#: `## Owed` owner is checked against.
RosterOf = Callable[[str, str], Awaitable[Mapping[str, str] | None]]
#: `(platform, conversation_id)` -> the room's `proactive` flag
#: (`Policy.proactive_allowed`): may Nora reach it unasked at all.
RoomProactive = Callable[[str, str], bool]
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
#: these agenda kinds, recorded on the session row.
SkillVersionsFor = Callable[[Sequence[str]], Mapping[str, object]]
#: `(text, versions)` for these agenda kinds from ONE resolution of each skill:
#: the text injected and the digests recorded, so a hot reload between two
#: separate reads cannot leave a row naming a digest of text the model never read.
SkillsResolvedFor = Callable[[Sequence[str]], tuple[str, Mapping[str, object]]]
#: `(text, sha256)` of the session's instruction, read PER SESSION: the Dreamer
#: objective hot-reloads (DEC-128), and a text captured at construction would
#: keep serving an objective the operator has already edited.
DoctrineSource = Callable[[], tuple[str, str | None]]


@dataclass
class TokenSpend:
    """The tokens one session's loop spent, as far as the runner could read them,
    left by the agent runner even when the loop FAILED, timed out or was cut off
    (a session that raised is the costliest kind, and its usage is in the agent,
    not in a result that never came). `None`: unread, never a zero."""

    total: int | None = None
    messages: tuple[dict[str, Any], ...] = ()
    requirements: dict[str, Any] = field(default_factory=dict)


#: The session's `TokenSpend`, set around the model loop. A context variable and
#: not a parameter of `CognitionAgentRunner.run`, so every runner double that
#: predates it still satisfies the Protocol; a task started by `asyncio.wait_for`
#: copies the context, and the holder is shared by reference.
SESSION_TOKENS: contextvars.ContextVar[TokenSpend | None] = contextvars.ContextVar(
    "cognition_session_tokens", default=None,
)


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
    #: a note body may cite, each checked for its author.
    records: tuple[RecordItem, ...] = ()
    #: What the session's read tools returned that a note may cite.
    fetched: Fetched = field(default_factory=Fetched)
    #: `(room key, agenda item or None)` of every write this session landed:
    #: what `close(reason="written")` is backed by, per item.
    #: A write that named no item is `(room, None)`.
    written: set[tuple[str, int | None]] = field(default_factory=set)
    closed_indices: set[int] = field(default_factory=set)
    closes: list[CloseOutcome] = field(default_factory=list)
    admitted_indices: set[int] = field(default_factory=set)
    memory_items: dict[int, Any] = field(default_factory=dict)
    graph_fetched: Any = field(default_factory=GraphFetched)
    admitted_relations: dict[str, Any] = field(default_factory=dict)
    admitted_evidence: list[Any] = field(default_factory=list)
    graph_text: str = ""
    historical: bool = False
    #: A verb other than `close` was called in this session, whatever it
    #: answered: the loop then never ends at the last close (`_all_quiet`).
    acted: bool = False
    #: Strands' `request_state` as `close` last saw it: where a quiet close set
    #: `stop_event_loop`, so a verb that runs in the SAME model turn (Strands
    #: reads the flag only after every tool of the turn has run) can take it
    #: back.
    request_state: dict[str, Any] | None = None
    #: The line of the prompt that says which room(s) the read tools resolve
    #: through (`_build_tools`).
    reads_note: str = ""
    unit_rooms: dict[str, tuple[str, str]] | None = None

    @property
    def allowed_rooms(self) -> dict[str, tuple[str, str]]:
        return self.rooms if self.unit_rooms is None else self.unit_rooms


_room_key = room_key

#: The close reasons that leave nothing for a follow-up model call to do: when
#: every agenda item closed with one of these and no other verb was called, the
#: loop ends at the last close (DEC-279).
_QUIET_CLOSES: frozenset[str] = frozenset({"no_change", "covered"})


def _all_quiet(state: _RunState) -> bool:
    """Every agenda item is closed, each `no_change` or `covered`, and no verb
    but `close` was called: the model has nothing left to write, and the call
    Strands would make after the last close only returns."""
    return (
        not state.acted
        and len(state.closed_indices) == len(state.items_by_index)
        and all(c.reason in _QUIET_CLOSES for c in state.closes)
    )


def _room_of(room: str, state: _RunState) -> tuple[str, str] | dict[str, object]:
    """Explicit destinations come from the canonical unit; the agenda chooses
    the default destination and owns item attribution, not write scope."""
    if not state.allowed_rooms:
        return {"status": "unavailable", "reason": "unit_scope_unavailable",
                "detail": "the unit's room set could not be established"}
    if room:
        found = state.allowed_rooms.get(room)
        if found is None:
            return {"status": "refused", "reason": "unknown_room",
                    "detail": f"{room!r} is not a room in this unit"}
        return found
    if len(state.rooms) == 1:
        key = next(iter(state.rooms))
        return _room_of(key, state)
    return {
        "status": "refused", "reason": "room_required",
        "detail": f"this agenda spans {len(state.rooms)} rooms; name the one this "
                  f"write is about as `room`, one of {sorted(state.allowed_rooms)}",
    }


def _write_target(
    room: str, item: int | None, state: _RunState,
) -> tuple[tuple[str, str], int | None] | dict[str, object]:
    """The room a write is about and the agenda item it is for. `item`
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
    resolved = _room_of(key, state)
    return resolved if isinstance(resolved, dict) else (resolved, item)


def _outside_unit(state: _RunState) -> dict[str, object]:
    """A reminder outside the unit is not this session's to move or cancel."""
    return {
        "status": "refused", "reason": "unknown_room",
        "detail": "that reminder's room is not in this unit",
    }


def _written_for(index: int, item: AgendaItem, state: _RunState) -> bool:
    """Whether a write that would commit backs `index`'s `written` close: one
    tagged with this item, or an untagged write in its room when it holds the
    only item there."""
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
    unparseable): every time a session verb takes is one time base."""
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
    note tier holds."""
    if not _ISO_DAY.match(text):
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    return day.year * 10_000 + day.month * 100 + day.day


def _is_abduced(event: Gathering) -> bool:
    """Whether any field the event STATES is only abduced. The field's own mode
    wins (`nora.domain.gatherings.field_mode`, the one reading every renderer
    uses), the row's reasoning being only its fallback: an event whose row says
    `abduced` but whose stated fields are all `told` is not a guess to ask about."""
    stated = {
        "what": event.title, "start": event.when, "end": event.end,
        "place": event.venue_text, "people": event.people, "status": event.status,
    }
    return any(
        field_mode(event, part) == "abduced"
        for part in EVENT_PARTS if stated.get(part)
    )


def _bare_row(row: str) -> str:
    """`event:<id>` / `note:<id>` / `<id>` -> `<id>`, the spelling a row is
    compared by."""
    kind, sep, rest = row.partition(":")
    return rest if sep and kind in ("note", "event") else row


def _refused(reason: str, detail: str | None) -> dict[str, object]:
    return {"status": "refused", "reason": reason, "detail": detail}


def _answer(answer: PaceAnswer, **ok: object) -> dict[str, object]:
    """A recorded pace call, as the tool's answer: the refusal WITH its reason
    when it was refused, so the model can act on it."""
    if answer.refusal_reason is not None:
        return _refused(answer.refusal_reason, answer.refusal_detail)
    return {"status": "ok", **ok}


class CognitionSessionRunner:
    def __init__(
        self, *, store: CognitionStore, stimulus_store: StimulusStore,
        agent_runner: CognitionAgentRunner, home_rooms: Mapping[str, Room],
        read_tools_for: ReadToolsFor, skill_text_for: SkillsFor, grounder: Grounder,
        doctrine_text: str, doctrine_sha256: str | None,
        open_note_titles: OpenNoteTitles, open_events: OpenEvents,
        prior_standing: PriorStanding,
        budget: AdaptorBudget, cooldown_seconds: float,
        units: Collection[str], session_timeout_seconds: float,
        max_items: int, max_strikes: int, max_self_wakes: int = 5,
        max_self_wakes_per_day: int = DEFAULT_MAX_SELF_WAKES_PER_DAY,
        read_only_tools: frozenset[str] = frozenset(),
        model_id: str | None = None, model_route: str | None = None,
        metrics: Any = None, mint_ref: Callable[[], str] = default_mint_ref,
        clock: Callable[[], float] = time.time,
        doctrine_source: DoctrineSource | None = None,
        skill_versions_for: SkillVersionsFor | None = None,
        skills_for: SkillsResolvedFor | None = None,
        tz: tzinfo = UTC, render_budget: RenderBudget | None = None,
        model_window_tokens: int | None = None, overflow_window_fraction: float = 0.8,
        person_known: Callable[[str], bool] | None = None,
        roster_of: RosterOf | None = None,
        nora_names: frozenset[str] = NORA_NAMES,
        room_proactive: RoomProactive | None = None,
        max_pending_per_requester: int | None = None,
        remind_min_lead_seconds: int | None = None,
        remind_max_horizon_days: int | None = None,
        notes: Any = None, events: Any = None, standing: Any = None,
        reminders: Any = None, ai_peer: str = "nora", memos: Any = None,
        ledger: Any = None, shared: Any = None,
        content_checks: bool = True,
        tag_trace_store: Any = None, memory: Any = None,
        rooms_of_unit: Callable[[str], Sequence[tuple[str, str]]] | None = None,
        resolve_row: Callable[[str, int], Awaitable[tuple[str, str] | None]] | None = None,
    ) -> None:
        """`doctrine_source`, when given, is read at the start of EVERY session
        and replaces `doctrine_text`/`doctrine_sha256` (which then serve only
        as the constructor's fixed fallback for a caller with a fixed text):
        the production composition passes the Dreamer's hot-reloading
        objective through it. `skill_versions_for` records what
        `skill_text_for` injected. `skills_for`, when given, replaces both: the
        text and the versions come from one resolution, taken once per session
        BEFORE the awaited grounding read and carried through (the production
        composition passes it: a skill hot-reloads, and two reads of it could
        straddle an edit).

        `read_only_tools` is the allowlist `read_tools_for`'s result is
        held to (the composition passes the registry's own read-only set,
        `ToolRegistry.read_only_tools()`); empty, the default, refuses every
        read tool: a session with no declared read surface has none.

        `notes` (`SqlNoteStore`), `events` (`GatheringService`), `standing`
        (`SqlStandingStore`) and `reminders` (`ReminderStore`) are the live
        stores the tag path writes through; the session's writes land there.
        `memos` (`SqlMemoStore`, DEC-306) is the memo tier's, which only a
        session writes; None answers every memo verb `unavailable`.
        `ai_peer` is Nora's own requester id, the owner of every reminder she
        sets herself.

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

        `room_proactive`, `max_pending_per_requester`, `remind_min_lead_seconds`
        and `remind_max_horizon_days` are what `remind` is held to: the room's
        flag, and the tag `remind` tool's own `ReminderCaps`. None leaves that
        check out, which only a test wants.

        `model_window_tokens` (None: unknown) and `overflow_window_fraction`
        are what a context overflow is PROVEN against (DEC-244): without a
        known window no overflow is ever the agenda's, and every one is
        `infra`."""
        self._memory = memory
        self._content_checks = content_checks
        self._tag_trace_store = tag_trace_store
        check_home_rooms(units, home_rooms)
        check_session_timeouts(budget.timeout_seconds, session_timeout_seconds)
        self._store = store
        self._notes = notes
        self._memos = memos
        self._events = events
        self._standing = standing
        self._reminders = reminders
        self._ledger = ledger
        self._shared = shared
        self._rooms_of_unit = rooms_of_unit
        self._resolve_row = resolve_row
        self._ai_peer = ai_peer
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
        self._skills_for = skills_for
        self._open_note_titles = open_note_titles
        self._open_events = open_events
        self._prior_standing = prior_standing
        self._roster_of = roster_of
        self._nora_names = frozenset(nora_names)
        self._room_proactive = room_proactive
        self._max_pending_per_requester = max_pending_per_requester
        self._remind_min_lead_seconds = remind_min_lead_seconds
        self._remind_max_horizon_days = remind_max_horizon_days
        self._budget = budget
        self._cooldown_seconds = cooldown_seconds
        self._max_items = max_items
        self._max_strikes = max_strikes
        self._max_self_wakes = max_self_wakes
        self._max_self_wakes_per_day = max_self_wakes_per_day
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

    def _inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        """Count, and NEVER raise: it runs after writes have landed (a self-wake
        stands, a ledger row is recorded), and a counter that fails (`Metrics.inc`
        raises on an undeclared name) must not turn a landed write into an error the
        model would answer by writing it again."""
        if self._metrics is None:
            return
        try:
            self._metrics.inc(name, value, **labels)
        except Exception:
            log.exception("could not count %s", name)

    def _counted(self, unit: str, verb: str, call: Any) -> Any:
        """A verb whose every refusal is counted by rule
        (`nora_cognition_refused_total{verb,rule}`): one place, so no verb and
        no early return can refuse without being counted. `unavailable` (a read
        the verb needed failed) counts too, as its own rule, never as a refusal
        of the model's work. The labels are the verb's own name and the reason
        code, both code constants; no room, person or text is a label.

        **A verb never raises to the model.** A store or a seam that fails inside
        any verb is logged (with its traceback, which can name a connection) and
        answered `unavailable` in words that say nothing of the exception: Strands
        would otherwise hand the model `status: error` with the raw text. The one
        place that holds for every verb, as the counting is."""
        @functools.wraps(call)
        async def counted(*args: Any, **kwargs: Any) -> dict[str, object]:
            try:
                result: dict[str, object] = await call(*args, **kwargs)
            except Exception:
                log.exception("cognition verb %s failed", verb)
                self._inc("nora_errors_total", stage="cognition_session")
                result = {"status": "unavailable", "reason": "error", "detail": _UNFINISHED}
            status = result.get("status")
            if status in ("refused", "unavailable", "unresolved"):
                reason = str(result.get("reason") or "unspecified")
                self._inc(
                    "nora_cognition_refused_total", verb=verb,
                    rule=reason if status == "refused" else f"{status}_{reason}",
                )
                if status == "refused" and verb in _PACE_VERBS:
                    self._inc("nora_self_wake_total", outcome="refused")
            return result
        return counted

    def _lock(self, unit: str) -> asyncio.Lock:
        return self._locks.setdefault(unit, asyncio.Lock())

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def on_wake(
        self, *, unit: str, view: UnitView, now_ms: int, wake_id: int,
        replay: bool = False, historical: bool = False, inspect_only: bool = False,
    ) -> CognitionSessionOutcome | dict[str, str] | None:
        """Called by the wake evaluator's hook on every wake it
        records (`WakeHook`). `inspect_only` returns the assembled model input
        before the model call; use it only in a disposable replay sandbox.
        Returns `None` on every ordinary path that makes no model
        call; RAISES, after recording the wake's outcome, on every path that
        did not close. Every path writes the wake's outcome (or, if that write
        fails, leaves the wake OPEN until it expires): nothing here returns
        with the wake's stimuli consumed and unseen."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks[wake_id] = task
        session_id: int | None = None
        #: Offered to the model loop (`SESSION_TOKENS`) and read by EVERY path
        #: that records this wake's outcome, so a failed, timed-out or cancelled
        #: session's spend is as durable as a closed one's.
        spend = TokenSpend()
        try:
            home_room = self._home_rooms.get(unit)
            if home_room is None:
                self._inc("nora_cognition_session_skipped_total", reason="no_home_room")
                raise MissingHomeRoomError(f"no home room configured for unit {unit!r}")
            # WHEN SHE LAST LOOKED is when the unit's last session ENDED, not the
            # newest message (`view.last_activity_ms` is how active the room
            # is): a clock line that says «you last looked» from a person's
            # message time tells her the wrong span (DEC-245). None: no
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
            #               unseen (no Rollup stimulus wakes a unit, DEC-279;
            #               it rides the next wake), and it is an agenda
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
            if self._memory is not None and not replay:
                retained.update(await self._memory.parked(unit, view.pending))
                # Malformed input is still a stimulus: keep it pending until understood.
                retained.update(malformed)
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
            # The skill is resolved ONCE, here, and its text carried to the prompt:
            # the grounding read below awaits, and a hot reload in that window
            # must not leave `skill_versions` naming a different text than the
            # one the model reads.
            skill_text, skill_versions = self._skills(kinds)
            # GROUNDED AFTER THE SKIP, BEFORE ANYTHING IS RECORDED: the agenda is
            # final, so the grounding's per-item
            # index (her own sends) is the index the render reads, and an item
            # promoted from `deferred` by a skip is grounded. A read that
            # fails raises here, so the wake is recorded `failed` and its
            # stimuli stay pending: a session never runs on bare counts it
            # cannot judge from.
            grounding = await self._grounder(unit, situation.agenda.items, pending)
            if inspect_only:
                # BEFORE ANYTHING IS RECORDED: an inspection returns what a
                # session would be run on and persists nothing — no session
                # row, no outcome write, no stimulus consumption. The live
                # path below records first and the replay path shares it.
                _, _, system, agenda = await self._session_input(
                    unit=unit, view=view, situation=situation, session_id=0,
                    wake_id=0, now_ms=now_ms, doctrine_text=doctrine_text,
                    skill_text=skill_text, grounding=grounding,
                    historical=historical,
                )
                return {"system": system, "agenda": agenda}
            session = NewCognitionSession(
                unit=unit, mode="live", started_at_ms=now_ms,
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
            self._inc("nora_cognition_session_total", mode="live")
            return await self._run_session(
                unit=unit, view=view, situation=situation, session_id=session_id,
                wake_id=wake_id, now_ms=now_ms, doctrine_text=doctrine_text,
                skill_text=skill_text, grounding=grounding, retained=sorted(retained),
                spend=spend, replay=replay, historical=historical,
            )
        except asyncio.CancelledError:
            # Only a process shutdown cancels a session (`shutdown`,
            # `WakeEvaluator.cancel_in_flight`; the wall clock raises
            # `SessionTimeoutError` instead), so this is a RESTART: recorded as
            # one, and `cancelled` neither backs the unit off nor strikes.
            await self._shielded_finish(
                wake_id, unit, session_id, "cancelled",
                restart_detail("the process was stopping and cancelled the session"),
            )
            raise
        except SessionExpiredError:
            raise
        except SessionTimeoutError as exc:
            if self._memory is not None and not replay:
                await self._memory.failed(unit, [s.id for s in view.pending], session_id)
            await self._finish(wake_id, unit, session_id, "timeout", str(exc))
            raise
        except Exception as exc:
            if self._memory is not None and not replay:
                await self._memory.failed(unit, [s.id for s in view.pending], session_id)
            log.exception("cognition session failed (unit=%s wake=%s)", unit, wake_id)
            await self._finish(
                wake_id, unit, session_id, "failed", f"{type(exc).__name__}: {exc}"[:500],
                cause=classify_failure(exc),
            )
            raise
        finally:
            self._tasks.pop(wake_id, None)

    async def replay(self, *, view: UnitView, now_ms: int, historical: bool = False) -> Any:
        """Run the recorded batch through live assembly, using sandbox collaborators."""
        return await self.on_wake(unit=view.unit, view=view, now_ms=now_ms,
                                  wake_id=0, replay=True, historical=historical)

    async def inspect(self, *, view: UnitView, now_ms: int) -> dict[str, str] | None:
        """The model input a session over `view` would be run on, through the same
        assembly, stopping before the model call. For a disposable replay sandbox."""
        result = await self.on_wake(unit=view.unit, view=view, now_ms=now_ms,
                                    wake_id=0, replay=True, inspect_only=True)
        return result if isinstance(result, dict) else None

    async def probe(
        self, *, unit: str, summary: str, now_ms: int, room: Room | None = None,
    ) -> CognitionRunResult:
        """One session over ONE hand-written agenda item: the seam the
        `tool_selection_cognition` corpus drives (`evals.rebuilt.entry`), whose
        cases are agenda-item sentences and not stimuli. It builds the same
        tools, holds the same instruction and calls the same agent runner as a
        wake's session, and it stops there: no wake, so no outcome, no carry
        and no fired self-wake; the session row and its writes go to the
        stores it was handed (a snapshot's or a sandbox's database)."""
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
        skill_text, skill_versions = self._skills([item.kind])
        session_id = await self._store.record_session(
            session=NewCognitionSession(
                unit=unit, mode="live", started_at_ms=now_ms, from_stimulus_id=0,
                through_stimulus_id=0, situation=situation.as_dict(),
                agenda={"items": [item.as_dict()], "deferred_stimulus_ids": []},
                # What the system prompt below injects, as a wake's session
                # records it: an eval arm's rows name the skill it ran with.
                doctrine_sha256=doctrine_sha256, skill_versions=skill_versions,
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
            system_prompt=doctrine_text + skill_text,
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

    def _skills(self, kinds: Sequence[str]) -> tuple[str, dict[str, object]]:
        """The skill text a session injects and the versions its row records."""
        if self._skills_for is not None:
            text, versions = self._skills_for(kinds)
            return text, dict(versions)
        return self._skill_text_for(kinds), (
            {} if self._skill_versions_for is None else dict(self._skill_versions_for(kinds))
        )

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
        write = asyncio.ensure_future(
            self._finish(wake_id, unit, session_id, outcome, detail),
        )
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
        if wake_id == 0:  # A replay has a session record but no live wake claim.
            return None
        ended_ms = self._now_ms()
        try:
            recorded = await self._stimuli.record_outcome(
                outcome=NewWakeOutcome(
                    wake_id=wake_id, unit=unit, outcome=outcome, at_ms=ended_ms,
                    detail=detail, session_id=session_id,
                    # A session that closed or was cancelled did not FAIL: it has
                    # no cause, never `infra`.
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

    async def _session_input(
        self, *, unit: str, view: UnitView, situation: Situation, session_id: int,
        wake_id: int, now_ms: int, doctrine_text: str, skill_text: str,
        grounding: Grounding, historical: bool = False,
    ) -> tuple[_RunState, list[Any], str, str]:
        """Everything a session assembles before the model call: the run
        state, the tools, the system prompt and the agenda text. A live
        session and an inspection share exactly this and diverge after it:
        one runs the model and records outcomes, the other returns what it
        built and persists nothing."""
        items = situation.agenda.items
        state = _RunState(
            session_id=session_id, wake_id=wake_id, unit=unit, now_ms=now_ms,
            items_by_index=dict(enumerate(items)), historical=historical,
            rooms={_room_key(i.room): (i.room.platform, i.room.conversation_id) for i in items},
            # Her own sends in an item's window are SHOWN too (DEC-245), marked
            # as hers, so a note citing one is refused `cites_nora`, not `unknown_id`.
            records=(
                *grounding.records.values(),
                *(r for sends in grounding.own_sends.values() for r in sends),
            ),
        )
        if self._memory is not None:
            await self._memory.prepare(state, {s.id: s for s in view.pending}, grounding)
        tools = await self._build_tools(
            unit=unit, state=state, now_ms=now_ms, labels=grounding.room_labels,
        )
        from nora.language.comprehend_render import SESSION_INSTRUCTION
        system_prompt = doctrine_text + skill_text
        if self._memory is not None:
            system_prompt += SESSION_INSTRUCTION
        shared_view = None
        if self._shared is not None:
            try:
                shared_view = await self._shared(
                    unit=unit, now_ms=now_ms,
                    wakes=await self._pending(unit, now_ms),
                )
            except Exception:
                # A shared read that fails must not take the session down —
                # but it MUST not read as an empty unit either. The view's own
                # `unavailable` status is what the block renders; swallowing
                # to None would silently drop the coverage line.
                log.exception("shared-state read failed for %s", unit)
                self._inc("nora_errors_total", stage="shared_state")
                shared_view = SharedView(
                    unit=unit, status="unavailable", as_of_ms=now_ms,
                )
        prompt = render_agenda(
            items, stimuli={s.id: s for s in view.pending}, grounding=grounding,
            reads_note=state.reads_note, now_ms=now_ms,
            last_session_ms=situation.last_session_ms, tz=self._tz,
            budget=self._render_budget, graph=state.graph_text,
            shared=shared_view,
        )
        return state, tools, system_prompt, prompt

    async def _run_session(
        self, *, unit: str, view: UnitView, situation: Situation, session_id: int,
        wake_id: int, now_ms: int, doctrine_text: str, skill_text: str,
        grounding: Grounding, spend: TokenSpend, retained: Sequence[int] = (),
        replay: bool = False, historical: bool = False,
    ) -> CognitionSessionOutcome:
        items = situation.agenda.items
        kinds = sorted({item.kind for item in items})
        state, tools, system_prompt, prompt = await self._session_input(
            unit=unit, view=view, situation=situation, session_id=session_id,
            wake_id=wake_id, now_ms=now_ms, doctrine_text=doctrine_text,
            skill_text=skill_text, grounding=grounding, historical=historical,
        )
        token = SESSION_TOKENS.set(spend)
        result: CognitionRunResult | None = None
        trace_outcome = "llm_error"
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
            trace_outcome = "llm_error" if result.stop_reason in LIMIT_STOP_REASONS else "silent"
        except TimeoutError as exc:
            raise SessionTimeoutError(
                f"the session ran past its {self._budget.timeout_seconds:g}s wall clock",
            ) from exc
        finally:
            SESSION_TOKENS.reset(token)
            if self._tag_trace_store is not None:
                from nora.domain.tag_trace import TagTrace
                await asyncio.wait_for(self._tag_trace_store.save(TagTrace(
                    tag_id=f"cognition:{session_id}", conversation_id="",
                    messages=result.messages if result is not None else spend.messages,
                    requirements=result.requirements if result is not None else spend.requirements,
                    transcript=(), outcome=("timeout" if isinstance(
                        sys.exception(), SessionTimeoutError) else trace_outcome),
                    created_at=int(self._clock() * 1000), started_at=now_ms,
                    occasion="cognition", unit=unit,
                    bound_rooms=tuple(sorted(state.rooms)),
                )), timeout=5.0)
            # What the loop spent counts whether it returned, failed, timed out or was
            # cut off: a session that raised is the costliest kind, and its usage is
            # in the agent (`spend`, left by the runner), not in a result that never
            # came. The result's own count wins when there is one.
            total = result.total_tokens if result is not None else None
            if total is None:
                total = spend.total
            if total is not None:
                # By what the unit's agenda was made of, never by unit, room or
                # person: a single kind, or `mixed`.
                self._inc(
                    "nora_cognition_unit_tokens_total", float(total),
                    unit_kind=kinds[0] if len(kinds) == 1 else "mixed",
                )

        if result.stop_reason in LIMIT_STOP_REASONS:
            # The loop RETURNED, but it was cut off at a bound (DEC-247), so it
            # never decided what to do with the items it had not reached: a
            # FAILED session, cause `infra`. No close, no strike, no carry (a
            # `_settle` here would strike a human's message for the model's
            # runaway), its stimuli stay pending and the failure backoff
            # applies. Writes it already made stay as recorded.
            self._inc("nora_cognition_session_limit_total", limit=result.stop_reason)
            raise SessionLimitError(result.stop_reason or "")

        if replay:
            return CognitionSessionOutcome(session_id=session_id, items=len(items),
                closed=tuple(state.closes), stop_reason=result.stop_reason)
        if self._memory is not None:
            await self._memory.failed(unit, [sid for i, item in state.items_by_index.items()
                if i not in state.admitted_indices for sid in item.stimulus_ids], session_id)
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
        raises `SessionLimitError` before this, DEC-247) leaves behind:
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
            await self._record_close(state, outcome, by_model=False)

        carries: list[NewCarry] = []

        def carry(sid: int, strike: int, carry_state: str, *, waits: bool = False) -> None:
            carries.append(NewCarry(
                unit=state.unit, stimulus_id=sid, strike=strike, state=carry_state,
                session_id=state.session_id, waits=waits,
            ))

        by_index = {c.item_index: c for c in state.closes}
        for index, item in enumerate(items):
            close = by_index[index]
            if self._memory is not None and index not in state.admitted_indices:
                for sid in item.stimulus_ids:
                    carry(sid, view.carry.get(sid, 0), "carried")
                continue
            for sid in item.stimulus_ids:
                if close.reason == "waiting":
                    carry(sid, close.strike, "carried", waits=True)
                elif close.reason == "struck":
                    carry(sid, close.strike, "struck")
                elif sid in view.carry:
                    carry(sid, view.carry[sid], "released")

        for sid in situation.agenda.deferred_stimulus_ids:
            strike = view.carry.get(sid, 0) + 1
            # Deferral counts remain diagnostic; uncomprehended items never strike out.
            carry(sid, strike, "carried" if self._memory is not None else
                  ("struck" if strike >= self._max_strikes else "carried"))
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
                        await pace.wake_fire(
                            ledger=self._store, session_id=state.session_id,
                            unit=state.unit, wake_ref=row.item, now_ms=self._now_ms(),
                        )
                        self._inc("nora_self_wake_total", outcome="fired")
                    except Exception:
                        log.exception("could not record self-wake %s fired", row.item)
                        self._inc("nora_errors_total", stage="cognition_session")

    async def _record_close(
        self, state: _RunState, outcome: CloseOutcome, *, by_model: bool = True,
    ) -> None:
        """Record one close. `by_model` False is CODE's close of an item the
        model never closed (`_settle`): the row is the same `waiting`, but the
        metric says `unclosed`, so a `waiting` the model chose and one it never
        reached are not one number."""
        await self._store.record_close(
            close=NewCognitionSessionClose(
                session_id=state.session_id, item_index=outcome.item_index,
                kind=outcome.kind, reason=outcome.reason, strike=outcome.strike,
            ),
            recorded_at_ms=state.now_ms,
        )
        state.closed_indices.add(outcome.item_index)
        state.closes.append(outcome)
        self._inc(
            "nora_cognition_item_total", kind=outcome.kind,
            outcome=outcome.reason if by_model or outcome.reason == "struck" else "unclosed",
        )

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
                    "session holds read tools and its own verbs only",
                )
            if name in SESSION_VERB_NAMES:
                raise NotReadOnlyToolError(f"read tool {name!r} shadows a session verb")
        return tools

    async def _build_tools(
        self, *, unit: str, state: _RunState, now_ms: int,
        labels: Mapping[str, str] | None = None,
    ) -> list[Any]:
        if self._rooms_of_unit is not None:
            state.unit_rooms = {
                record_room_key(p, c): (p, c) for p, c in self._rooms_of_unit(unit)
            }
        # EACH ITEM'S READS RESOLVE THROUGH ITS OWN ROOM (DEC-231, R-21). One
        # room on the agenda: that room's tools. Several: `read_tools_for`
        # returns ONE tool per name (a tool name is one tool: Strands keeps the
        # LAST of two silently, which read one
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
        # The prompt calls a room by its LABEL; the id is what a tool's
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
        # Public reads use the same bounded, evidence-retaining egress wrapper.
        if self._memory is not None:
            from nora.agent.comprehend_tools import ComprehendRead, LookupState
            lookups = LookupState(state.graph_fetched, unit, state.session_id,
                self._memory.records, self._metrics, self._memory.private_names,
                persons=tuple({p for v in state.memory_items.values()
                               for source in v.sources for p in source.persons}))
        # The reads that hand out citable rows are watched: what they return is
        # what a note may cite besides the agenda's own rows.
        read_tools = watch_reads(self._guarded_read_tools(unit, rooms), state.fetched, keys)

        if self._memory is not None:
            read_tools = [ComprehendRead(t, lookups) if hasattr(t, "stream") else t
                          for t in read_tools]

        async def shown_for(
            platform: str, conversation_id: str, body: str,
        ) -> Shown | dict[str, object]:
            """What a write in this room may cite and name, built from what the
            session was actually shown and fetched, so the tag path's own
            validator runs on it: the rows of THIS room the agenda
            raised (id, author, person, whether they are Nora's), the rows
            `record_query` returned, the ids an existing note cites, the Rollup
            rows read, the room's roster and Nora's names. An unreadable
            roster is `unavailable`, never an empty room that refuses every
            owner."""
            here = Room(platform, conversation_id)
            fetched = state.fetched.of(_room_key(here))
            rendered: dict[int, ShownMessage] = {
                # `sender` IS the `from` the prompt rendered for this row: the
                # note validator compares a `(<names>)` slot to it, so it is
                # `speaker_of`, the one function the renderer uses too.
                r.message_id: ShownMessage(
                    id=r.message_id, sender=speaker_of(r.author), person_id=r.person_id,
                    is_nora=r.own or r.author in self._nora_names,
                    turn_handle=r.turn_handle,
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
                turn_handles=frozenset(fetched.turn_handles),
            )

        def wrote(
            platform: str, conversation_id: str, item: int | None, **ok: object,
        ) -> dict[str, object]:
            """A write that landed: it backs a `written` close of the item it
            names."""
            state.written.add((_room_key(Room(platform, conversation_id)), item))
            return {"status": "ok", **ok}

        established_by = f"{COGNITION_PATH_PREFIX}{unit}"

        async def close(
            item_index: int, reason: Literal["written", "no_change", "waiting", "covered"],
            tool_context: Any = None,
        ) -> dict[str, object]:
            """Close one agenda item. Call this when you have dealt with an item
            or decided it needs nothing now. An item you leave open, or close
            `waiting`, comes back on the next agenda with a strike, and code
            closes it for good after a few.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok (`written` with no write of yours for that
            item is refused).

            Args:
                item_index: The item's number in the agenda.
                reason: `written` when a write you made for it covers it (one
                    that was not refused, and named this item when its room
                    holds several), `no_change` when nothing about it
                    needs to change, `waiting` when it cannot be settled yet,
                    `covered` when another item already handles it.
            """
            if self._memory is not None and item_index not in state.admitted_indices:
                return _refused("proposal_required", "admit understanding of this item first")
            if item_index in state.closed_indices:
                return _refused("already_closed", "that item is already closed")
            item = state.items_by_index.get(item_index)
            if item is None:
                return _refused("no_such_item", f"{item_index} is not an item of this agenda")
            if reason == "written" and not _written_for(item_index, item, state):
                # A close is measured against what was written, never against the
                # model's say-so.
                return _refused(
                    "no_write_for_item",
                    "no write of yours for this item was accepted in this session (name "
                    "it as the write's `item`); write it first, or close it `no_change`, "
                    "`waiting` or `covered`",
                )
            try:
                outcome = close_or_strike(
                    item, item_index=item_index, reason=reason,
                    max_strikes=self._max_strikes,
                )
            except ValueError as exc:
                return _refused("bad_reason", str(exc))
            await self._record_close(state, outcome)
            if tool_context is not None:
                request = tool_context.invocation_state.setdefault("request_state", {})
                state.request_state = request
                if _all_quiet(state):
                    # Strands' own way for a tool to end the loop: no further
                    # model call after this tool batch (`strands.event_loop`,
                    # 1.50.2). Any other verb of the same batch takes it back.
                    request["stop_event_loop"] = True
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
            is refused.

            Returns `status`: ok (with the `note_id`), refused or unavailable,
            with a `reason` and `detail` when it is not ok.

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
                    `platform:conversation_id`, which the reads block
                    shows beside each room's label.
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
                    return _refused(
                        "bad_anchor_date", "anchor_date must be an ISO date, year-month-day",
                    )
            shown = await shown_for(platform, conversation_id, body)
            if isinstance(shown, dict):
                return shown
            try:
                parsed = parse_note_body(body, shown=shown, require_open=True)
            except NoteBodyError as exc:
                return _refused(exc.rule, str(exc))
            # Under the unit's lock: Strands runs one turn's tool calls
            # concurrently, and two creates of one title would both pass the check.
            async with self._lock(unit):
                if has_duplicate_open_title(title, await self._open_note_titles(unit)):
                    return _refused(
                        "duplicate_title",
                        "a note with that title is already open; update it instead",
                    )
                try:
                    note = await self._notes.create(
                        unit=unit, title=title, description=description,
                        uncertainty=uncertainty, closing_condition=closing_condition,
                        established_in=conversation_id, body=parsed.text,
                        established_by=established_by, now_ms=self._now_ms(),
                        anchor_date=anchor_day,
                        anchor_entity_ids=tuple(anchor_entities or ()),
                    )
                except ValueError as exc:
                    return _refused("invalid_note", str(exc))
            return wrote(platform, conversation_id, item, note_id=note.id)

        async def note_revise(
            note_id: str, body: str, parent_revision_id: str,
            room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Revise a note. Call this when a note's content changes: a detail
            settles, a plan moves. Give back the WHOLE note with your change
            folded in: `body` replaces the current contents, and the old
            revision is kept.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

            Args:
                note_id: The note's id.
                body: The whole note after your change, in note_create's
                    sections, every line ending in the message ids it rests on,
                    as [<id>, <id>], or a Rollup row you read, as
                    rollup(kind|subject|from/to).
                parent_revision_id: The note's head revision id your last read
                    of it showed. A stale one is refused.
                room: Optional when the agenda is one room, required when it
                    spans several. The room this note is about, as
                    `platform:conversation_id`, which the reads block
                    shows beside each room's label.
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
            try:
                parsed = parse_note_body(body, shown=shown)
            except NoteBodyError as exc:
                return _refused(exc.rule, str(exc))
            try:
                revision = await self._notes.append_revision(
                    unit=unit, note_id=note_id, body=parsed.text,
                    parent_revision_id=parent_revision_id,
                    established_by=established_by, now_ms=self._now_ms(),
                    evidence_room=_room_key(Room(platform, conversation_id)),
                )
            except StaleNoteHeadError:
                return _STALE_NOTE
            except ValueError as exc:
                return _refused("invalid_revision", str(exc))
            return wrote(platform, conversation_id, item, revision_id=revision.id)

        async def note_set_status(
            note_id: str, reason: Literal["settled", "anchor_passed"],
            base_revision_id: str = "", room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Close a note for good. Call this when its question is settled
            (what would close it has happened) or its day has passed and
            nothing is left open about it. A retired note leaves the index; its
            revisions are kept.

            If the group will need the answer again, keep it with memo_create
            first, then retire the note.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

            Args:
                note_id: The note's id.
                reason: `settled` when its closing condition is met,
                    `anchor_passed` when the day it was about has gone by and
                    nothing about it is still open.
                base_revision_id: Optional. The note's head revision id your
                    last read of it showed; a note changed since is not retired.
                room: Optional when the agenda is one room, required when it
                    spans several, as for note_revise.
                item: Optional. The agenda item this write is for, by its number.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            because = {
                "settled": NoteSupersedeReason.RETIRED_CLOSED,
                "anchor_passed": NoteSupersedeReason.RETIRED_EXPIRED,
            }.get(reason)
            if because is None:
                return _refused("bad_reason", "reason is settled or anchor_passed")
            retired = await self._notes.supersede(
                key=unit, target=note_id, replaced_by=None, because=because,
                source=None, now_ms=self._now_ms(),
                expected_head=base_revision_id or None,
            )
            if not retired:
                return _refused(
                    "not_retired",
                    "no open note by that id at that revision; read the notebook again",
                )
            for held in await self._reminders.pending_of(self._ai_peer):
                if (held.payload or {}).get("note_id") == note_id:
                    await self._reminders.mark(
                        held.id, ReminderState.CANCELLED, outcome=CANCELLED_NOTE_RETIRED,
                    )
            return wrote(platform, conversation_id, item)

        async def note_merge(
            survivor_id: str, duplicate_id: str, body: str,
            room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Fold a duplicate note into the one that keeps the question. Call
            this when two open notes track the same uncertainty. The survivor
            gets `body` as its new contents, the duplicate is retired pointing
            at it, and a reminder held on either note follows the survivor.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

            Args:
                survivor_id: The note that keeps the question.
                duplicate_id: The note folded into it.
                body: The survivor's whole note after the fold, in note_create's
                    sections, with what the duplicate knew and the survivor did
                    not folded in. Every line ends in the ids it rests on, which
                    may be any id either note already cites.
                room: Optional when the agenda is one room, required when it
                    spans several, as for note_revise.
                item: Optional. The agenda item this write is for, by its number.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            if survivor_id == duplicate_id:
                return _refused("same_note", "a note cannot be merged into itself")
            survivor = await self._notes.read(unit=unit, note_id=survivor_id)
            duplicate = await self._notes.read(unit=unit, note_id=duplicate_id)
            if survivor is None or duplicate is None:
                return _refused("not_found", "both notes must be open notes of this group")
            shown = await shown_for(platform, conversation_id, body)
            if isinstance(shown, dict):
                return shown
            try:
                parsed = parse_note_body(body, shown=replace(
                    shown, carried=shown.carried | cited_ids_in(survivor[1].body)
                    | cited_ids_in(duplicate[1].body),
                    turn_handles=shown.turn_handles | turn_handles_in(survivor[1].body)
                    | turn_handles_in(duplicate[1].body),
                ), require_open=True)
            except NoteBodyError as exc:
                return _refused(exc.rule, str(exc))
            anchor = latest_anchor(survivor[0].anchor_date, duplicate[0].anchor_date)
            now = self._now_ms()
            try:
                await self._notes.merge_into(
                    unit=unit, survivor_id=survivor_id, survivor_head=survivor[1].id,
                    duplicate_id=duplicate_id, duplicate_head=duplicate[1].id,
                    body=parsed.text, anchor_date=anchor,
                    revisit_at=default_revisit_at(anchor_date=anchor, now_ms=now),
                    reset_strikes=False, established_by=established_by, now_ms=now,
                )
            except StaleNoteHeadError:
                return _STALE_NOTE
            except ValueError as exc:
                return _refused("invalid_revision", str(exc))
            return wrote(platform, conversation_id, item)

        # -- memos (DEC-306): settled and kept, never closed --

        def _memo_screen(verb: str, parts: Mapping[str, object]) -> dict[str, object] | None:
            """The Dreamer's identifier and coordinate screen (R-18/R-28,
            `dream_screen.offence`) over what a memo write says OUTSIDE its `##
            Kept` lines (`nora.domain.memo.screened_parts`): a Kept line holds
            what the room said, verbatim (DEC-230). Refused with the field,
            never the text."""
            found = offence(parts, content_checks=self._content_checks)
            if found is None:
                return None
            reason, path = found
            self._inc("nora_dream_screened_total", adaptor="cognition", reason=reason)
            return _refused(
                reason,
                f"`{path}` carries an {reason}; only a `## Kept` line may hold what "
                f"was said verbatim, so move it there or write {verb} without it",
            )

        def _memos_off() -> dict[str, object]:
            return {"status": "unavailable", "reason": "unwired",
                    "detail": "the memo store is not wired here; nothing was written"}

        async def _memo_body(
            verb: str, title: str, body: str, platform: str, conversation_id: str,
            carried: frozenset[int] = frozenset(),
            carried_turns: frozenset[str] = frozenset(),
        ) -> tuple[Any, tuple[str, ...]] | dict[str, object]:
            """`(parsed body, citations)` of a memo write, or its refusal: the
            title and body caps, the note-body grammar over what this session
            was shown (and what the head already cites), then the screen."""
            if not title.strip() or len(title) > MEMO_TITLE_MAX_CHARS:
                return _refused(
                    "bad_title", f"a memo title is 1 to {MEMO_TITLE_MAX_CHARS} characters",
                )
            if len(body) > MEMO_BODY_MAX_CHARS:
                return _refused(
                    "too_long", f"a memo body is at most {MEMO_BODY_MAX_CHARS} characters",
                )
            shown = await shown_for(platform, conversation_id, body)
            if isinstance(shown, dict):
                return shown
            try:
                parsed = parse_memo_body(
                    body, shown=replace(shown, carried=shown.carried | carried,
                                        turn_handles=shown.turn_handles | carried_turns),
                )
                for handle in turn_handles_in(parsed.text):
                    trace = await self._tag_trace_store.get(
                        turn_id=int(handle[1:]), unit=unit,
                    ) if self._tag_trace_store is not None else None
                    cited_turn_lineage(trace, unit=unit)
            except NoteBodyError as exc:
                return _refused(exc.rule, str(exc))
            value = title_carries_value(title, parsed)
            if value is not None:
                return _refused(
                    "title_carries_value",
                    f"the title holds {value!r}, which a `## Kept` line keeps; a title "
                    "names the thing (wifi password, door code), never its value",
                )
            screened = _memo_screen(verb, screened_parts(title, parsed))
            if screened is not None:
                return screened
            return parsed, citations_of(parsed)

        async def _duplicate(title: str, but: str | None = None) -> Memo | None:
            """The kept memo of this unit that already answers to `title`, other
            than `but`."""
            for kept in await self._memos.kept(unit=unit):
                if kept.id != but and has_duplicate_open_title(title, [kept.title]):
                    return kept  # type: ignore[no-any-return]
            return None

        async def memo_create(
            title: str, body: str, mode: Literal["told", "deduced", "abduced"] = "told",
            room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Keep something the group has settled and will need again. Call
            this when a room settles a fact about the group, someone else or the
            world (a password, a deposit split, a booking reference), or asks you
            to keep one. Most settled things need no memo.

            A person's explicit ask to keep something about the group is
            written, not weighed. A fact about the speaker alone is not a memo,
            nor is anything still open (a note) or dated and attended (an
            event). A memo never closes; it is revised or withdrawn. A `##
            Kept` line holds what was said exactly as said; the title names
            the thing, never its value, and a title or condition carrying a
            value, a phone, an email or a key-like string is refused.

            Returns `status`: ok (with the `memo_id`), refused or unavailable,
            with a `reason` and `detail` when it is not ok. A memo already kept
            under that title is refused `duplicate`, naming it: revise that one.

            Args:
                title: A short handle, at most 80 characters, that names the
                    thing (wifi password, door code), never its value.
                body: `## Kept`, then optionally `## Conditions` (when it holds,
                    its exceptions). Each line is one "- " item ending in the
                    ids of the messages it rests on, as [<id>, <id>]: a message
                    you were shown or read, your own included (cite the
                    person's message asking to keep it too, when there is one).
                mode: `told` when a person said it, `deduced` when it follows
                    from what you were shown, `abduced` as a best explanation.
                room: Optional when the agenda is one room, required when it
                    spans several. The room it was settled in, as
                    `platform:conversation_id`.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by it.
            """
            if self._memos is None:
                return _memos_off()
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            checked = await _memo_body("memo_create", title, body, platform, conversation_id)
            if isinstance(checked, dict):
                return checked
            parsed, citations = checked
            async with self._lock(unit):
                twin = await _duplicate(title)
                if twin is not None:
                    return _refused(
                        "duplicate",
                        f"memo {twin.id} is already kept as {twin.title!r}; revise it "
                        "with memo_revise instead of keeping a second one",
                    )
                memo = await self._memos.create(
                    unit=unit, platform=platform, conversation_id=conversation_id,
                    title=title, body=parsed.text, citations=citations, mode=mode,
                    established_by=established_by, model=self._model_id,
                    now_ms=self._now_ms(),
                )
            return wrote(platform, conversation_id, item, memo_id=memo.id,
                         revision_id=memo.head.id)

        async def _memo_head(memo_id: str) -> Memo | dict[str, object]:
            memo = await self._memos.get(unit=unit, memo_id=memo_id)
            if memo is None:
                return _refused("not_found", "there is no memo by that id in this group")
            if not memo.is_kept:
                return _refused("withdrawn", "that memo was withdrawn; nothing moves it now")
            return memo  # type: ignore[no-any-return]

        async def memo_revise(
            memo_id: str, base_revision_id: str, body: str, title: str = "",
            mode: Literal["told", "deduced", "abduced"] = "told",
            room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Revise a kept memo. Call this when what the group settled has
            changed or gained a detail. Give back the WHOLE memo with your change
            folded in: `body` replaces it, and the old revision is kept.

            Returns `status`: ok (with the new `revision_id`), refused or
            unavailable, with a `reason` and `detail` when it is not ok
            (`stale_base` when the memo changed since you read it).

            Args:
                memo_id: The memo's id, as <memos> shows it.
                base_revision_id: The `revision_id` memo_read last showed.
                body: The whole memo after your change, in memo_create's
                    sections, every line ending in the ids it rests on.
                title: Optional. A new title; left out, the title stays.
                mode: How you reached the change: `told`, `deduced` or `abduced`.
                room: Optional when the agenda is one room, required when it
                    spans several, as for memo_create.
                item: Optional. The agenda item this write is for, by its number.
            """
            if self._memos is None:
                return _memos_off()
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            head = await _memo_head(memo_id)
            if isinstance(head, dict):
                return head
            new_title = title or head.title
            checked = await _memo_body(
                "memo_revise", new_title, body, platform, conversation_id,
                carried=cited_ids_in(head.head.body),
                carried_turns=turn_handles_in(head.head.body),
            )
            if isinstance(checked, dict):
                return checked
            parsed, citations = checked
            async with self._lock(unit):
                twin = await _duplicate(new_title, but=head.id) if title else None
                if twin is not None:
                    return _refused(
                        "duplicate", f"memo {twin.id} is already kept as {twin.title!r}",
                    )
                try:
                    revision = await self._memos.append(
                        unit=unit, memo_id=head.id, base_revision_id=base_revision_id,
                        title=new_title, body=parsed.text, citations=citations, mode=mode,
                        state="kept", established_by=established_by, model=self._model_id,
                        now_ms=self._now_ms(),
                    )
                except StaleMemoError:
                    return _refused(
                        "stale_base", f"the memo's head is now {head.head.id}; read it "
                        "again with memo_read and redo your change against what it says",
                    )
                except MemoWithdrawnError:
                    return _refused("withdrawn", "that memo was withdrawn; nothing moves it now")
            return wrote(platform, conversation_id, item, revision_id=revision.id)

        async def memo_set_status(
            memo_id: str, base_revision_id: str, cited_ids: list[str],
            room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Withdraw a kept memo for good. Call this only when someone in the
            group says it no longer holds (the password changed, the booking was
            cancelled). It leaves <memos>; its revisions are kept.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

            Args:
                memo_id: The memo's id, as <memos> shows it.
                base_revision_id: The `revision_id` memo_read last showed.
                cited_ids: The ids of the messages saying it no longer holds, at
                    least one, each one you were shown or read.
                room: Optional when the agenda is one room, required when it
                    spans several, as for memo_create.
                item: Optional. The agenda item this write is for, by its number.
            """
            if self._memos is None:
                return _memos_off()
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            head = await _memo_head(memo_id)
            if isinstance(head, dict):
                return head
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            try:
                ids, keys = check_citations(cited_ids, shown, what="memo_set_status")
            except NoteBodyError as exc:
                return _refused(exc.rule, str(exc))
            try:
                await self._memos.append(
                    unit=unit, memo_id=head.id, base_revision_id=base_revision_id,
                    title=head.title, body=head.head.body,
                    citations=(*(str(i) for i in ids), *(f"rollup({k})" for k in keys)),
                    mode="told", state="withdrawn", established_by=established_by,
                    model=self._model_id, now_ms=self._now_ms(),
                )
            except StaleMemoError:
                return _refused(
                    "stale_base", "the memo changed since you read it; read it again first",
                )
            except MemoWithdrawnError:
                return _refused("withdrawn", "that memo was withdrawn; nothing moves it now")
            return wrote(platform, conversation_id, item)

        def _evidence(
            verb: str, reasoning: str, confidence: str, cited_ids: Sequence[str],
            shown: Shown, parts: Sequence[str], *, may_be_uncited: bool = False,
        ) -> tuple[tuple[FieldMeta, ...], tuple[int, ...]] | dict[str, object]:
            """What an event write states and rests on, held to what the session
            saw: the mode and confidence the tier allows,
            and citations it was shown. `may_be_uncited`: `lapsed`, whose
            evidence is the clock."""
            try:
                meta = tuple(
                    FieldMeta(field=p, reasoning=reasoning, confidence=confidence)
                    for p in parts
                )
                if not cited_ids and may_be_uncited:
                    return meta, ()
                ids, _keys = check_citations(cited_ids, shown, what=verb)
            except InvalidEventFieldModeError as exc:
                return _refused(f"bad_{exc.field}", str(exc))
            except NoteBodyError as exc:
                return _refused(exc.rule, str(exc))
            return meta, ids

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
            instead of creating a second one.

            Every field you give rests on the rows you cite. An `abduced` value
            (your best explanation of something nobody said) is written as
            abduced and is put to the room as a question, and a yes to it later
            does not make it told.

            Returns `status`: ok (with the event's `id`), refused or unavailable,
            with a `reason` and `detail` when it is not ok.

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
                    `platform:conversation_id`, which the reads block
                    shows beside each room's label.
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
                return _refused("bad_when", "when must be ISO 8601 with an offset")
            if status not in ("staged", "proposed"):
                return _refused("bad_status", "an event is proposed or staged, nothing later")
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            parts = ["what", "status", *(["start"] if due else []),
                     *(["place"] if venue_text else [])]
            checked = _evidence(
                "event_propose", reasoning, confidence, cited_ids, shown, parts,
            )
            if isinstance(checked, dict):
                return checked
            meta, ids = checked
            try:
                event = await self._events.create_event(
                    conversation_id=conversation_id, requester_person_id=established_by,
                    title=title, when=due, venue_text=venue_text or None,
                    platform=platform, unit_id=unit, message_ids=ids,
                    status=GatheringStatus(status), reasoning=reasoning,
                    confidence=confidence, field_meta=meta,
                )
            except AmbiguousOpenGatheringsError:
                return _refused(
                    "ambiguous_open_gatherings",
                    "two or more events are already open here; revise one of them",
                )
            except (InvalidEventFieldModeError, ValueError) as exc:
                return _refused("invalid_event", str(exc))
            return wrote(platform, conversation_id, item, id=str(event.id))

        async def _event_write(
            verb: str, call: Callable[[], Awaitable[Gathering]],
            platform: str, conversation_id: str, item: int | None,
        ) -> dict[str, object]:
            """One event revision through the live service, its refusals in this
            session's words."""
            try:
                event = await call()
            except GatheringNotFoundError:
                return _refused("not_found", "there is no event by that id in this room")
            except GatheringCancelledError as exc:
                return _refused("terminal_status", f"nothing moves that event on ({exc})")
            except StaleEventRevisionError:
                return _refused(
                    "stale_base", "the event changed since you read it; read it again and "
                    "redo your change against what it says now",
                )
            except (InvalidEventFieldModeError, ValueError) as exc:
                return _refused("invalid_event", f"{verb}: {exc}")
            return wrote(
                platform, conversation_id, item, id=str(event.id),
                head_revision_id=str(event.head_revision_id),
            )

        def _uuid(text: str) -> uuid.UUID | None:
            try:
                return uuid.UUID(text)
            except ValueError:
                return None

        async def event_revise(
            event_id: str, reasoning: Literal["told", "deduced", "abduced"],
            confidence: Literal["low", "medium", "high"], cited_ids: list[str],
            when: str = "", venue_text: str = "",
            base_revision_id: str = "", room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Change the time or venue of an event that is still open in a
            room (staged, proposed or confirmed). Call this when an open
            event's details are settled or change.

            What you change rests on the rows you cite, and an `abduced` value
            is written as abduced and put as a question. One write has one
            reasoning: if the time and the place rest on different evidence,
            make two writes.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

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
                    `platform:conversation_id`, which the reads block
                    shows beside each room's label.
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
                return _refused("bad_when", "when must be ISO 8601 with an offset")
            if due is None and not venue_text:
                return _refused("nothing_to_change", "give a `when` or a `venue_text`")
            event_uuid = _uuid(event_id)
            base = _uuid(base_revision_id) if base_revision_id else None
            if event_uuid is None or (base_revision_id and base is None):
                return _refused("not_found", "that is not an event id from this room")
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            checked = _evidence(
                "event_revise", reasoning, confidence, cited_ids, shown,
                [*(["start"] if due else []), *(["place"] if venue_text else [])],
            )
            if isinstance(checked, dict):
                return checked
            meta, ids = checked
            return await _event_write("event_revise", lambda: self._events.update_event(
                event_id=event_uuid, conversation_id=conversation_id, when=due,
                venue_text=venue_text or None, platform=platform,
                unit_id=unit, base_revision_id=base,
                message_ids=ids, reasoning=reasoning, confidence=confidence,
                field_meta=meta,
            ), platform, conversation_id, item)

        async def event_set_status(
            event_id: str, status: Literal["confirmed", "cancelled", "happened", "lapsed"],
            reasoning: Literal["told", "deduced", "abduced"],
            confidence: Literal["low", "medium", "high"], cited_ids: list[str],
            base_revision_id: str = "", room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Move an event that is still open in a room (staged, proposed or
            confirmed) to a new state. Call this when the room confirms it,
            says it happened, or says it is off.

            The move rests on the rows you cite; an `abduced` one is written as
            abduced and put as a question ("did it happen?"). A move to `lapsed`
            (the date passed with nothing agreed) may cite nothing, since the
            evidence is the clock. Only a status before `happened` moves: an
            event that happened, was cancelled or lapsed stays as it is.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

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
                    `platform:conversation_id`, which the reads block
                    shows beside each room's label.
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
                return _refused("bad_status", "use confirmed, cancelled, happened or lapsed")
            event_uuid = _uuid(event_id)
            base = _uuid(base_revision_id) if base_revision_id else None
            if event_uuid is None or (base_revision_id and base is None):
                return _refused("not_found", "that is not an event id from this room")
            shown = await shown_for(platform, conversation_id, "")
            if isinstance(shown, dict):
                return shown
            checked = _evidence(
                "event_set_status", reasoning, confidence, cited_ids, shown, ["status"],
                may_be_uncited=parsed is GatheringStatus.LAPSED,
            )
            if isinstance(checked, dict):
                return checked
            meta, ids = checked
            return await _event_write("event_set_status", lambda: self._events.set_event_status(
                event_id=event_uuid, conversation_id=conversation_id, status=parsed,
                base_revision_id=base, platform=platform, unit_id=unit,
                message_ids=ids,
                reasoning=reasoning, confidence=confidence, field_meta=meta,
            ), platform, conversation_id, item)

        async def standing_revise(
            where_it_stands: str, hold_open: str | None = None, i_owe: str | None = None,
            mode: Literal["deduced", "abduced", "said"] = "deduced",
            base_revision_id: str | None = None, room: str = "", item: int | None = None,
        ) -> dict[str, object]:
            """Revise a room's Standing: where it stands right now. Call this
            when what a room has settled, or is holding open, has changed. Give
            the whole position, not a delta.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

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
                    spans several. The room, as `platform:conversation_id`,
                    which the reads block shows beside each room's label.
                item: Optional. The agenda item this write is for, by its
                    number. A `written` close of that item is backed by a write
                    that names it.
            """
            resolved = _write_target(room, item, state)
            if isinstance(resolved, dict):
                return resolved
            (platform, conversation_id), item = resolved
            async with self._lock(unit):
                head = await self._prior_standing(platform, conversation_id)
                prior = None if head is None else head.standing
                revision = await live_standing_revise(
                    store=self._standing, prior=prior, platform=platform,
                    conversation_id=conversation_id, where_it_stands=where_it_stands,
                    working_set=() if prior is None else prior.working_set,
                    hold_open=hold_open, i_owe=i_owe, mode=mode,
                    last_folded_row_id=None if prior is None else prior.last_folded_row_id,
                    base_revision_id=base_revision_id,
                    prior_revision_id=None if head is None else head.revision_id,
                    now_ms=self._now_ms(), source="cognition",
                )
            if revision.refusal is not None:
                return _refused(revision.refusal.reason, revision.refusal.detail)
            return wrote(platform, conversation_id, item)

        async def wake_at(
            subject_rows: list[str], at: str, why: str,
        ) -> dict[str, object]:
            """Ask for your own next look at some rows, at a time you name. Call
            this at the end of a pass when something will be worth checking
            again later ("the event is at 20:00, look at 19:40"). Nothing is
            sent: it only wakes you. A new one that shares a subject row with a
            pending one replaces it.

            Returns `status`: ok (with the `wake_id` to move or cancel it), refused
            or unavailable, with a `reason` and `detail` when it is not ok.

            Args:
                subject_rows: The rows to look at again, at least one.
                at: When, ISO 8601 with an offset.
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
                created_today = await self._store.count_self_wakes_created(
                    unit=unit, since_ms=call_ms - _DAY_MS,
                )
                wake_ref = self._mint_ref()
                answer = await pace.wake_at(
                    ledger=self._store, stimuli=self._stimuli, session_id=state.session_id,
                    unit=unit, subject_rows=subject_rows, at_ms=at_ms, why=why,
                    now_ms=call_ms, wake_ref=wake_ref,
                    cooldown_seconds=self._cooldown_seconds, pending=pending,
                    max_self_wakes=self._max_self_wakes, created_today=created_today,
                    max_self_wakes_per_day=self._max_self_wakes_per_day,
                )
            if not answer.refused:
                self._inc("nora_self_wake_total", outcome="created")
            return _answer(answer, wake_id=wake_ref)

        async def wake_move(wake_id: str, at: str, why: str) -> dict[str, object]:
            """Move a self-wake you set to a new time. Call this when the time
            you chose no longer fits. The old time no longer wakes you.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

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
                answer = await pace.wake_move(
                    ledger=self._store, stimuli=self._stimuli,
                    session_id=state.session_id, unit=unit, wake_ref=wake_id, at_ms=at_ms,
                    why=why, now_ms=call_ms, cooldown_seconds=self._cooldown_seconds,
                    pending=pending, created_by_session_id=None,
                )
            if not answer.refused:
                self._inc("nora_self_wake_total", outcome="moved")
            return _answer(answer)

        async def wake_cancel(wake_id: str, why: str) -> dict[str, object]:
            """Cancel a self-wake you set. Call this when there is no longer
            anything to look at. It will not wake you.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

            Args:
                wake_id: The id `wake_at` returned.
                why: The reason you cancel it.
            """
            async with self._lock(unit):
                call_ms = self._now_ms()
                pending = await self._pending(unit, call_ms)
                answer = await pace.wake_cancel(
                    ledger=self._store, stimuli=self._stimuli,
                    session_id=state.session_id, unit=unit, wake_ref=wake_id, why=why,
                    now_ms=call_ms, pending=pending, created_by_session_id=None,
                )
            if not answer.refused:
                self._inc("nora_self_wake_total", outcome="cancelled")
            return _answer(answer)

        async def decision_conclude(
            subject: str, decision: str, options: str = "", leaning: str = "",
            conditions: str = "", sources: list[str] | None = None,
            standing_room: str = "", standing_body: str = "",
            hold_open: str = "", i_owe: str = "",
        ) -> dict[str, object]:
            """Record what this turn concluded, explicitly. Call this when you
            settled something this turn: what you decided, what the options
            were, which way you lean, what would close it, and the rows or
            entries it rests on. The call itself IS the record: it lands in
            this turn's trace as a toolUse, and the shared view shows it to
            the next turn and the next session before any consolidation runs.
            It writes no Notebook row: a conclusion here is recorded, not a
            judged entry; Cognition still authorizes what becomes one.

            Returns `status`: ok, refused or unavailable, with a `reason`
            and `detail` when it is not ok.

            Args:
                subject: The `kind:id` of the open entry this conclusion is
                    about, or a short name when none applies.
                decision: The conclusion in one sentence.
                options: Optional. The options you weighed.
                leaning: Optional. Which way you lean, and why.
                conditions: Optional. What would settle or close this.
                sources: Optional. The row/entry ids this rests on.
                standing_room: Optional. `platform:conversation_id` — set it
                    with `standing_body` when the conclusion is a position
                    update for that room; the Record keeps it; Cognition authorizes.
                standing_body: Optional. The new position, verbatim.
                hold_open: Optional. What you are deliberately keeping open.
                i_owe: Optional. What you owe that room.
            """
            if not decision.strip():
                return _refused("no_decision", "say what you concluded")
            # Same source contract as the engagement tool: every citation
            # must resolve — a transcript row id, or a `kind:id` open entry
            # the canonical stores can confirm — else the record stays
            # `unresolved`, never an ok on a guessed provenance.
            if not (sources or ()):
                return {"status": "unresolved",
                        "reason": "source_required",
                        "detail": "cite the transcript row id(s) this "
                                  "conclusion rests on"}
            for source in sources or ():
                if not isinstance(source, str):
                    return {"status": "unresolved",
                            "reason": "source_unresolved",
                            "detail": f"{source!r} is not a citation this "
                                      "session can resolve"}
                if source.isdigit():
                    # Same existence check the engagement tool runs — a
                    # transcript row id must RESOLVE against the unit's
                    # Record, not merely parse.
                    if self._resolve_row is None:
                        return {"status": "unavailable",
                                "reason": "resolver_unavailable",
                                "detail": "cited rows cannot be resolved "
                                          "without the message store"}
                    hit = await self._resolve_row(unit, int(source))
                    if hit is None:
                        return {"status": "unresolved",
                                "reason": "source_unresolved",
                                "detail": f"no transcript row {source} in "
                                          "this unit"}
                    # The resolved row's OWN room is the gate — recorded so
                    # the destination projection sees the real source
                    # requirement (TurnRecording is installed by the agent
                    # runner).
                    src_origin = record_room_key(hit[0], hit[1])
                    record_requirement(DisclosureRequirement(
                        src_origin, (message_lineage(src_origin),)))
                    continue
                resolved_source = await self._resolve_named_subject(
                    unit, state, source)
                if resolved_source is None:
                    return {"status": "unresolved",
                            "reason": "source_unresolved",
                            "detail": (
                                f"{source!r} is not a transcript row id or an "
                                "open entry this session can cite"
                            )}
                # A named entry's own canonical lineage is the gate —
                # carried on the Record, never re-derived downstream.
                source_requirement = await self._named_source_requirement(
                    unit, state, *resolved_source)
                if source_requirement is None or not source_requirement.known:
                    return {"status": "unresolved",
                            "reason": "source_unresolved",
                            "detail": f"the gates of {source!r} could not be "
                                      "resolved"}
                record_requirement(source_requirement)
            return {"status": "ok"}

        async def remind(
            intent: Literal["tell", "ask"], text: str, subject: str, action: str,
            subject_rows: list[str], at: str,
            room: str = "", also_rooms: list[str] | None = None,
            new_action: str = "",
        ) -> dict[str, object]:
            """Reach a room at a time: when the moment comes you get a turn in
            that room to say something to it or ask it something, and you may
            still stay silent then. Call this when a room should hear from you
            and not before.

            `subject` names the open entry the act is about, as `kind:id`
            (`note:`, `decision:`, `commitment:`, `thread:` or `event:` plus the
            id the note index and ledger read show). It must resolve to an
            open entry or the call is `unresolved`, never scheduled on a
            citation alone. `action` is YOUR short key for the act
            (`confirm-dinner-date`, `ask-vote-deadline`): two questions about
            the same subject are different actions, and the same
            subject+action already pending somewhere you did not name is
            refused `conflict_other_room`. `also_rooms` is how you mean the
            same act in several rooms at once.

            Returns `status`: ok (with `reminder_ids`), refused or
            unavailable, plus `unresolved` when `subject` does not resolve to
            the open entry the act is about, each with a `reason` and `detail`
            when it is not ok.

            Args:
                intent: `tell` to say something, `ask` to ask something.
                text: What to raise, in a sentence, as you will read it when it
                    comes due.
                subject: The open entry the act is about, as `kind:id`.
                action: Your key for this act on this subject: same act again
                    is the same key, a different question is a different one.
                subject_rows: The evidence rows it rests on, at least one.
                at: When to reach the room, ISO 8601 with an offset.
                room: Optional when the agenda is one room, required when it
                    spans several. The room to reach, as
                    `platform:conversation_id`, which the reads block
                    shows beside each room's label.
                also_rooms: Optional. Every other room this same act should
                    reach, each as `platform:conversation_id`. An act the unit
                    already has pending somewhere else is refused unless you
                    name that room here or in `room`.
                new_action: Optional. Required, a short why, only when this is
                    a DIFFERENT act on a subject an `intent` act already
                    waits on: a pending act's action key is the stable name
                    of what is pending, so a second act needs a recorded
                    reason, not a fresh spelling. The why is kept on the row.
            """
            resolved = _room_of(room, state)
            if isinstance(resolved, dict):
                return resolved
            platform, conversation_id = resolved
            at_ms = _when_ms(at)
            if at_ms is None:
                return _bad_time("at")
            if not text.strip():
                return _refused("no_text", "say what to raise when it comes due")
            action_key = action.strip().lower()
            if not action_key:
                return _refused(
                    "no_action", "name this act: same act again is the same key",
                )
            destinations = [(platform, conversation_id)]
            for extra in also_rooms or []:
                r = _room_of(extra, state)
                if isinstance(r, dict):
                    return r
                if r not in destinations:
                    destinations.append(r)
            async with self._lock(unit):
                resolved_subject = await self._resolve_named_subject(
                    unit, state, subject,
                )
                if resolved_subject is None:
                    # The `unresolved` toolResult in this session's trace is
                    # the durable evidence — the shared view reads it back
                    # through the turn Record, across restarts and processes.
                    return {
                        "status": "unresolved",
                        "reason": "subject_unresolved",
                        "detail": (
                            f"{subject!r} does not resolve to an open note, "
                            "decision, commitment, thread or event this act is "
                            "about: name the open entry's `kind:id`, or do not "
                            "schedule it"
                        ),
                    }
                subject_id, subject_kind = resolved_subject[1], resolved_subject[0]
                pending = await self._pending_reminds()
                context = await self._remind_context(
                    unit, platform, conversation_id, subject_rows, at_ms,
                )
                refusal = check_remind(
                    subject_rows=subject_rows, intent=intent,
                    platform=platform,
                    conversation_id=conversation_id, pending=pending,
                    context=context,
                    subject=resolved_subject, action=action_key,
                    extra_destinations=destinations[1:],
                )
                if refusal is not None:
                    return _refused(refusal.reason, refusal.detail)
                same_intent = {
                    p.action for p in pending
                    if p.subject == resolved_subject
                    and p.intent == intent
                } - {action_key}
                if same_intent and not new_action.strip():
                    return _refused(
                        "conflict_same_subject",
                        f"an {intent!r} act is already pending on that subject "
                        f"as {sorted(same_intent)[0]!r}: schedule under the same "
                        "action if it is the same act, or pass new_action with "
                        "why this is a different question",
                    )
                # Every destination validated BEFORE a row exists: proactive
                # policy and the requester cap apply per room, so a batch
                # refused late is refused whole.
                for dest_platform, dest_cid in destinations[1:]:
                    if self._room_proactive is not None and not self._room_proactive(
                        dest_platform, dest_cid,
                    ):
                        return _refused(
                            "room_not_proactive",
                            f"you may not reach {dest_platform}:{dest_cid} "
                            "unasked",
                        )
                link: dict[str, str] = {"plan_id": str(uuid.uuid4())}
                if new_action.strip():
                    link["new_action_reason"] = new_action.strip()[:200]
                if subject_kind == "note":
                    shown_note = await self._notes.read(
                        unit=unit, note_id=subject_id)
                    if shown_note is None:
                        return _refused(
                            "not_found", f"no open note {subject_id!r}")
                    # The note link machinery (merge repoint, retire-cancel,
                    # the moved-note screen and its global one-open-row-per-
                    # note arbiter) all key on these payload fields — the
                    # deliberate stronger invariant: a note-subject act is
                    # single-destination, while `also_rooms` covers acts on
                    # decisions, commitments, threads and events.
                    link["note_id"] = shown_note[0].id
                    link["revision_id"] = shown_note[1].id
                    link["note_title"] = shown_note[0].title
                pending_dests = {
                    (p.platform, p.conversation_id) for p in pending
                    if p.subject == resolved_subject and p.action == action_key
                }
                unit_rooms = (
                    list(self._rooms_of_unit(unit))
                    if self._rooms_of_unit is not None
                    # Unwired harnesses have no canonical scope: the agenda's
                    # own rooms are all the scope there is (the same parity
                    # the unwired debrief keeps).
                    else [tuple(k.split(":", 1)) for k in state.rooms]
                )
                if not unit_rooms:
                    # The conflict arbiter needs the CANONICAL unit, not this
                    # agenda's room list: an act pending in a unit room the
                    # agenda did not name is exactly the race the lock exists
                    # to stop, so a scope we cannot establish refuses the
                    # write rather than narrowing it.
                    return {"status": "unavailable",
                            "reason": "unit_scope_unavailable",
                            "detail": "the unit's room set could not be read"}
                delivered = await self._reminders.delivered_subject(
                    unit_rooms, requester_person_id=self._ai_peer,
                    subject_kind=subject_kind, subject_id=subject_id,
                    action=action_key, limit=5,
                )
                if len(delivered) >= 5:
                    # The keyed read cut off: an uncounted delivered act
                    # could sit past the cap, so «not delivered» is not
                    # established.
                    return {"status": "unavailable",
                            "reason": "delivered_check_incomplete",
                            "detail": "the delivered check could not be completed"}
                if delivered:
                    return _refused(
                        "duplicate_pending",
                        "that act was already delivered for the room(s) named",
                    )
                rows = [
                    self._reminder_row(
                        platform=dest_platform, conversation_id=dest_cid,
                        text=text, intent=intent, subject_rows=subject_rows,
                        at_ms=at_ms, unit=unit,
                        link={
                            **link,
                            "subject_kind": subject_kind,
                            "subject_id": subject_id,
                            "action": action_key,
                        },
                    )
                    for dest_platform, dest_cid in destinations
                    if (dest_platform, dest_cid) not in pending_dests
                ]
                if not rows:
                    return _refused(
                        "duplicate_pending",
                        "the same act is already pending for the room(s) named",
                    )
                try:
                    # ONE transaction, advisory-locked on the unit with the
                    # cross-room conflict check INSIDE it: every destination
                    # lands or none does, against any writer in any process.
                    await self._reminders.create_many(
                        rows, arbitration_key=f"proactive:{unit}",
                        arbitration_rooms=[tuple(r) for r in unit_rooms],
                    )
                except (ConflictingProactiveActError,
                        DuplicateProactiveNoteError,
                        DuplicateProactiveSubjectError,
                        DuplicateReminderError):
                    return _refused(
                        "duplicate_pending",
                        "the same act is already pending for the room(s) named",
                    )
                reminder_ids = [str(r.id) for r in rows]
            return {"status": "ok", "reminder_id": reminder_ids[0],
                    "reminder_ids": reminder_ids}

        async def _own_reminder(reminder_id: str) -> Reminder | dict[str, object]:
            row: Reminder | None = next(
                (r for r in await self._reminders.pending_of(self._ai_peer)
                 if str(r.id) == reminder_id), None,
            )
            if row is None:
                return _refused("not_found", f"no pending reminder of yours {reminder_id!r}")
            if _room_key(Room(row.platform, row.conversation_id)) not in state.allowed_rooms:
                return _outside_unit(state)
            return row

        async def remind_move(
            reminder_id: str, at: str, why: str,
        ) -> dict[str, object]:
            """Move a reminder you set to a new time. Call this when the time
            you chose no longer fits. Only your own.

            Returns `status`: ok (with the new `reminder_id`), refused or
            unavailable, with a `reason` and `detail` when it is not ok.

            Args:
                reminder_id: The id `remind` returned.
                at: The new time, ISO 8601 with an offset.
                why: The reason you move it.
            """
            at_ms = _when_ms(at)
            if at_ms is None:
                return _bad_time("at")
            async with self._lock(unit):
                row = await _own_reminder(reminder_id)
                if isinstance(row, dict):
                    return row
                refusal = check_remind_time(RemindContext(
                    now_ms=self._now_ms(), at_ms=at_ms,
                    min_lead_seconds=self._remind_min_lead_seconds,
                    max_horizon_days=self._remind_max_horizon_days,
                ))
                if refusal is not None:
                    return _refused(refusal.reason, refusal.detail)
                moved = replace(
                    row, id=uuid.uuid4(), due_at=now_utc(at_ms),
                    created_at=now_utc(self._now_ms()),
                )
                if not await self._reminders.cancel(row.id, conversation_id=row.conversation_id):
                    # The heartbeat claimed it between the read and the cancel:
                    # it is firing now, so a moved copy would say it twice.
                    return _refused("already_due", "that reminder is coming due now")
                await self._reminders.create(moved)
            return {"status": "ok", "reminder_id": str(moved.id)}

        async def remind_cancel(reminder_id: str, why: str) -> dict[str, object]:
            """Cancel a reminder you set. Call this when the room no longer
            needs to hear it. Only your own.

            Returns `status`: ok, refused or unavailable, with a `reason` and
            `detail` when it is not ok.

            Args:
                reminder_id: The id `remind` returned.
                why: The reason you cancel it.
            """
            async with self._lock(unit):
                row = await _own_reminder(reminder_id)
                if isinstance(row, dict):
                    return row
                await self._reminders.cancel(row.id, conversation_id=row.conversation_id)
            return {"status": "ok"}

        def unstop() -> None:
            if state.request_state is not None:
                state.request_state.pop("stop_event_loop", None)

        def acting(verb: Any) -> Any:
            """Every verb but `close` marks the session as having acted, before
            it runs and whatever it answers (`_all_quiet`), and takes back a
            stop a quiet `close` set earlier in the same model turn: the model
            must see what this verb answered (a refusal it may correct). Taken
            back before AND after it runs, so tools run concurrently in one
            turn cannot order a close's stop after it."""
            @functools.wraps(verb)
            async def call(*args: Any, **kwargs: Any) -> dict[str, object]:
                item = inspect.signature(verb).bind(*args, **kwargs).arguments.get("item")
                covered = (item in state.admitted_indices if item is not None
                           else set(state.items_by_index) <= state.admitted_indices)
                if self._memory is not None and not covered:
                    return _refused("proposal_required", "admit a proposal covering the item first")
                key = None
                if self._memory is not None and verb.__name__ in (
                        "note_create", "memo_create", "event_propose"):
                    key = await self._memory.act_key(state, verb.__name__,
                        inspect.signature(verb).bind(*args, **kwargs).arguments)
                    existing = await self._memory.records.act_result(key, state.session_id)
                    if existing is not None:
                        await self._memory.link_result(state,
                            {**existing, **({"event_id": existing["id"]}
                                if verb.__name__ == "event_propose" else {})})
                        return dict(existing)
                state.acted = True
                unstop()
                try:
                    result: dict[str, object] = await verb(*args, **kwargs)
                finally:
                    unstop()
                if key is not None and result.get("status") == "ok":
                    # ponytail: a second transaction after the verb's commit; a crash
                    # between them loses the key, so a retried event_propose could
                    # write a second gathering. Write both in one transaction if seen.
                    await self._memory.records.act_result(key, state.session_id, result)
                    await self._memory.link_result(state,
                        {**result, **({"event_id": result["id"]}
                            if verb.__name__ == "event_propose" else {})})
                return result
            return call

        async def memory_propose(
            readings: list[dict[str, Any]], entities: list[dict[str, Any]],
            relations: list[dict[str, Any]], evidence: list[dict[str, Any]],
            corrections: list[dict[str, Any]], items: list[int],
        ) -> dict[str, Any]:
            """Admit understanding. Call this when agenda items are understood.

            Returns admitted items, refusals with reasons and the connected subgraph.
            Empty lists explicitly record nothing to understand.

            Args:
                readings: Grounded readings of the shown sources.
                entities: Proposed entities.
                relations: Proposed relations.
                evidence: Shown source keys and exact spans.
                corrections: Changes to existing relations.
                items: Agenda indices covered by this understanding.
            """
            if self._memory is None:
                return {"status": "unavailable", "note": "Memory admission is unavailable."}
            result: dict[str, Any] = await self._memory.propose(state, items=items, payload={
                "readings": readings, "entities": entities, "relations": relations,
                "evidence": evidence, "corrections": corrections})
            return result

        async def profile_revise(
            entity: list[str], proposals: list[dict[str, Any]],
        ) -> dict[str, Any]:
            """Record profile changes in shadow. Call this when admitted evidence changes a profile.

            Args:
                entity: The profile subject kind and id.
                proposals: Parts, text, relation ids and evidence keys.
            """
            if self._memory is None:
                return {"status": "unavailable"}
            result: dict[str, Any] = await self._memory.profile(state, entity, proposals)
            return result

        verbs = (
            close, *(acting(v) for v in (
                note_create, note_revise, note_set_status, note_merge, memo_create,
                memo_revise, memo_set_status, event_propose, event_revise, event_set_status,
                standing_revise, wake_at, wake_move, wake_cancel, remind, remind_move,
                remind_cancel, decision_conclude,
            )),
        )
        if self._memory is not None:
            verbs = (memory_propose, profile_revise, *verbs)
        if state.historical:
            verbs = (memory_propose, profile_revise, close)
        return [*read_tools, *(self._counted(unit, v.__name__, v) for v in verbs)]

    # `created_by_session_id=None` on every pace move and cancel above, on
    # purpose: the pending seam returns only rows COGNITION wrote, so «own»
    # already holds, and passing this session's id would refuse a self-wake set
    # by an EARLIER session, which is the normal case («she keeps, moves or
    # cancels them»).

    def _reminder_row(
        self, *, platform: str, conversation_id: str, text: str, intent: str,
        subject_rows: Sequence[str], at_ms: int, unit: str, link: Mapping[str, str],
    ) -> Reminder:
        """The row the tag path's `remind` writes for Nora's own decision
        (`kind="proactive"`, owned by her `ai_peer` id, fired by the one fire
        path's proactive occasion and screened there by the room's flag), with
        who decided it in its payload."""
        return Reminder(
            id=uuid.uuid4(), requester_person_id=self._ai_peer, platform=platform,
            conversation_id=conversation_id, conversation_type="group", text=text,
            created_at=now_utc(self._now_ms()), due_at=now_utc(at_ms),
            state=ReminderState.PENDING, fired_at=None, outcome=None, kind="proactive",
            payload={
                **link, "intent": intent, "subject_rows": " ".join(subject_rows),
                "by": f"{COGNITION_PATH_PREFIX}{unit}",
            },
        )

    async def _pending_reminds(self) -> list[PendingRemind]:
        """Nora's own pending reminders, as the dedupe rule reads them."""
        out: list[PendingRemind] = []
        for r in await self._reminders.pending_of(self._ai_peer):
            payload = r.payload or {}
            kind = payload.get("subject_kind")
            sid = payload.get("subject_id")
            action = payload.get("action")
            out.append(PendingRemind(
                reminder_ref=str(r.id), platform=r.platform,
                conversation_id=r.conversation_id,
                subject_rows=tuple(str(payload.get("subject_rows") or "").split()),
                subject=(
                    (kind, sid)
                    if isinstance(kind, str) and kind
                    and isinstance(sid, str) and sid
                    else None
                ),
                action=action if isinstance(action, str) else "",
                intent=str(payload.get("intent") or ""),
            ))
        return out

    _SUBJECT_KINDS = frozenset({"note", "decision", "commitment", "thread", "event"})

    async def _resolve_named_subject(
        self, unit: str, state: Any, subject: str,
    ) -> tuple[str, str] | None:
        """The shared-object key the act is about, resolved from the EXPLICIT
        `kind:id` the producer named — never from cited evidence rows, whose
        set can match several open entries and silently choose wrong (the
        reviewer's finding: citation-set matching is ambiguity selection, not
        resolution).

        The named kind+id must be an OPEN entry in this unit's canonical
        stores: an open note id, an open ledger entry (decision, commitment
        or thread), or an open event id — each checked against its store's
        open set, not pattern-matched. `None` means `unresolved`: the act is
        carried in session evidence for the next session to address, never
        scheduled on a guess."""
        kind, _, wanted = (subject or "").strip().partition(":")
        if kind not in self._SUBJECT_KINDS or not wanted.strip():
            return None
        wanted = wanted.strip()
        if kind == "note":
            open_notes = {n.id for n in await self._notes.standing(unit=unit)}
            return ("note", wanted) if wanted in open_notes else None
        rooms = list(state.allowed_rooms.values())
        if kind in {"decision", "commitment", "thread"} and self._ledger is not None:
            for platform, conversation_id in rooms:
                for entry in await self._ledger.read(
                    unit=unit, platform=platform, conversation_id=conversation_id,
                ):
                    if entry.id == wanted and entry.kind == kind \
                            and entry.status == "open":
                        return (entry.kind, entry.id)
            return None
        if kind == "event":
            for platform, conversation_id in rooms:
                for event in await self._open_events(unit, platform, conversation_id):
                    if str(event.id) == wanted:
                        return ("event", str(event.id))
        return None

    async def _named_source_requirement(
        self, unit: str, state: Any, kind: str, wanted: str,
    ) -> DisclosureRequirement | None:
        """The resolved entry's own canonical lineage, flattened to the
        requirement entries a destination checks — the same lookups
        `_resolve_named_subject` ran, returning the gates this time.
        `None` = no gates to propagate = unresolved."""
        rooms = list(state.allowed_rooms.values())
        entry = None
        if kind == "note":
            entry = next(
                (n for n in await self._notes.standing(unit=unit)
                 if n.id == wanted), None)
        elif (kind in {"decision", "commitment", "thread"}
                and self._ledger is not None):
            for platform, conversation_id in rooms:
                for candidate in await self._ledger.read(
                    unit=unit, platform=platform,
                    conversation_id=conversation_id,
                ):
                    if (candidate.id == wanted and candidate.kind == kind
                            and candidate.status == "open"):
                        entry = candidate
        elif kind == "event":
            for platform, conversation_id in rooms:
                for event in await self._open_events(
                    unit, platform, conversation_id
                ):
                    if str(event.id) == wanted:
                        entry = event
        if entry is None or getattr(entry, "lineage", None) is None:
            return None
        flat = tuple(
            le for _name, es in entry.lineage.sections for le in es
        )
        return DisclosureRequirement(
            None, flat, tuple(entry.exclude_persons),
        ) if flat else None

    async def _remind_context(
        self, unit: str, platform: str, conversation_id: str, subject_rows: Sequence[str],
        at_ms: int,
    ) -> RemindContext:
        """What `remind`'s code-side guards need, read now: the room's
        `proactive` flag, whether a row the reminder cites is only abduced (an
        event one of whose stated fields is `abduced`, by `field_mode`), how
        many pending rows Nora's own requester id holds, and the live lead and
        horizon."""
        proactive = (
            True if self._room_proactive is None
            else self._room_proactive(platform, conversation_id)
        )
        cited = {_bare_row(r) for r in subject_rows}
        abduced = any(
            str(e.id) in cited and _is_abduced(e)
            for e in await self._open_events(unit, platform, conversation_id)
        ) if cited else False
        return RemindContext(
            room_proactive=proactive, abduced_subject=abduced,
            max_pending=self._max_pending_per_requester,
            live_pending=await self._reminders.count_pending(self._ai_peer),
            now_ms=self._now_ms(), at_ms=at_ms,
            min_lead_seconds=self._remind_min_lead_seconds,
            max_horizon_days=self._remind_max_horizon_days,
        )

    async def _pending(self, unit: str, now_ms: int) -> Sequence[PendingSelfWake]:
        """The unit's pending self-wakes, read over a BOUNDED window (a ref
        silent for longer than the horizon plus a day is past due)."""
        return await self._store.pending_self_wakes(
            unit=unit, since_ms=now_ms - int(PENDING_WINDOW_SECONDS * 1000),
        )


def now_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
