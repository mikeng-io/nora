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
It ends exactly one way: `closed` (the model ended its turn; observed stimuli are consumed
and unseen ones deferred), written in ONE
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
REFUSES any tool not on `read_only_tools`), guidance gated by the offered tools,
and its own write, pace and remind verbs (`_build_tools`). Each write
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
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, tzinfo
from typing import Any, ClassVar, Protocol

from nora.agent import cognition_pace as pace
from nora.agent.fetch_watch import Fetched, GraphFetched, watch_reads
from nora.app.cognition_session_lessons import (
    read as lesson_read,
)
from nora.app.cognition_session_lessons import (
    settle as lesson_settle,
)
from nora.app.cognition_session_lessons import (
    verbs as lesson_verbs,
)
from nora.app.session_verbs import SessionDeps, _refused, _room_key, now_utc
from nora.domain.cognition_ground import Grounding, RecordItem, rollup_status
from nora.domain.cognition_pace import (
    DEFAULT_MAX_SELF_WAKES_PER_DAY,
    PENDING_WINDOW_SECONDS,
    PendingSelfWake,
)
from nora.domain.cognition_remind import (
    PendingRemind,
    RemindContext,
)
from nora.domain.cognition_session import (
    Agenda,
    AgendaItem,
    Room,
    Situation,
    build_situation,
)
from nora.domain.gatherings import (
    EVENT_PARTS,
    Gathering,
    field_mode,
)
from nora.domain.lessons import LessonEvidenceView
from nora.domain.memory_graph import ENTITY_KINDS, ENTRY, Source
from nora.domain.note_body import (
    Shown,
    ShownMessage,
)
from nora.domain.notes import (
    NOTE_OBJECT_REFS_MAX,
    SourceRef,
)
from nora.domain.observations import SourceScope, parse_handle
from nora.domain.ports import (
    CognitionStore,
    NewCarry,
    NewCognitionSession,
    NewWakeOutcome,
    RecordedOutcome,
    StimulusStore,
)
from nora.domain.reminders import (
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
from nora.language.cognition_parse import rendered_new_sources
from nora.language.cognition_render import (
    RenderBudget,
    render_agenda,
    room_label,
    speaker_of,
)
from nora.obs.tracing import eval_capture_enabled, record_eval_event

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
    "note_create", "note_revise", "note_set_status", "note_merge",
    "memo_create", "memo_revise", "memo_set_status", "event_propose", "event_revise",
    "event_set_status", "standing_revise", "topic_create",
    "topic_revise", "topic_set_status",
    "wake_at", "wake_move", "wake_cancel", "remind", "remind_move", "remind_cancel",
    "memory_propose", "profile_read", "profile_revise",
    "lesson_write", "lesson_retire",
})

_PACE_VERBS = frozenset({"wake_at", "wake_move", "wake_cancel"})
#: What a verb says when a store or a read it needed failed. A write may have
#: landed part-way, so «nothing was written» would be false, and a retry on that
#: word could write it twice.
_UNFINISHED = (
    "I couldn't finish that just now, so it may or may not have taken effect: don't "
    "assume either, and leave it for the next pass."
)

#: `lesson_view` sentinel on `_session_input`: `on_wake` hands the view it
#: already read; a probe reaches `_session_input` without one and reads there.
_UNREAD: Any = object()


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
    reserve_tokens: bool = False


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
    `_fn` hook). `nora.app.cognition_agent_runner.DreamerAgentRunner`
    is the production implementation, delegating to
    `DreamerRuntime.run_agent` — the one model-invocation route (node 30);
    `tests/behavioral/
    test_cognition_session_writes_nothing_live.py`'s `ScriptedAgentRunner`
    drives a fixed sequence of tool calls with no model and no Strands.

    **It RAISES on failure.** A model loop that errors or runs out its clock
    is not a session that returned; an implementation that swallowed either
    into a normal `CognitionRunResult` would make a failed session read as one
    that ran and advance the
    watermark past stimuli nobody looked at."""

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult: ...


@dataclass(frozen=True)
class CognitionSessionOutcome:
    session_id: int
    items: int
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
#: `(unit, rooms, *, clock=None)`: `clock` is the historical lane's as-of bind
#: — a live call passes nothing and the source's own clock stands.
ReadToolsFor = Callable[..., list[Any]]
#: `(unit, agenda items, the claimed stimuli)` -> what she is shown beside the
#: agenda (`nora.domain.cognition_ground`): the Record rows that raised each
#: stimulus and the Register index lines. RAISES when it cannot read: a session
#: that could not be grounded must not run on the bare counts.
#: The fourth argument is the session's own instant (`now_ms`): a live
#: wake's clock or a historical session's evidence time; `None` means "now".
#: The fifth (`historical`) withholds reads with no as-of view — held topics.
Grounder = Callable[
    [str, Sequence[AgendaItem], Sequence[Stimulus], int | None, bool],
    Awaitable[Grounding],
]
#: A historical session passes its evidence instant as `clock`, binding every
#: built tool's `now_fn` to that instant rather than the process's wall clock;
#: a live session passes nothing and `read_tools_for` defaults it.
HistoricalClock = Callable[[], datetime]


def _as_clock(instant: datetime) -> HistoricalClock:
    """`instant` as the constant clock a historical session's reads are built
    under."""
    return lambda: instant


SkillsFor = Callable[[], str]
#: The versions (content digests) of the guidance `SkillsFor` would inject for
#: this session, recorded on the session row.
SkillVersionsFor = Callable[[], Mapping[str, object]]
#: `(text, versions)` for this session from ONE resolution of each skill:
#: the text injected and the digests recorded, so a hot reload between two
#: separate reads cannot leave a row naming a digest of text the model never read.
SkillsResolvedFor = Callable[[], tuple[str, Mapping[str, object]]]
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
#: predates it still satisfies the Protocol; the loop runs inside
#: `asyncio.timeout` in this task's own context, and the holder is shared by
#: reference.
SESSION_TOKENS: contextvars.ContextVar[TokenSpend | None] = contextvars.ContextVar(
    "cognition_session_tokens", default=None,
)

#: The idempotency key `receipted_act` computed for the call it is running, read by the
#: create verbs so their idempotency receipt can commit inside the register
#: row's own transaction. A ContextVar, not state: same-turn tool calls run
#: concurrently and each must see its own key.
_ACT_KEY: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cognition_act_key", default=None,
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
    memory_items: dict[int, Any] = field(default_factory=dict)
    graph_fetched: Any = field(default_factory=GraphFetched)
    admitted_relations: dict[str, Any] = field(default_factory=dict)
    admitted_evidence: list[Any] = field(default_factory=list)
    #: account key -> the heads, handles, revision and disputes `profile_read`
    #: showed this session: the ONLY account state `profile_revise` may write
    #: against (a conflict drops the entry, so a stale view is never kept).
    profile_accounts: dict[str, Any] = field(default_factory=dict)
    #: Graph node keys this session was actually shown (the wake preview and
    #: every proposal's connected subgraph, plus admitted relation endpoints):
    #: the only targets a Register `associations` argument may name.
    shown_keys: set[Any] = field(default_factory=set)
    #: Relation ids this session was actually shown — the wake preview's
    #: emitted edge rows, a proposal's connected subgraph, and the edge rows
    #: a `memory_search`/`memory_read` result emitted: the only targets a
    #: correction may name (the `shown_keys` rule, one axis over).
    shown_relations: set[str] = field(default_factory=set)
    #: `(kind, name)` pairs a returned `possible_existing` refusal named this
    #: session: the only `{new: …}` endpoints `new.distinct` may confirm.
    possible_existing: set[tuple[str, str]] = field(default_factory=set)
    #: Person ids the session's emitted rows were authored by — the only
    #: `@person:` handles a statement may name (the `shown_keys` rule, one
    #: axis over). The agenda's rendered rows are read back at `propose`;
    #: a read tool's handed-out rows add theirs in `on_rows`.
    shown_persons: set[str] = field(default_factory=set)
    #: `object` handles this session was actually shown, to the room the
    #: grounder read them from — the only refs a note's `object_refs` may
    #: name (the `shown_keys` rule, one axis over).
    shown_handles: dict[str, tuple[str, str] | None] = field(default_factory=dict)
    document_evidence: list[tuple[int, str, str, str]] = field(default_factory=list)
    document_rooms: frozenset[tuple[str,str]] = frozenset()
    graph_text: str = ""
    #: The evidence this session was shown (`CognitionLessons.shown`): its
    #: `citable` is the only evidence `lesson_write` may cite; `take` writes
    #: its cursor. None when [lessons] is off, historical, or the read failed.
    lesson_view: LessonEvidenceView | None = None
    historical: bool = False
    #: The line of the prompt that says which room(s) the read tools resolve
    #: through (`_build_tools`).
    reads_note: str = ""
    unit_rooms: dict[str, tuple[str, str]] | None = None

    @property
    def allowed_rooms(self) -> dict[str, tuple[str, str]]:
        return self.rooms if self.unit_rooms is None else self.unit_rooms


_OWED_HEADING = re.compile(r"(?mi)^\s*#+\s*owed\b")



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



def _shown_rooms(grounding: Grounding, view: UnitView) -> dict[str, tuple[str, str] | None]:
    """The shown `object` handles, each to the room the grounder read it from."""
    shown: dict[str, tuple[str, str] | None] = {}

    def remember(handle: str, room: tuple[str, str]) -> None:
        if handle not in shown:
            shown[handle] = room
        elif shown[handle] != room:
            shown[handle] = None

    for ref, e in grounding.observations.items():
        stimulus = next((s for s in view.pending if s.ref == ref), None)
        if (stimulus is not None and stimulus.platform is not None
                and stimulus.conversation_id is not None):
            remember(e.handle, (stimulus.platform, stimulus.conversation_id))
    for key, events in grounding.source_objects.items():
        platform, _, cid = key.partition(":")
        for e in events:
            remember(e.handle, (platform, cid))
    for retained in grounding.retained_objects.values():
        for r in retained:
            if r.head is not None:
                remember(r.ref.handle, (r.ref.platform, r.ref.conversation_id))
    return shown


def _shown_custody(
    state: _RunState, prompt: str, grounding: Grounding, unit: str, memory: Any,
) -> None:
    """Citation custody follows the actual emitted elements, never loaded rows."""
    from xml.etree import ElementTree

    from nora.domain.blocks import render_group

    root = ElementTree.fromstring(  # noqa: S314 - own escaped output
        render_group("context", (), prompt)
    )
    emitted_objects = {element.attrib["object"] for element in root.iter()
                       if "object" in element.attrib}
    state.shown_handles = {key: room for key, room in state.shown_handles.items()
                           if key in emitted_objects}
    transcript_by_id = {line.row.id: line for line in grounding.transcript}
    for element in root.findall("./transcript/m"):
        mid = int(element.attrib["source_id"])
        line = transcript_by_id[mid]
        source_room = Room(line.platform, line.conversation_id)
        if source_room is None:
            continue
        text = element.text or ""
        if not line.row.is_nora and text == (line.row.body or ""):
            state.document_evidence.append(
                (mid, source_room.platform, source_room.conversation_id, text))
        state.fetched.take(
            "record_query",
            {
                "status": "ok",
                "messages": [{"n": mid, "sender": element.attrib.get("from", "unknown")}],
            },
            _room_key(source_room),
        )
        if memory is not None and not line.row.is_nora and text:
            state.graph_fetched.show(
                Source(
                    ("message", str(mid)),
                    "record",
                    unit,
                    (line.row.person_id,) if line.row.person_id else (),
                    text,
                )
            )
    complete_record_text = {
        r.message_id: r.text for r in grounding.records.values()
        if r.kind in {"message", "edit"}
    }
    for item in root.findall("./agenda/item"):
        index = int(item.attrib["index"])
        source_room = state.items_by_index[index].room
        for element in item.findall("m"):
            mid = int(element.attrib["n"])
            if (
                mid > 0
                and element.text
                and element.text == complete_record_text.get(mid)
                and not any(
                    element.attrib.get(flag) == "yes"
                    for flag in ("own", "deleted", "withdrawn")
                )
            ):
                state.document_evidence.append(
                    (mid, source_room.platform, source_room.conversation_id, element.text)
                )
    if memory is not None:
        # ONE evidence custody: the agenda's emitted rows are the only
        # sources a graph statement may cite (the loaded-but-clipped rest
        # was never shown).
        memory.register_shown(state)


class CognitionSessionRunner:
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

    def __init__(
        self,
        *,
        store: CognitionStore,
        stimulus_store: StimulusStore,
        agent_runner: CognitionAgentRunner,
        home_rooms: Mapping[str, Room],
        read_tools_for: ReadToolsFor,
        skill_text_for: SkillsFor,
        grounder: Grounder,
        doctrine_text: str,
        doctrine_sha256: str | None,
        open_note_titles: OpenNoteTitles,
        open_events: OpenEvents,
        prior_standing: PriorStanding,
        budget: AdaptorBudget,
        cooldown_seconds: float,
        units: Collection[str],
        session_timeout_seconds: float,
        max_items: int,
        max_self_wakes: int = 5,
        max_self_wakes_per_day: int = DEFAULT_MAX_SELF_WAKES_PER_DAY,
        read_only_tools: frozenset[str] = frozenset(),
        model_id: str | None = None, model_route: str | None = None,
        metrics: Any = None, mint_ref: Callable[[], str] = default_mint_ref,
        clock: Callable[[], float] = time.time,
        doctrine_source: DoctrineSource | None = None,
        skill_versions_for: SkillVersionsFor | None = None,
        skills_for: SkillsResolvedFor | None = None, tz: tzinfo = UTC,
        render_budget: RenderBudget | None = None, model_window_tokens: int | None = None,
        overflow_window_fraction: float = 0.8,
        person_known: Callable[[str], bool] | None = None, roster_of: RosterOf | None = None,
        nora_names: frozenset[str] = frozenset(), room_proactive: RoomProactive | None = None,
        max_pending_per_requester: int | None = None,
        remind_min_lead_seconds: int | None = None, remind_max_horizon_days: int | None = None,
        notes: Any = None, events: Any = None,
        standing: Any = None, reminders: Any = None,
        ai_peer: str = "nora", memos: Any = None, ledger: Any = None,
        # The shared entry store the topic-account verbs write.
        entries: Any = None, shared: Any = None, standing_fold: Any = None,
        content_checks: bool = True,
        tag_trace_store: Any = None, memory: Any = None,
        rooms_of_unit: Callable[[str], Sequence[tuple[str, str]]] | None = None,
        # The observation Record the write verbs check `object_refs`
        # against: a ref must still resolve in its shown room to be
        # retained. None refuses a ref write `unavailable`, never accepts
        # one it cannot check.
        observations: Any = None,
        documents: Any = None, documents_context: Any = None,
        # `CognitionLessons`; None ([lessons] disabled) offers no lesson verbs.
        lessons: Any = None,
    ) -> None:
        """The session runner's collaborators; see the class docstring."""
        self._documents = documents
        self._documents_context = documents_context
        self._lessons = lessons
        self._memory = memory
        self._content_checks = content_checks
        self._tag_trace_store = tag_trace_store
        self._observations = observations
        check_home_rooms(units, home_rooms)
        check_session_timeouts(budget.timeout_seconds, session_timeout_seconds)
        # Stored for `with_collaborators`: the two ctor arguments nothing else
        # keeps, so a variant runner can be built without re-asking them.
        self._units = units
        self._session_timeout_seconds = session_timeout_seconds
        self._store = store
        self._notes = notes
        self._memos, self._entries = memos, entries
        self._events = events
        self._standing = standing
        self._reminders = reminders
        self._ledger = ledger
        self._shared, self._standing_fold = shared, standing_fold
        self._rooms_of_unit = rooms_of_unit
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
        #: Outcome writes shielded from a second cancel (a `shutdown` and the
        #: Dreamer lane's `cancel_in_flight` both cancelling one task), held
        #: so they are not garbage-collected while they finish.
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

    def _counted(self, verb: str, call: Any, *, state: _RunState) -> Any:
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
            # Native verbs operate on this unit's canonical state. Keep any
            # more-specific requirements already recorded by their reads.
            from nora.agent.turn_recording import record_requirement
            from nora.domain.lineage import message
            from nora.domain.turn_record import DisclosureRequirement

            record_requirement(DisclosureRequirement(
                None, tuple(message(record_room_key(*room)) for room in state.rooms.values()),
            ))
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
    ) -> CognitionSessionOutcome | dict[str, Any] | None:
        """Called by the wake evaluator's hook on every wake it
        records (`WakeHook`). `inspect_only` returns the assembled model input
        before the model call; use it only in a disposable replay sandbox.
        Returns `None` on every ordinary path that makes no model
        call; RAISES, after recording the wake's outcome, on every path that
        did not close. Every path writes the wake's outcome (or, if that write
        fails, leaves the wake OPEN until it expires): nothing here returns
        with the wake's stimuli consumed and unseen."""
        session_id: int | None = None
        #: Offered to the model loop (`SESSION_TOKENS`) and read by EVERY path
        #: that records this wake's outcome, so a failed, timed-out or cancelled
        #: session's spend is as durable as a closed one's.
        spend = TokenSpend()
        attempted = {s.id for s in view.pending}
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
                    unit=unit, stimulus_id=sid, strike=0,
                    state="carried", session_id=None, waits=True)
                for sid in sorted(retained)
            ]
            situation = build_situation(
                unit=unit, pending=pending, home_room=home_room, now_ms=now_ms,
                last_session_ms=last_looked_ms, max_items=self._max_items)
            if situation.agenda.is_empty:
                self._inc("nora_cognition_session_skipped_total", reason="empty_agenda")
                if inspect_only:
                    return None  # an inspection persists nothing
                await self._finish(
                    wake_id, unit, None, "closed",
                    "empty agenda" if not skipped else
                    f"empty agenda: {len(skipped)} rollup stimulus(es) unreadable",
                    carries=retain_carries)
                return None

            attempted = {sid for item in situation.agenda.items for sid in item.stimulus_ids}
            doctrine_text, doctrine_sha256 = self._doctrine()
            # The skill is resolved ONCE, here, and its text carried to the prompt:
            # the grounding read below awaits, and a hot reload in that window
            # must not leave `skill_versions` naming a different text than the
            # one the model reads.
            skill_text, skill_versions = self._skills()
            # GROUNDED AFTER THE SKIP, BEFORE ANYTHING IS RECORDED: the agenda is
            # final, so the grounding's per-item
            # index (her own sends) is the index the render reads, and an item
            # promoted from `deferred` by a skip is grounded. A read that
            # fails raises here, so the wake is recorded `failed` and its
            # stimuli stay pending: a session never runs on bare counts it
            # cannot judge from.
            grounding = await self._grounder(
                unit, situation.agenda.items, pending, now_ms, historical)
            # BEFORE THE SESSION ROW: it persists `grounding.as_dict()`, so a
            # live session's lesson evidence must already be on it, and the
            # same view is reused for the prompt — never a second read.
            grounding, lesson_view = await lesson_read(self._lessons, unit=unit,
                grounding=grounding, historical=historical, inc=self._inc)
            if inspect_only:
                return await self._inspect(
                    unit, view, situation, now_ms, doctrine_text,
                    doctrine_sha256, skill_text, skill_versions, grounding,
                    lesson_view, historical)
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
                    # The lane the session ran in — the historical
                    # (backfill) lane's sessions are counted apart from the
                    # live one's for progress reporting.
                    "lane": "historical" if historical else "live"},
                doctrine_sha256=doctrine_sha256, skill_versions=skill_versions,
                config_revision_id=None, model_id=self._model_id,
                model_route=self._model_route, wake_id=wake_id)
            if eval_capture_enabled():
                record_eval_event("nora.session.instructions", {"body": json.dumps({
                    "doctrine_sha256": doctrine_sha256, "skill_versions": skill_versions})})
            session_id = await self._store.record_session(
                session=session, recorded_at_ms=now_ms)
            self._inc("nora_cognition_session_total", mode="live")
            return await self._run_session(
                unit=unit, view=view, situation=situation, session_id=session_id,
                wake_id=wake_id, now_ms=now_ms, doctrine_text=doctrine_text,
                doctrine_sha256=doctrine_sha256,
                skill_text=skill_text, grounding=grounding, retained=sorted(retained),
                spend=spend, replay=replay, historical=historical, lesson_view=lesson_view)
        except asyncio.CancelledError:
            # Only a process shutdown cancels a session (`shutdown`,
            # `WakeEvaluator.cancel_in_flight`; the wall clock raises
            # `SessionTimeoutError` instead), so this is a RESTART: recorded as
            # one, and `cancelled` neither backs the unit off nor strikes.
            await self._shielded_finish(
                wake_id, unit, session_id, "cancelled",
                restart_detail("the process was stopping and cancelled the session"))
            raise
        except SessionExpiredError:
            raise
        except SessionTimeoutError as exc:
            if self._memory is not None and not replay:
                await self._memory.failed(
                    unit, sorted(attempted), session_id)
            await self._finish(wake_id, unit, session_id, "timeout", str(exc))
            raise
        except Exception as exc:
            if self._memory is not None and not replay:
                await self._memory.failed(
                    unit, sorted(attempted), session_id)
            log.exception("cognition session failed (unit=%s wake=%s)", unit, wake_id)
            await self._finish(
                wake_id, unit, session_id, "failed", f"{type(exc).__name__}: {exc}"[:500],
                cause=classify_failure(exc))
            raise

    async def replay(self, *, view: UnitView, now_ms: int, historical: bool = False) -> Any:
        """Run the recorded batch through live assembly, using sandbox collaborators."""
        return await self.on_wake(unit=view.unit, view=view, now_ms=now_ms,
                                  wake_id=0, replay=True, historical=historical)

    _CTOR_RENAMES: ClassVar[dict[str, str]] = {
        "stimulus_store": "_stimuli",
        "model_window_tokens": "_window_tokens",
        "overflow_window_fraction": "_window_fraction",
    }

    @property
    def agent_runner(self) -> CognitionAgentRunner:
        return self._agent_runner

    @property
    def memory(self) -> Any:
        return self._memory

    @property
    def observations(self) -> Any:
        return self._observations

    @property
    def max_items(self) -> int:
        return self._max_items

    def with_collaborators(self, **changes: Any) -> CognitionSessionRunner:
        """A runner over THIS runner's collaborators with the named ones
        replaced. The historical (backfill) lane builds its runner this way
        from the live composition's: everything not named — stores, budgets,
        doctrine/skill sources, clocks — is shared, so the two runners cannot
        drift into different instructions or limits. The checks
        `__init__` makes re-run for the changed values."""
        spec: dict[str, Any] = {}
        params = inspect.signature(CognitionSessionRunner.__init__).parameters
        for name in params:
            if name == "self" or params[name].kind in (
                inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            spec[name] = getattr(self, self._CTOR_RENAMES.get(name, f"_{name}"))
        spec.update(changes)
        return CognitionSessionRunner(**spec)

    async def _inspect(
        self, unit: str, view: UnitView, situation: Situation, now_ms: int,
        doctrine_text: str, doctrine_sha256: str, skill_text: str,
        skill_versions: dict[str, object], grounding: Any,
        lesson_view: Any, historical: bool) -> dict[str, Any]:
        # BEFORE ANYTHING IS RECORDED: returns what a session would run on
        # and persists nothing; `work` carries each item's dedup key.
        state, _, system, agenda = await self._session_input(
            unit=unit, view=view, situation=situation, session_id=0, wake_id=0,
            now_ms=now_ms, doctrine_text=doctrine_text, skill_text=skill_text,
            grounding=grounding, historical=historical,
            doctrine_sha256=doctrine_sha256, lesson_view=lesson_view)
        return {"system": system, "agenda": agenda,
                "items": [i.as_dict() for i in situation.agenda.items],
                "stimuli": [{"id": s.id, "kind": s.kind, "ref": s.ref} for s in view.pending],
                "instructions": {"doctrine_sha256": doctrine_sha256,
                                 "skill_versions": skill_versions},
                **({"new_sources": rendered_new_sources(agenda)}
                   if eval_capture_enabled() else {}),
                "work": [{"index": index, "work_id": w.work.id,
                          "input_hash": w.work.input_hash,
                          "inputs": w.snapshot.get("stimulus_inputs", {})}
                         for index, w in sorted(state.memory_items.items())]}

    async def inspect(
        self, *, view: UnitView, now_ms: int, historical: bool = False,
    ) -> dict[str, Any] | None:
        """The model input a session over `view` would be run on, through the same
        assembly, stopping before the model call. For a disposable replay sandbox
        and for the unified backfill's per-item dedup check (the `work` key it
        also returns — `(work_id, input_hash)` per agenda item)."""
        result = await self.on_wake(unit=view.unit, view=view, now_ms=now_ms,
                                    wake_id=0, replay=True, inspect_only=True,
                                    historical=historical)
        return result if isinstance(result, dict) else None

    async def probe(
        self, *, unit: str, summary: str, now_ms: int, room: Room | None = None,
    ) -> CognitionRunResult:
        """One session over ONE hand-written agenda item: the seam the
        `tool_selection_cognition` corpus drives (`evals.rebuilt.entry`), whose
        cases are agenda-item sentences and not stimuli. It runs the SAME
        assembly a wake's session runs (`_session_input`) — the same
        instruction, the same tools, the same render — over an empty view and
        grounding, so the model faces exactly the contract a real session
        poses; and it stops there: no wake, so no outcome, no carry and no
        fired self-wake; the session row and its writes go to the stores it
        was handed (a snapshot's or a sandbox's database)."""
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
        skill_text, skill_versions = self._skills()
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
            recorded_at_ms=now_ms)
        # One hand-written item is the whole input — so the assembly is the
        # wake's own `_session_input`, not a parallel partial copy of it: the
        # SAME instruction (objective and tool-gated guidance), the SAME
        # tools built the same way, the SAME `render_agenda` call — over a
        # view and grounding that are honestly EMPTY (no stimuli, no observed
        # objects, nothing fetched yet), because a probe item is a sentence,
        # not a stimulus. Nothing is invented to fill them: an empty Grounding
        # renders exactly what a wake with no readable evidence renders, which
        # is what "synthetic" means here — it is a real session's shape over
        # one agenda line, never a chronological journey's.
        view = UnitView(
            unit=unit, pending=(), last_activity_ms=None, last_wake=None)
        _state, tools, system_prompt, prompt = await self._session_input(
            unit=unit, view=view, situation=situation, session_id=session_id,
            wake_id=0, now_ms=now_ms, doctrine_text=doctrine_text,
            skill_text=skill_text, grounding=Grounding(),
            doctrine_sha256=doctrine_sha256,
        )
        return await self._agent_runner.run(
            system_prompt=system_prompt, prompt=prompt,
            tools=tools, budget=self._budget,
            trace_attributes={"cognition.unit": unit, "cognition.session_id": str(session_id)},
        )

    def _doctrine(self) -> tuple[str, str | None]:
        if self._doctrine_source is not None:
            return self._doctrine_source()
        return self._doctrine_text, self._doctrine_sha256

    def _skills(self) -> tuple[str, dict[str, object]]:
        """The skill text a session injects and the versions its row records."""
        if self._skills_for is not None:
            text, versions = self._skills_for()
            return text, dict(versions)
        return self._skill_text_for(), (
            {} if self._skill_versions_for is None else dict(self._skill_versions_for())
        )

    async def shutdown(self) -> None:
        """Wait for the finishing outcome writes to land. The tasks
        themselves are not this runner's to cancel: a live session's task is
        admitted on the Dreamer lane (`SubconsciousExecutor` →
        `DreamerRuntime.dispatch`), and `CognitionShutdown` cancels it there
        before calling this — the boundary that created the task cancels it
        (node 18). What remains here is the settlement half: a session must
        not vanish with its wake still reading «in flight» when it could
        have said `cancelled`."""
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
        self,
        *,
        unit: str,
        view: UnitView,
        situation: Situation,
        session_id: int,
        wake_id: int,
        now_ms: int,
        doctrine_text: str,
        skill_text: str,
        grounding: Grounding,
        historical: bool = False,
        doctrine_sha256: str | None = None, lesson_view: Any = _UNREAD,
    ) -> tuple[_RunState, Any, str, str]:
        """Everything a session assembles before the model call: the run
        state, the tools, the system prompt and the agenda text. A live
        session and an inspection share exactly this and diverge after it:
        one runs the model and records outcomes, the other returns what it
        built and persists nothing."""
        items = situation.agenda.items
        state = _RunState(
            session_id=session_id,
            wake_id=wake_id,
            document_rooms=grounding.document_rooms, unit=unit, now_ms=now_ms,
            items_by_index=dict(enumerate(items)), historical=historical,
            rooms={_room_key(i.room): (i.room.platform, i.room.conversation_id) for i in items},
            shown_handles=_shown_rooms(grounding, view),
            # Her own sends in an item's window are SHOWN too (DEC-245), marked
            # as hers, so a note citing one is refused `cites_nora`, not `unknown_id`.
            records=(
                *grounding.records.values(),
                *(r for sends in grounding.own_sends.values() for r in sends),
            ),
        )
        if self._memory is not None:
            await self._memory.prepare(state, {s.id: s for s in view.pending}, grounding)
        if lesson_view is _UNREAD:
            grounding, lesson_view = await lesson_read(self._lessons,
                unit=unit, grounding=grounding, historical=historical,
                inc=self._inc)
        state.lesson_view = lesson_view
        tools = await self._build_tools(
            unit=unit, state=state, now_ms=now_ms, labels=grounding.room_labels,
            doctrine_sha256=doctrine_sha256,
        )
        from nora.agent.surface import deferred_surface

        # The whole offered surface is resident (29 §2's read tools «any
        # time»): nothing sits on a shelf behind `load_tools`, so the read
        # catalogue is callable on the first step and no name cap applies.
        # A tool the registry offers but cannot wire answers `unavailable`,
        # never silently absent. Historical sessions withhold act verbs.
        surface = deferred_surface(
            tools, resident={t.tool_name if hasattr(t, "tool_name") else t.__name__
                             for t in tools})
        from nora.language.comprehend_render import render_session_system

        system_prompt = render_session_system(
            doctrine_text, skill_text, tool_names=surface.admitted, historical=historical)
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
            items,
            stimuli={s.id: s for s in view.pending},
            grounding=grounding,
            reads_note=state.reads_note,
            now_ms=now_ms,
            last_session_ms=situation.last_session_ms, tz=self._tz,
            budget=self._render_budget,
            graph=state.graph_text if self._memory is not None else None,
            memory_items=state.memory_items,
            shared=shared_view,
        )
        _shown_custody(state, prompt, grounding, unit, self._memory)
        return state, surface, system_prompt, prompt

    async def _run_session(
        self, *, unit: str, view: UnitView, situation: Situation, session_id: int,
        wake_id: int, now_ms: int, doctrine_text: str, skill_text: str,
        grounding: Grounding, spend: TokenSpend, retained: Sequence[int] = (),
        replay: bool = False, historical: bool = False,
        doctrine_sha256: str | None = None, lesson_view: Any = _UNREAD,
    ) -> CognitionSessionOutcome:
        items = situation.agenda.items
        kinds = sorted({item.kind for item in items})
        state, tools, system_prompt, prompt = await self._session_input(
            unit=unit, view=view, situation=situation, session_id=session_id,
            wake_id=wake_id, now_ms=now_ms, doctrine_text=doctrine_text,
            skill_text=skill_text, grounding=grounding, historical=historical,
            doctrine_sha256=doctrine_sha256, lesson_view=lesson_view)
        from dataclasses import asdict

        if eval_capture_enabled():
            record_eval_event("nora.session.started", {"body": json.dumps({
                "session_id": session_id, "wake_id": wake_id, "clock_ms": now_ms,
                "timezone": str(self._tz), "historical": historical,
                "budgets": asdict(replace(self._budget, reserve_tokens=historical)),
                "system": system_prompt, "agenda": prompt,
                "items": [i.as_dict() for i in items],
                "stimuli": [{"id": s.id, "kind": s.kind, "ref": s.ref} for s in view.pending],
            }, default=str)})
        token = SESSION_TOKENS.set(spend)
        result: CognitionRunResult | None = None
        trace_outcome = "llm_error"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._budget.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                result = await self._run_agent(
                    unit=unit, pending_ids=frozenset(s.id for s in view.pending),
                    system_prompt=system_prompt, prompt=prompt, tools=tools,
                    historical=historical,
                    trace_attributes={
                        "cognition.unit": unit, "cognition.session_id": str(session_id),
                        # The agenda's size, for the trace (no call ceiling
                        # scales by it: DEC-247).
                        "cognition.items": str(len(items)),
                    },
                )
            trace_outcome = "llm_error" if result.stop_reason in LIMIT_STOP_REASONS else "silent"
        except TimeoutError as exc:
            # `asyncio.timeout` raises TimeoutError only after its own
            # deadline callback fired; an external task.cancel() propagates
            # as CancelledError, so TimeoutError here IS the wall clock.
            raise SessionTimeoutError(
                f"the session ran past its {self._budget.timeout_seconds:g}s wall clock",
            ) from exc
        finally:
            if eval_capture_enabled():
                record_eval_event("nora.session.terminal", {"body": json.dumps({
                    "session_id": session_id, "wake_id": wake_id,
                    "outcome": result.stop_reason if result is not None else "ERROR",
                    "error_type": type(sys.exception()).__name__ if sys.exception() else None,
                    "bound": ("timeout_seconds" if isinstance(sys.exception(), SessionTimeoutError)
                              else result.stop_reason if result is not None
                              and result.stop_reason in LIMIT_STOP_REASONS else None),
                    "saved": state.admitted_evidence,
                }, default=str)})
            SESSION_TOKENS.reset(token)
            from nora.domain.tag_trace import TagTrace
            trace = TagTrace(
                tag_id=f"cognition:{session_id}", conversation_id="",
                messages=result.messages if result is not None else spend.messages,
                requirements=result.requirements if result is not None else spend.requirements,
                transcript=(), outcome=("timeout" if isinstance(
                    sys.exception(), SessionTimeoutError) else trace_outcome),
                created_at=int(self._clock() * 1000), started_at=now_ms,
                occasion="cognition", unit=unit, bound_rooms=tuple(sorted(state.rooms)),
            )
            if self._tag_trace_store is not None:
                save_deadline = loop.time() + 5.0
                # TimeoutError here is the save's own deadline firing, same
                # classification as the session clock above.
                async with asyncio.timeout_at(save_deadline):
                    await self._tag_trace_store.save(trace)
            # The SAME automatic completion hook the tag path holds (design
            # 20): the tools-less Standing fold runs on this session's
            # complete retained Record — including a silent or failed
            # session whose Record exists — once per room the session
            # actually bound. Each write lands under the room's own real
            # scope key; nothing is stored under a fabricated unit room.
            # Replay and historical (backfill) sessions are skipped: an old
            # session must not fold stale state over a live position.
            if (
                self._standing_fold is not None and trace.messages
                and not replay and not historical
            ):
                # ONE unit-scoped materialization: Cognition's cross-room
                # state writes unit-scope ledger entries, not a room
                # Standing per bound room — the whole retained Record goes
                # in once, never copied room-by-room.
                await self._standing_fold(
                    trace=trace, unit=unit, rooms=tuple(sorted(state.rooms.values())),
                )
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

        if result.stop_reason != "end_turn":
            if result.stop_reason in LIMIT_STOP_REASONS:
                self._inc("nora_cognition_session_limit_total", limit=result.stop_reason)
            raise SessionLimitError(result.stop_reason or "unknown")

        if replay:
            if self._memory is not None:
                await self._memory.completed(state)
            return CognitionSessionOutcome(session_id=session_id, items=len(items),
                                           stop_reason=result.stop_reason)
        # Only unseen inputs remain pending after a normal end_turn. Carry
        # changes commit atomically with the wake outcome and its watermark.
        deferred = set(situation.agenda.deferred_stimulus_ids)
        carries = [NewCarry(
            unit=unit, stimulus_id=sid, strike=0, state="carried", session_id=session_id,
        ) for sid in sorted(deferred)]
        carries.extend(NewCarry(
            unit=unit, stimulus_id=sid, strike=0, state="released", session_id=session_id,
        ) for item in items for sid in item.stimulus_ids if sid in view.carry)
        # A Rollup stimulus the roster cannot read YET is retained, not consumed
        # (see `on_wake`): it waits, and returns as an item once it is readable.
        carries.extend(
            NewCarry(
                unit=unit, stimulus_id=sid, strike=0,
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
        await lesson_settle(
            self._lessons, unit=unit, state=state, session_id=session_id,
            historical=historical, now_ms=self._now_ms(), inc=self._inc)
        await self._record_fired(state=state, situation=situation, view=view)
        return CognitionSessionOutcome(
            session_id=session_id, items=len(items), stop_reason=result.stop_reason,
        )

    async def _run_agent(
        self, *, unit: str, pending_ids: frozenset[int], system_prompt: str, prompt: str,
        tools: list[Any], trace_attributes: dict[str, str], historical: bool = False,
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
                budget=replace(self._budget, reserve_tokens=historical),
                trace_attributes=trace_attributes,
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

    # -- tools --------------------------------------------------------------

    def _guarded_read_tools(
        self, unit: str, rooms: Sequence[Room],
        *, clock: HistoricalClock | None = None,
        on_rows: Callable[..., None] | None = None,
    ) -> list[Any]:
        """`read_tools_for`'s result, refused unless every tool is on the
        read-only allowlist and none shadows a session verb. The session's
        ceiling is «may look, may not write»; a read-tool source that ever
        returned a write is stopped here, not trusted."""
        # `clock` and `on_rows` are an OPTIONAL protocol: a source declares
        # them (the registry's does) or it does not, and one that does not is
        # never handed them. Value and signature are both checked, so a
        # narrower source — a test double, a tag-path closure — still builds.
        try:
            params: Mapping[str, inspect.Parameter] = inspect.signature(
                self._read_tools_for).parameters
        except (TypeError, ValueError):
            params = {}
        accepts_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        extra = {
            **({"clock": clock}
               if clock is not None and (accepts_kw or "clock" in params) else {}),
            **({"on_rows": on_rows}
               if on_rows is not None and (accepts_kw or "on_rows" in params) else {}),
        }
        tools = self._read_tools_for(unit, rooms, **extra)
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
        self,
        *,
        unit: str, state: _RunState,
        now_ms: int,
        labels: Mapping[str, str] | None = None, doctrine_sha256: str | None = None,
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
        # each takes an `agenda_room` argument routing the read through that
        # room's own build; an `agenda_room` value not on the agenda is refused.
        # The unit is the
        # boundary the session may read across (DEC-231: `record_query
        # scope="all_rooms"` and `search_memory_workspace` read the unit's other
        # rooms), the `agenda_room` argument picks the binding (roster, lineage)
        # a read resolves through. The agenda's rooms are never replaced by
        # the unit's home room.
        rooms = list(dict.fromkeys(item.room for item in state.items_by_index.values()))
        keys = [_room_key(r) for r in rooms]
        # The prompt calls a room by its LABEL; the id is what a tool's
        # `room` argument takes, so it appears here, once, beside the label it
        # belongs to, and nowhere in an item.
        named = [room_label(r, labels or {}) for r in rooms]
        state.reads_note = (
            f"Your read tools look through room {named[0]}."
            if len(keys) == 1
            else "Your read tools take an `agenda_room`, one of: "
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
                               for source in v.sources for p in source.persons}),
                shown_keys=state.shown_keys,
                shown_relations=state.shown_relations)

            def on_rows(rows: Sequence[Any]) -> None:
                """A transcript read's handed-out rows, kept as the citable
                `("message", id)` evidence `memory_propose` admits on: what
                the tool showed is the bounded, as-of-projected row — a row
                the session was never shown cannot become evidence, and a
                row whose body the read withheld (deleted, redacted) carries
                no text to cite, so it registers nothing either."""
                for r in rows:
                    if r.body is None:
                        continue
                    if r.person_id:
                        state.shown_persons.add(r.person_id)
                    state.graph_fetched.show(Source(
                        ("message", str(r.id)), "record", unit,
                        (r.person_id,) if r.person_id else (),
                        r.body))

        # A historical session reads as of its evidence instant: every built
        # tool's `now_fn` is bound to it (the registry's injected clock, the
        # same seam `with_memory_search` uses), so an end-less or before-less
        # read lands at the instant the same way it lands at «now» live —
        # and the reads themselves fold rows, edits, reactions, deletes and
        # source events AT that instant, never at the wall clock's.
        historical_clock: HistoricalClock | None = None
        if state.historical:
            historical_clock = _as_clock(datetime.fromtimestamp(now_ms / 1000, UTC))
        # The reads that hand out citable rows are watched: what they return is
        # what a note may cite besides the agenda's own rows.
        read_tools = watch_reads(
            self._guarded_read_tools(
                unit, rooms, clock=historical_clock,
                on_rows=on_rows if self._memory is not None else None),
            state.fetched, keys)

        if self._memory is not None:
            read_tools = [ComprehendRead(t, lookups) if hasattr(t, "stream") else t
                          for t in read_tools]

        if historical_clock is not None:
            from nora.agent.comprehend_tools import as_of_reads
            read_tools = as_of_reads(read_tools)

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
                rendered=tuple(rendered.values()), carried=frozenset(fetched.carried_ids),
                roster=roster, nora_names=self._nora_names,
                rollup_keys=frozenset(fetched.rollup_keys),
                turn_handles=frozenset(fetched.turn_handles),
            )

        def wrote(
            platform: str, conversation_id: str, item: int | None, **ok: object,
        ) -> dict[str, object]:
            """The accepted write result."""
            return {"status": "ok", **ok}

        def _associations(raw: Any) -> list[tuple[str, str]] | dict[str, object]:
            """The existing matters a Register write names, held to the graph
            this session was actually shown: each entry is `[kind, id]` — a
            node key as the preview and `memory_propose`'s `connected` render
            it. An unseen key is refused, never silently written: the unit is
            the read boundary, so anything shown was in scope."""
            targets: list[tuple[str, str]] = []
            for pair in raw or ():
                if (
                    not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or not all(isinstance(x, str) for x in pair)
                ):
                    return _refused(
                        "bad_association",
                        "each association is [kind, id], a graph node key you were shown",
                    )
                key = (pair[0], pair[1])
                if key not in state.shown_keys:
                    return _refused(
                        "unseen_association",
                        f"{pair[0]}:{pair[1]} is not in the graph this session was shown; "
                        "admit the association in memory_propose or read it first",
                    )
                if key[0] not in (*ENTITY_KINDS, *ENTRY):
                    return _refused(
                        "bad_association",
                        f"an entry cannot link to a {pair[0]!r} node",
                    )
                targets.append(key)
            return targets

        async def _object_refs(raw: Any) -> tuple[SourceRef, ...] | dict[str, object]:
            """Resolve only shown object handles; source room ids stay server-side."""
            if not isinstance(raw, list):
                return _refused("bad_object_refs", "object_refs is a list of object handles")
            if not raw:
                return ()
            if self._observations is None:
                return {"status": "unavailable", "reason": "no_observation_store",
                        "detail": "object_refs retention is not wired in this session"}
            if len(raw) > NOTE_OBJECT_REFS_MAX:
                return _refused(
                    "too_many_object_refs",
                    f"at most {NOTE_OBJECT_REFS_MAX} object_refs per note")
            out: list[SourceRef] = []
            seen: set[str] = set()
            for handle in raw:
                if not isinstance(handle, str) or not parse_handle(handle)[0]:
                    return _refused("bad_object_refs", "each object_ref is an object handle")
                if handle in seen:
                    return _refused("duplicate_object_ref", "an object handle is listed twice")
                seen.add(handle)
                shown_room = state.shown_handles.get(handle)
                if handle in state.shown_handles and shown_room is None:
                    return _refused(
                        "ambiguous_object_ref", "that handle names objects in multiple shown rooms")
                if shown_room is None:
                    return _refused(
                        "unseen_object_ref", "that object was not shown in this session")
                platform, cid = shown_room
                try:
                    head = await self._observations.head_for_handle(
                        scope=SourceScope(platform=platform, account_key="",
                                          conversation_id=cid), handle=handle)
                except Exception:
                    log.exception("retained object validation read failed")
                    return {"status": "unavailable", "reason": "object_ref_unreadable",
                            "detail": "the source object could not be checked"}
                if head is None:
                    return {"status": "unavailable", "reason": "object_ref_unresolved",
                            "detail": "the source object no longer resolves in its shown room"}
                out.append(SourceRef(
                    platform=platform, conversation_id=cid,
                    handle=handle, thread=head.thread))
            return tuple(out)

        established_by = f"{COGNITION_PATH_PREFIX}{unit}"


        def _act_receipt(payload_for: Callable[[Any], dict[str, object]]) -> Any:
            """The idempotency receipt bound into a create verb's own
            transaction (`receipt=` on the register store): the row and its
            receipt commit or roll back together, so a crash between them can
            no longer lose the key and let a retried call write a second row.
            None when this call is not a receipted create."""
            key = _ACT_KEY.get()
            if key is None or self._memory is None:
                return None

            async def write(conn: Any, row: Any) -> None:
                await self._memory.records.act_result_on(
                    conn, key, state.session_id, payload_for(row))

            return write

        # The write verbs are built by their OWNING areas and reached through
        # the package roots — the seam `nora.documents`' session tools use.
        # `self` carries the runner's collaborators; the helpers above carry
        # this call's session state.
        import importlib

        deps = SessionDeps(
            runner=self, unit=unit, state=state,
            established_by=established_by, shown_for=shown_for, wrote=wrote,
            act_receipt=_act_receipt, associations=_associations,
            object_refs=_object_refs, doctrine_sha256=doctrine_sha256,
        )
        notes_v, memos_v, topics_v, standing_v, memory_v = importlib.import_module(
            "nora.memory").wire.session_verbs(deps)
        events_v, wakes_v, reminds_v = importlib.import_module(
            "nora.agenda").wire.session_verbs(deps)

        receipted = linked = (
            "note_create", "memo_create", "event_propose",
            "note_revise", "memo_revise", "event_revise",
        )
        #: The revise verbs' row id lives in their ARGUMENTS, not their result.
        row_arg = {"note_revise": "note_id", "memo_revise": "memo_id",
                   "event_revise": "event_id"}

        def _assoc_targets(bound: Mapping[str, Any]) -> Any:
            """The named associations, or None when `associations` was left
            out — a revise that does not name them leaves the row's links
            untouched; a create always projects its own links."""
            raw = bound.get("associations")
            if raw is None:
                return None
            return [
                (a[0], a[1])
                for a in raw
                if isinstance(a, (list, tuple)) and len(a) == 2
            ]

        def receipted_act(verb: Any) -> Any:
            """Keep idempotency and graph links on successful notebook writes."""
            @functools.wraps(verb)
            async def call(*args: Any, **kwargs: Any) -> dict[str, object]:
                bound = inspect.signature(verb).bind(*args, **kwargs).arguments
                key = None
                if self._memory is not None and verb.__name__ in receipted:
                    key = await self._memory.act_key(state, verb.__name__, bound)
                    existing = await self._memory.records.act_result(key, state.session_id)
                    if existing is not None:
                        targets = _assoc_targets(bound)
                        if verb.__name__ == "event_propose":
                            merged = {**existing, "event_id": existing["id"]}
                        elif verb.__name__ in row_arg:
                            merged = {**existing,
                                      row_arg[verb.__name__]: bound[row_arg[verb.__name__]]}
                        else:
                            merged = existing
                        if targets is not None or verb.__name__ not in row_arg:
                            await self._memory.link_result(
                                state, merged, targets=targets)
                        return dict(existing)
                token = _ACT_KEY.set(key)
                try:
                    result: dict[str, object] = await verb(*args, **kwargs)
                finally:
                    _ACT_KEY.reset(token)
                if (self._memory is not None and verb.__name__ in linked
                        and result.get("status") == "ok"):
                    if verb.__name__ in row_arg:
                        result = {**result,
                                  row_arg[verb.__name__]: bound[row_arg[verb.__name__]]}
                    elif verb.__name__ == "event_propose":
                        result = {**result, "event_id": result["id"]}
                    targets = _assoc_targets(bound)
                    if targets is not None or verb.__name__ not in row_arg:
                        await self._memory.link_result(
                            state, result, targets=targets)
                return result
            return call


        verbs = tuple(receipted_act(v) for v in (
            *notes_v, *memos_v, *topics_v, *events_v, *standing_v, *wakes_v, *reminds_v,
        ))
        if self._memory is not None:
            verbs = (*memory_v, *verbs)
        if state.historical:
            # Profile and topic verbs are withheld, not shadowed: the stores
            # see only CURRENT heads (no as-of view), so a session at T would
            # read — or revise — the future. History offers memory writes.
            verbs = memory_v[:1]
        verbs = (*verbs, *lesson_verbs(
            self._lessons, unit=unit, state=state, established_by=established_by,
            doctrine_sha256=doctrine_sha256, model=self._model_id))
        # `wire` is reached through the area root (`nora.documents` may be the
        # only importer of `nora.documents.wire`): the root's lazy attribute
        # resolves it, exactly as `build_tool_specs` reaches every area.
        from nora.agent.ports import adapt
        from nora.domain.documents import DocumentAuthority
        document_specs = importlib.import_module("nora.documents").wire.session_tool_specs(
            self._documents,
            self._documents_context,
            lambda: DocumentAuthority(
                unit, state.document_rooms, state.session_id,
                tuple(state.document_evidence), as_of_ms=state.now_ms, read_only=state.historical,
            ),
        )
        return [
            *read_tools,
            *(adapt(spec) for spec in document_specs
              if self._documents is not None and self._documents_context is not None
              and (not state.historical or spec.name != "document_revise")),
            *(self._counted(v.__name__, v, state=state) for v in verbs),
        ]

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


