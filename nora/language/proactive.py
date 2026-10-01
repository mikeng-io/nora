"""The proactive adaptor — Nora's other half, the DECIDE turn of one OODA loop
(design/dreamer-agent-loop/39, DEC-162).

WHAT THIS IS
------------
One `DreamAdaptor`, unit-keyed, run once a day on the Dreamer's own
clock. It reads a unit's whole ORIENT state — the open notes, each
room's Standing with its age, the log of what it decided before and how
that went, and its own self card — and decides whether anything is worth
raising, and WHEN. Its only act is `remind`: under the `dream_adaptor`
provenance the tool stamps a `kind="proactive"` reminder row owned by the
reserved `ai_peer` id and bound to the unit's home room (39 D3/D5), and
the heartbeat's fire path — the ACT turn — speaks at the decided time, or
declines (39 D4/D12). The decision IS the tool call. The envelope this
adaptor parses is bookkeeping: which notes it considered, what it scheduled
or declined, and why; its sink writes `record_wake` on each note it raised
(39 D6) and nothing else. The envelope is bookkeeping and bookkeeping can be
lost, so the sink reads the rows back and charges any raise the envelope did
not report — see `commit`.

WHAT IT MAY NOT DO
------------------
It composes no message: `send_reply` is not in its tools and the Dreamer has
no deliverer. It writes no note content: `note_create`/`note_update` are
not in its tools (39 D6 — the one structural brake on the self-refine loop
is that nothing Nora says can create, revise or close a note). It reads no
transcript: `load_transcript` is deliberately absent (39 §1 — the verbatim
window is where her own echo would be loudest; Standing already carries the
room). Every other read verb it holds is bound to the HOME ROOM the
composition root chose from `policy.toml`, through the tool source.

WHAT `inputs` RENDERS, AND WHEN IT RENDERS NOTHING
--------------------------------------------------
The index, not the depth (39 D9): note titles, anchors, revisit dates and
strikes — `note_read` is the call for a body; Standing per room WITH its
`updated_at`, because Standing is written only after tag turns (39 D8) and a
quiet room's paragraph is days old; the decision log — her own rows over the
last N days, with outcomes, joined to the unit through policy's room
map (39 D7); the `<self_card>` block, framed as DEC-96 frames it; and
`now`. A unit with NO home room renders nothing (the operator has not
said where she stands, so she does not), and so does a unit with no
open notes and no Standing — the runtime's fire condition, the curator's
precedent, and what keeps a silent unit from costing a research loop a
day.

WHEN A PASS RUNS AT ALL — THE DETERMINISTIC CHECK (issue #136, revising 39 D10)
------------------------------------------------------------------------------
The adaptor ticks hourly (`AdaptorSchedule`), and every tick is a store read
and nothing else unless one of two things is true: the DAILY FLOOR — no pass
has rendered yet on this Hong Kong calendar day and it is past
`daily_hour` — or a CHANGE since the last rendered pass: a note of the
unit was created, revised on the tag path, or crossed its `revisit_at`,
or a room's Standing moved — AND the unit's HOME ROOM has been quiet
for `quiet_seconds` (the room a proactive row fires into, and so the only
one this pass can interrupt; «every room of the unit» until
2026-09-10 — see `_quiet`). Anything else renders `""`, and `DreamerRuntime.run`
makes no model call on a blank prompt. That is the same shape the note
tier already has (a watermark that fires on lag, a curator that renders
nothing on a tidy notebook), so a unit where nothing moved costs one
SQL read an hour and zero tokens, and a note born at 10:00 about tonight
is seen at the next quiet hour rather than tomorrow morning.

«Last rendered pass» is DURABLE — `dream_schedule_state`, under the name
`proactive:pass`, through the `DreamScheduleClock` port the runtime's own
slots use. A process-local dict is the exact defect that table was built to
remove (`nora/app/runtime.py`): a restart would render an extra pass every
deploy. With no clock wired (`clock_store=None`, the contract fake and the
eval drive) every tick renders and the pass is counted `unclocked`, so a
wiring gap is a series that climbs rather than a silence.

The WAKE (39 D6) is charged at most once per note per Hong Kong day, in the
same table under `proactive:wake`, so several passes a day cannot strike a
note to `MAX_DATELESS_STRIKES` in two days.

Ordinal HANDLES, never ids, for the curator's reason: the Dreamer's screen
drops any envelope item carrying a uuid, so an adaptor that asked the model
about ids would have every decision silently rejected. The handle -> id map
lives per key on this instance between `inputs` and `commit`, as the
curator's `_shown` does.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, get_args
from zoneinfo import ZoneInfo

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    RowStyle,
    assemble,
    render_bullets,
    render_rows,
    restamp,
)
from nora.domain.decision_log import (
    PROACTIVE_DECISION_WRITERS,
    DecisionLogRecorder,
    render_decisions,
)
from nora.domain.models import TranscriptRow
from nora.domain.notes import (
    Note,
    NoteRevision,
    WakeOutcome,
    default_revisit_at,
    is_tag_path_revision,
)
from nora.domain.ports import (
    DEFAULT_MEMORY_READ_TIMEOUT_SECONDS,
    CultureReader,
    Proposal,
    Recollection,
)
from nora.domain.prompt import escape, one_line
from nora.domain.reminders import Reminder
from nora.domain.self_presence import Presence
from nora.domain.standing import Standing
from nora.language.culture_render import read_culture, render_culture
from nora.language.note_render import render_note_rows, standing_only
from nora.language.presence_render import render_presence
from nora.language.self_card_render import render_self_card
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.language.proactive")

#: The adaptor's `name`: the metric label, the schedule and budget key, the
#: `AdaptorGrant` key, and the `dream_adaptor` provenance `remind` reads.
PROACTIVE_ADAPTOR_NAME = "proactive"

#: `nora_errors_total{stage=...}` for a roster-index recall that failed or came
#: back unreachable (issue #210). Its own stage beside
#: `proactive_self_card` for that label's reason: an operator asking which half
#: of the pass's memory went blind — her own card, or her model of the room —
#: must not have to guess. Declared in `nora/obs/metric_specs.py`'s
#: `nora_errors_total` description; `tests/unit/test_metric_specs_stages.py`
#: keeps the two from drifting.
STAGE_PROACTIVE_ROSTER_INDEX = "proactive_roster_index"

#: The tools the decide turn holds (39 D3) — every read verb through the home
#: room plus the one act verb. Not `send_reply`, not `send_to`, not
#: `note_create`/`note_update`, not `load_transcript`.
PROACTIVE_TOOLS: frozenset[str] = frozenset({
    "remind",
    "where_is", "saved_places", "route", "group_members",
    # `lookup_person` GRANTED 2026-09-10 (issue #145). Without it the adaptor
    # held no verb that answers «who is this handle?», and `group_members`
    # returns real names with no aliases beside them — so a note written in the
    # group's own nicknames had nothing to resolve against. Measured on the
    # `friends` unit (Langfuse d63f40f2, 2026-09-09): ten tool calls, six
    # of them `search_memory_workspace`, five asking the same question — who
    # `@熱咖啡☕少奶多糖`, `@隨風而去`, `@山田花子.하나` and `@國婷永琪` are — until
    # R-7 stopped it. The searches returned only more occurrences of the same
    # handles; the mapping exists in memory as scattered chat lines, which is
    # depth, and the turn needed an index.
    "lookup_person",
    "about", "ask_memory", "search_memory_workspace",
    "note_read", "note_search",
    "web_search", "web_fetch", "weather", "now",
    # `online_now` GRANTED 2026-09-15 (design/online-status/04, issue #248):
    # the live-state cross-check the decide turn had no verb for — a note
    # saying "Blake sick, wants the night off" was spoken into the home room
    # while the group was mid-game, because nothing in the turn could observe
    # the state contradicting the note. READ-ONLY, so the ceiling admits it;
    # its input domain is the home roster unioned with the note subjects,
    # supplied per build by `RegistryToolSource` — NOT in
    # `UNIT_SAFE_TOOLS` (room-roster-scoped, same rule as `where_been`).
    "online_now",
})

#: The two `dream_schedule_state` names this adaptor keeps beside the
#: runtime's own slot row: when a pass last RENDERED for a unit, and when
#: a note was last woken by it (key `<unit>/<note_id>`).
PASS_CLOCK_NAME = "proactive:pass"  # noqa: S105 — a table row name, not a secret
WAKE_CLOCK_NAME = "proactive:wake"

#: How many awaited collaborator CALL SITES `inputs` and `commit` hold between
#: them — the number `DreamerRuntime` multiplies by `[dreamer]
#: store_timeout_seconds` to bound each of them (issue #60). It lives HERE, in
#: the module that makes the calls, on `STANDING_STORE_CALLS_PER_FOLD`'s own
#: argument: a bound derived from a number kept somewhere else drifts in
#: silence, because a hold that is too wide looks exactly like a healthy one
#: until something wedges.
#:
#: This is the WIDEST of the three shipped adaptors, and the reason is its
#: render: `inputs` reads the note index, each note's last revision, the home
#: room's quiet window, the standing position (twice — once for the block,
#: once per changed note), her reminder log, the SHARED DECISION LOG (one
#: `read` site, gathered across the unit's rooms), the self card, and both
#: `dream_schedule_state` clocks; `commit` records the pass fire, one decision
#: -log `record` per envelope item, then per woken note a read, a wake and a
#: second fire.
#: `tests/architecture/test_dream_store_calls_are_declared.py` counts them off
#: this module's AST and reddens when a call is added without this number
#: moving.
#:
#: 14 -> 16 on 2026-09-11 (issue #199): `_loop_decisions`' read and
#: `_log_decision`'s write. Both are `DecisionLogRecorder` calls, which carry
#: their OWN 2s wall clock and never raise — so unlike every other call
#: counted here they cannot be the thing that wedges a tick. They are counted
#: anyway, because the number is «call sites this method holds» and a bound
#: that quietly stopped matching the source is the defect this gate exists for.
#:
#: 16 -> 17 on 2026-09-11 (issue #210): `_roster_index`' `representation`, the
#: depth the `<people>` index gained. ONE, because this constant counts CALL
#: SITES — `tests/architecture/test_dream_store_calls_are_declared.py` reads
#: them off this module's AST, so the number is a property of the source and
#: not of a run. The gather behind that site fans out to N members; what bounds
#: THAT is `[memory] roster_index_members` and `read_timeout_seconds` inside
#: `_roster_index`, not this number. A per-member `await` in a loop would be N
#: sites and would have to be counted as N; that is the reading that moves this
#: number again.
#:
#: 17 -> 18 on 2026-09-25 (DEC-207): `self._culture.head` per unit room,
#: the same per-room site shape as `_standing.read` beside it (rooms are at
#: most one per platform, DEC-105).
STORE_CALLS_PER_TICK = 18

PassReason = Literal[
    "daily", "changed", "held_quiet", "unchanged", "unclocked",
    "no_home_room", "nothing_shown",
]
PASS_REASONS: frozenset[str] = frozenset(get_args(PassReason))

#: Local hour (in `timezone`) after which the daily floor may render.
DEFAULT_DAILY_HOUR = 7
#: How long the unit's HOME ROOM must have been silent before a change
#: is acted on — `[dreamer] quiet_seconds`' meaning, threaded as a value.
#: Every room, until 2026-09-10; the home room is the only one a proactive
#: row can interrupt, so the rest were a proxy (`_quiet`).
DEFAULT_QUIET_SECONDS = 1800.0

DecisionAction = Literal["remind", "decline"]
DECISION_ACTIONS: frozenset[str] = frozenset(get_args(DecisionAction))

#: How far back the decision log reads (39 D7); how many rows the STORE read
#: is bounded by (it is keyed on the ai_peer across every unit, so the
#: bound is over all of them and must not bite before the room-map join);
#: and how many of THIS unit's rows, newest, reach the prompt.
DEFAULT_HISTORY_DAYS = 14
DEFAULT_HISTORY_LIMIT = 400
DEFAULT_HISTORY_SHOWN = 40
#: `<self_card>` conclusions, DEC-96's shipped number.
DEFAULT_SELF_CARD_CONCLUSIONS = 8
#: How many home-room members `_roster_index` will READ memory for (issue #210).
#:
#: The bound is on the READS and NOT on the render, and the split is the whole
#: point: every member still gets their name and aliases, because a member
#: missing from the index is invisible to her and invisible is
#: indistinguishable from absent (`_notes_block`'s argument in
#: `nora/agent/prefill.py`, which is why THAT block has no cap at all). What is
#: capped is the Honcho fan-out — one concurrent `representation` per member,
#: and a forty-person Discord home room is forty concurrent calls on the front
#: of a pass.
#:
#: The participation turn bounds its own reads on both axes and states that the
#: bound is on the reads; a reviewer pointed out on 2026-09-11 that the same
#: argument had not been applied here. It is the roster's ORDER that decides
#: who is read — `Room.members()`' order, which is the roster's own — so this
#: is a cheap bound rather than a good one; a home room past it wants a
#: relevance order, which is a different change.
DEFAULT_ROSTER_INDEX_MEMBERS = 12
#: How much of a Standing body reaches the prompt. Standing is capped at 1200
#: chars by its own tier; this is a second, per-room bound so N rooms cannot
#: crowd the notes out of the window.
#: One ROOM per line in the unit listing: a bullet, the room's label, and
#: the position. No handle to address and no clock at the head — the age rides
#: mid-line on `suffix`, which is where this grammar puts it.
_ROOM_STYLE = RowStyle(handle="bullet")


#: What a room with no `policy.toml` label is called on a `<rooms>` line.
#: Never its id (R-18) — the line's suffix already names the network.
UNNAMED_ROOM = "a room with no name"


def _room_name(room: UnitRoom) -> str:
    """The speaker position of a `<rooms>` line: the label, or the stand-in."""
    return room.label or UNNAMED_ROOM


def _room_body(_room: UnitRoom, *, text: str) -> str:
    """The position itself, already collapsed and clipped by the caller."""
    return text


def _room_suffix(room: UnitRoom, *, age: str) -> str:
    """The mid-line part of a room's standing line: which platform, how stale.

    A `functools.partial` rather than a defaulted lambda — the accessor must
    not capture the loop's `age` (ruff B023) and mypy cannot infer a lambda
    with defaults.
    """
    return f"({room.platform}), as of {age} ago"


def _clip(text: str) -> str:
    """`STANDING_CHARS` worth of a position, with an ellipsis when it ran on."""
    return text if len(text) <= STANDING_CHARS else text[:STANDING_CHARS] + "…"


STANDING_CHARS = 800


class _Person(Protocol):
    """The three fields the people index renders. A structural type rather than
    `PersonRef`: that lives in `nora.domain`, and naming it here would say the
    roster's shape is this module's business when only these fields are.

    `honcho_peer_id` joined the two names when the index gained DEPTH (issue
    #210, design 39 D9): the block used to answer «who is Aaron» and stopped
    there, so the pass spent its `max_memory_calls` ration re-asking it. A name
    cannot key a `representation` read — `honcho_peer_id` is the roster's
    canonical id and the only thing that can (`PersonRef`: `== person_id`, by
    construction).
    """

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> tuple[str, ...]: ...

    @property
    def honcho_peer_id(self) -> str: ...


@dataclass(frozen=True)
class UnitRoom:
    """One room of a unit as the adaptor needs to name it: where to read
    Standing, and which room is HOME (39 D2). Built by the composition root
    from `Policy`; this module never reads policy."""

    platform: str
    conversation_id: str
    #: The operator's `policy.toml` label, or `""`. Never the id: every render
    #: of it is model-facing (R-18), and the renders say «a room with no name»
    #: rather than falling back.
    label: str
    home: bool = False
    #: What the operator says this room IS (`policy.Conversation.description`,
    #: issue #161) — «a games room; 2200 is when people log on, not an
    #: appointment». `None` where the operator wrote nothing, which is the
    #: shipped state. Rendered only for the HOME room, as `<presence>`'s
    #: `this_room_is` (it was `<home_room>`'s body until 2026-09-23),
    #: because that is the only room this pass reads people and positions
    #: through; the other rooms appear as positions, not as batches to
    #: interpret.
    description: str | None = None
    #: `policy.Conversation.proactive` — whether she may speak here unprompted.
    #: Read by `_open_notes_block` only, to say on each note whether the room it
    #: was born in can take a reminder about it. `remind` routes a note-linked
    #: row to that room and refuses a closed one (design/dreamer-agent-loop/40),
    #: and before this the pass could only find out by spending the call — see
    #: `_note_origin`.
    proactive: bool = False
    #: Every id this room is known by (`policy.Conversation.ids`), because
    #: `Note.established_in` holds whichever one the note was born under — the
    #: same keying `remind`'s `origin_rooms` map uses. Empty means
    #: `(conversation_id,)`.
    ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Decision:
    """One envelope item, typed. `note_id` is `None` for a unit-level
    entry («nothing here today») — legal, and it records no wake."""

    note_id: str | None
    action: DecisionAction
    why: str


@dataclass(frozen=True)
class _Shown:
    """What `inputs` rendered for one key, kept for `commit`. `rendered`
    says whether a prompt was produced at all — `commit` records the pass
    clock only then, so a blank tick never counts as a pass.

    `since_ms` is the pass clock as this tick READ it — the last pass whose
    rows were accounted for. `commit` reconciles every proactive row written
    after it, which is how a pass that died before `commit` (no readable
    sample) still has its rows charged: its clock was never stamped, so the
    next pass's window still reaches back over them. `None` means unclocked
    or first-ever, and reconciliation then falls back to `at_ms` — this
    pass's own render instant — so it still covers this pass's own rows.

    `at_ms` is when `inputs` rendered."""

    by_handle: Mapping[str, str]
    anchor_date: Mapping[str, int | None]
    rendered: bool = False
    since_ms: int | None = None
    at_ms: int = 0


class _NoteStore(Protocol):
    async def standing(self, *, unit: str) -> tuple[Note, ...]: ...

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...

    async def record_wake(
        self, *, unit: str, note_id: str, next_revisit_at: int, now_ms: int,
    ) -> WakeOutcome | None: ...


class _PassClock(Protocol):
    """`nora.domain.ports.DreamScheduleClock`, the two verbs."""

    async def last_fired(self, *, adaptor_name: str, key: str) -> int | None: ...

    async def record_fire(self, *, adaptor_name: str, key: str, at_ms: int) -> None: ...


class _TranscriptSource(Protocol):
    """One `RecordStore` read — the newest row of a room, for the quiet
    check. Narrowed to the one method for the curator's reason."""

    async def recent(
        self, *, conversation_id: str, limit: int,
    ) -> Sequence[TranscriptRow]: ...


class _StandingReader(Protocol):
    async def read(self, *, platform: str, conversation_id: str) -> Standing | None: ...


class _DecisionLog(Protocol):
    async def history(
        self, *, requester_person_id: str, since: datetime, limit: int,
    ) -> Sequence[Reminder]: ...


class _SelfMemory(Protocol):
    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = ...,
        target: str | None = ..., unit: str | None = ...,
    ) -> Recollection: ...


#: What `ProactiveAdaptor.inputs` carries, declared as data
#: (`design/context-assembly/01-the-registry.md`) rather than left as the
#: sequence of `parts.append(...)` calls in that method — «does proactive
#: carry `standing`?» used to be a trace through `inputs`, `_people_block`,
#: `_decision_log`, `_loop_decisions` and `_self_card`; it is now a grep of
#: this tuple. `inputs` hands its blocks to `assemble()`, so `order` IS the
#: order the model reads: reordering this table reorders the prompt. The
#: numbers were the append order of the hand join this replaced (step 2a of
#: `design/context-assembly/04-every-occasion.md`), and the goldens in
#: `tests/unit/test_dreamer_assembly_goldens.py` pin that order byte for byte.
#:
#: `<culture>` repeats, once per room, all at one `order`: `assemble` sorts
#: stably, so the rooms keep the unit's own room order.
#:
#: `key="turn"` for all eight, the safe default, for the same reason the
#: gate's table gives both of ITS rows `"turn"`: this pass is its own call
#: pattern, not part of the tag path's cached prefix, so a key borrowed from
#: `prefill._BLOCKS` would not describe the same cache boundary
#: (`design/context-assembly/03-sequencing.md` S3 acceptance #4). Nothing
#: routes on `key` yet either way (`00-overview.md` §3).
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("now", 10, "turn"),
    # `<presence>` REPLACES `<home_room>` (design/self-awareness/02, D2):
    # the same block the tag path renders, from the same renderer, with the
    # home room as the room she is in and the operator's description as an
    # attribute. Two statements of where she is would be two sources for
    # one fact.
    ("presence", 20, "turn"),
    ("people", 30, "turn"),
    ("notes", 40, "turn"),
    ("rooms", 50, "turn"),
    # One `<culture room=…>` per room of the unit that has been folded
    # (DEC-207), right under the positions: `<rooms>` says where she had got
    # to in each, and the account says what each room IS — the half a pass
    # choosing WHERE to raise something was deciding without.
    ("culture", 55, "turn"),
    ("decisions", 60, "turn"),
    ("loop_decisions", 70, "turn"),
    ("self_card", 80, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


class ProactiveAdaptor:
    """One `DreamAdaptor`: read a unit, decide what to raise and when,
    through `remind`; record a wake on every note decided about."""

    name = PROACTIVE_ADAPTOR_NAME
    key_kind = "unit"
    uses_watermark = False
    objective = "proactive"
    envelope_key = "decisions"
    tools: frozenset[str] = PROACTIVE_TOOLS
    #: `DreamAdaptor.store_calls_per_tick` — see `STORE_CALLS_PER_TICK` above.
    store_calls_per_tick = STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        notes: _NoteStore,
        standing: _StandingReader | None,
        reminders: _DecisionLog,
        memory: _SelfMemory | None,
        rooms_of: Callable[[str], Sequence[UnitRoom]],
        # WHO IS IN THE HOME ROOM, name and the handles they are called by.
        # A callable for `rooms_of`'s reason: the roster is read through
        # `nora.agent.room`, which this layer may not import.
        #
        # Issue #145. Without it the pass held no index for PEOPLE, only for
        # notes and rooms — so a note written in the group's own handles
        # («@熱咖啡☕少奶多糖», «@隨風而去») had nothing to resolve against, and
        # design 39 D9's «inject the index, call for depth» was implemented for
        # everything except the one thing every note is about. Measured on the
        # `friends` unit: six of ten tool calls in one pass were the same
        # memory search asking who these people were, until R-7 stopped it.
        #
        # `None` renders no block, which is the pre-2026-09-10 behaviour and
        # what a composition with no roster degrades to.
        people_of: Callable[[UnitRoom], Awaitable[Sequence[_Person]]] | None = None,
        # WHERE SHE IS (design/self-awareness/02, D2): the unit's rooms, their
        # transport state, that other groups exist, her handles. A callable
        # for `rooms_of`'s reason — reading policy and adapter health is the
        # composition root's `PresenceReader`, which this layer may not
        # import. `None` renders the home room's network and label alone.
        presence_of: Callable[[UnitRoom], Awaitable[Presence | None]] | None = None,
        ai_peer: str,
        clock: Callable[[], datetime],
        timezone: ZoneInfo,
        history_days: int = DEFAULT_HISTORY_DAYS,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        history_shown: int = DEFAULT_HISTORY_SHOWN,
        self_card_conclusions: int = DEFAULT_SELF_CARD_CONCLUSIONS,
        # `[memory] roster_index_conclusions` — the DEPTH of the `<people>`
        # index (issue #210, 39 D9). 0 is the pre-#210 block exactly: names and
        # aliases, no memory read at all, which is what a composition with no
        # `memory` degrades to anyway.
        roster_index_conclusions: int = 0,
        #: How many members the index READS for, and the wall clock over the
        #: whole batch. Both are belts: `DreamerRuntime` already bounds
        #: `inputs` at `store_calls_per_tick x [dreamer] store_timeout_seconds`,
        #: but that bound is shared with sixteen other call sites and says
        #: nothing about how many concurrent Honcho calls one of them may make.
        roster_index_members: int = DEFAULT_ROSTER_INDEX_MEMBERS,
        read_timeout_seconds: float = DEFAULT_MEMORY_READ_TIMEOUT_SECONDS,
        metrics: Metrics | None = None,
        clock_store: _PassClock | None = None,
        records: _TranscriptSource | None = None,
        quiet_seconds: float = DEFAULT_QUIET_SECONDS,
        daily_hour: int = DEFAULT_DAILY_HOUR,
        decisions: DecisionLogRecorder | None = None,
        # THE ROOMS' ACCOUNTS (design/culture-tier, DEC-207). Keyed on
        # `UnitRoom.conversation_id`, which the composition root fills with
        # the room's `primary_id` — the key the culture fold writes under
        # (DEC-181). `None` while `[culture] enabled = false`.
        culture: CultureReader | None = None,
    ) -> None:
        self._notes = notes
        self._culture = culture
        # `None` is `[standing] enabled = false`: no paragraph to read, and
        # the prompt says nothing about rooms rather than «no position».
        self._standing = standing
        self._reminders = reminders
        # `None` renders no self card — DEC-96's fail-open-and-silent shape.
        self._memory = memory
        # Unit -> its rooms, with the home room marked. A callable
        # because policy is loaded once at startup and this is the one seam
        # through which the adaptor learns about rooms at all.
        self._rooms_of = rooms_of
        self._people_of = people_of
        self._presence_of = presence_of
        self._ai_peer = ai_peer
        # An AWARE datetime, threaded rather than read: the decision log's
        # window and every «in N days» in the prompt are comparisons against
        # it, and a clock read inside would put them beyond a test's reach.
        self._clock = clock
        self._tz = timezone
        self._history = timedelta(days=history_days)
        self._history_limit = history_limit
        self._history_shown = history_shown
        self._self_card_conclusions = self_card_conclusions
        self._roster_index_conclusions = roster_index_conclusions
        self._roster_index_members = roster_index_members
        self._read_timeout_seconds = read_timeout_seconds
        self._metrics = metrics
        # The durable pass/wake clock (module docstring). `None` renders every
        # tick and counts `unclocked` — the contract fake and the eval drive;
        # composition passes the SQL clock the runtime's slots use.
        self._clock_store = clock_store
        # The quiet check's one read; `None` treats every room as quiet.
        self._records = records
        self._quiet_ms = int(quiet_seconds * 1000)
        self._daily_hour = daily_hour
        # The shared decision log (issue #199) — the OTHER half of the loop.
        # Distinct from `self._reminders` above, which is her own REMINDER rows
        # and renders as `<decisions>`: this one is what the participation
        # turn and the tag turn did in each of these rooms, and it renders as
        # `<loop_decisions>`. `None` renders no block and writes no row.
        self._decisions = decisions
        self._shown: dict[str, _Shown] = {}

    def _inc(self, name: str, **labels: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, 1.0, **labels)

    # -- inputs -------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The unit's orient state, rendered (39 D9). `batch` is always
        empty (`uses_watermark = False`) and is ignored.

        **Returns `""` when there is nothing to reason about, and that is
        load-bearing** (the curator's own argument): no home room, or no
        open notes AND no Standing. The runtime skips the model on a blank
        prompt and still calls `commit(key, ())`, which records nothing
        here because nothing was shown.

        **Raises** when the notes or the decision log cannot be read: both
        are inputs the decision rests on, and the runtime maps the raise to
        `unavailable`, never to a pass over a guessed world.
        """
        rooms = tuple(self._rooms_of(key))
        if not any(room.home for room in rooms):
            # COUNTED, like every other tick. An operator who never named a
            # home room otherwise sees no series at all for this unit,
            # and a wiring gap that reads as silence is the one this repo has
            # already paid for: a reason nobody counts is a reason nobody
            # notices.
            self._inc("nora_proactive_pass_total", reason="no_home_room")
            self._shown[key] = _Shown({}, {})
            return ""
        now = self._clock()
        now_ms = int(now.timestamp() * 1000)

        open_notes = tuple(await self._notes.standing(unit=key))
        # THE DETERMINISTIC CHECK, before anything is rendered and before any
        # model is near (issue #136): daily floor, or a change since the last
        # rendered pass in a quiet unit. Counted by reason, because the
        # reversal condition is a ratio over this series.
        reason, since_ms = await self._pass_reason(
            key, rooms, open_notes, now=now, now_ms=now_ms,
        )
        if reason in ("unchanged", "held_quiet"):
            self._inc("nora_proactive_pass_total", reason=reason)
            self._shown[key] = _Shown({}, {})
            return ""

        # ONE INDEX RENDERER, shared with the tag path and the note-candidates
        # pass (#292). The handles are still this pass's own — `note_wake` and
        # `remind` resolve a model-named `[n]` back to a note id through
        # `_Shown.by_handle` — but they are no longer a second numbering beside
        # a second grammar. `standing_only` is the ONE filter, called once
        # here, and `render_note_rows` numbers whatever it is handed 1..N in
        # order — so the map and the rendered `n` have a single derivation.
        shown_notes = standing_only(open_notes)
        by_handle = {str(i): n.id for i, n in enumerate(shown_notes, 1)}
        anchor_date = {n.id: n.anchor_date for n in shown_notes}
        self._shown[key] = _Shown(
            by_handle=by_handle, anchor_date=anchor_date, rendered=True,
            since_ms=since_ms, at_ms=now_ms,
        )

        standing_lines = await self._standing_lines(rooms, now_ms=now_ms)
        if not open_notes and not standing_lines:
            # The tick was DUE — `daily` or `changed` — and still rendered
            # nothing, because the unit has no open notes and no room has
            # written a position. Counted as what happened rather than as the
            # reason it was due: `daily` and `changed` are the series the
            # reversal condition reads as «a pass ran», and a blank render is
            # not one (audit 2026-09-10).
            self._inc("nora_proactive_pass_total", reason="nothing_shown")
            self._shown[key] = _Shown({}, {})
            return ""
        self._inc("nora_proactive_pass_total", reason=reason)

        now_order, now_key = _ROW["now"]
        blocks = [ContextBlock(
            name="now", order=now_order, key=now_key,
            # A CLOCK THIS PROCESS OWNS — nothing in the room wrote it.
            body=f"{now.astimezone(self._tz):%Y-%m-%d %a %H:%M} ({self._tz.key})",
        )]
        home = next(room for room in rooms if room.home)
        blocks.append(await self._presence_block(home))
        people = await self._people_block(home, unit=key)
        if people is not None:
            blocks.append(people)
        blocks.append(_open_notes_block(
            shown_notes, now=now, tz=self._tz, rooms=rooms,
        ))
        rooms_order, rooms_key = _ROW["rooms"]
        blocks.append(ContextBlock(
            name="rooms", order=rooms_order, key=rooms_key,
            body="\n".join(standing_lines) if standing_lines else "(no positions)",
        ))
        blocks.extend(await self._cultures(rooms))
        blocks.append(await self._decision_log(rooms, now=now))
        blocks.append(await self._loop_decisions(key, rooms, home, now=now))
        self_card = await self._self_card(key, platform=home.platform)
        if self_card is not None:
            blocks.append(self_card)
        return assemble(blocks, sep="\n\n")

    async def _presence_block(self, home: UnitRoom) -> ContextBlock:
        """Where she stands for this unit, as `<presence>` — the tag path's
        block from the tag path's renderer (design/self-awareness/02, D2).

        It replaced `<home_room>`, which said the platform and the label and
        nothing about the unit's other rooms or whether any of them was
        reachable: a pass deciding WHERE to raise something could not choose
        a room it did not know existed. The operator's description (issue
        #161) rides as `this_room_is`, where `<home_room>` carried it as its
        body.

        `presence_of` is the composition root's `PresenceReader`, read on the
        home room at pass time and never cached. `None` (no reader wired, the
        contract fake and a bare eval drive) or a reader that could not place
        the room renders the network ALONE — never a claim about rooms or
        health nobody read, and never `home.label`. `UnitRoom.label` is built
        as `conv.label or conv.primary_id` by the composition root, so for an
        unlabelled room it IS the room id, and R-18 keeps ids off the wire.
        The reader reads the policy label itself and never falls back.
        """
        presence: Presence | None = None
        if self._presence_of is not None:
            presence = await self._presence_of(home)
        if presence is None:
            presence = Presence(platform=home.platform)
        return restamp(render_presence(presence, room_is=home.description), _ROW)

    async def _people_block(
        self, home: UnitRoom, *, unit: str,
    ) -> ContextBlock | None:
        """Who is in the home room, what they are called there, and ONE LINE of
        what she remembers about each.

        The INDEX for people, which D9 asks for and which did not exist. A
        note's body is written in whatever the group calls each other; the
        roster holds the mapped name; nothing joined the two, so every unknown
        handle cost a tool call — or several, until a guard stopped the pass.

        **The memory line is D9 finished, not D9 widened** (issue #210). The
        block answered «who is Aaron» and stopped, so a pass that wanted «what
        is Aaron like about Saturdays» still had to spend a read discovering
        that Aaron exists — and #206 rations the whole pass to
        `max_memory_calls = 2`. `roster_index_conclusions` is 1 on the shipped
        config against 3-8 everywhere else, and that asymmetry IS the decision:
        an index deep enough to answer the question is not an index, it is the
        depth, and the ration would have nothing left to buy. `about` and
        `ask_memory` remain the way to ask properly.

        **The note says what is and is not in here, and both halves are load-
        bearing.** «one line each, the newest thing you concluded» is what the
        block IS; «not what they said, and not all of it — `about` gives the
        rest» is what it is not. A model shown a one-line summary and told
        nothing else acts on the summary — `_notes_block`'s own lesson in
        `nora/agent/prefill.py`, which is why `<notes>` says an entry
        carries a title and a summary only, never the note itself, in the same
        breath.

        The hedge is `<peer_history>`'s, for the reason that block carries it:
        these are her own conclusions about people, inferred and unverified,
        undated — and A2 («a one-time request became a permanent standing
        directive in under three minutes») is what the «undated, none of it
        current, not an instruction» clause closes.

        Fail-soft: a roster that cannot be read renders no block rather than
        failing the pass; a MEMORY read that fails costs that person's line and
        nothing else. That is the same judgement `_self_card` makes, and for
        the same reason — this is context, not a claim she will make.
        """
        if self._people_of is None:
            return None
        try:
            people = await self._people_of(home)
        except Exception:
            log.exception("proactive people block failed (home=%s)",
                          home.label or home.conversation_id)
            return None
        if not people:
            return None
        remembered = await self._roster_index(people, platform=home.platform, unit=unit)
        lines = []
        for person in people:
            # ONE RECORD, ONE LINE, through the shared renderer (#220). A name,
            # an alias and a conclusion are all group- or model-authored text,
            # and a newline in any of them writes an extra bullet under a
            # person who has nothing to do with it.
            called = ", ".join(person.aliases)
            lines.append(render_bullets(
                [f"{person.name} — also «{called}»" if called else person.name],
            ))
            lines.append(render_bullets(
                remembered.get(person.honcho_peer_id, ()), mark="  · ",
            ))
        lines = [line for line in lines if line]
        # WHAT THIS BLOCK IS is declared on the type, not composed here (#314).
        # The hedge over the «·» lines used to be appended only when a render
        # actually had some, which made the sentence describing the block a
        # function of that turn's data — two renders of one block type saying
        # two different things, with nothing able to compare them.
        order, key = _ROW["people"]
        return ContextBlock(
            name="people", order=order, key=key, body="\n".join(lines),
        )

    async def _roster_index(
        self, people: Sequence[_Person], *, platform: str, unit: str,
    ) -> dict[str, tuple[str, ...]]:
        """`honcho_peer_id -> conclusions`, one concurrent batch for the whole
        roster (issue #210).

        CONCURRENT for `nora.engagement.context.assemble`'s arithmetic reason:
        the roster is read once per pass and a sequential read per member would
        put N round trips on the front of it. `return_exceptions=True` so one
        member's failure costs THAT member's line — the block, the pass and the
        other members are not on the hook for it — counted once per failure on
        its own stage.

        BOUNDED on both axes, for the reason `FirePath._read_memory` is: the
        fan-out is one concurrent Honcho call PER MEMBER, and a forty-person
        home room is forty of them on the front of a pass.
        `[memory] roster_index_members` caps how many are READ (never how many
        are rendered — see `DEFAULT_ROSTER_INDEX_MEMBERS`), and
        `read_timeout_seconds` bounds the whole batch.

        `{}` when either knob is 0 or there is no memory port at all, which is
        the pre-#210 block byte-for-byte.
        """
        if (
            self._memory is None
            or self._roster_index_conclusions <= 0
            or self._roster_index_members <= 0
        ):
            return {}
        targets = tuple(dict.fromkeys(
            person.honcho_peer_id for person in people if person.honcho_peer_id
        ))[:self._roster_index_members]
        if not targets:
            return {}
        try:
            results = await asyncio.wait_for(asyncio.gather(*(
                self._memory.representation(
                    peer_id=self._ai_peer,
                    target=target,
                    # The pass is about what this group has NOT SETTLED, so the
                    # index is curated to that question rather than to a message
                    # (there is none — 39 D9 drops the transcript from this turn).
                    search_query="what is still open with this person in this group",
                    max_conclusions=self._roster_index_conclusions,
                    # This pass is keyed on the UNIT, so the read is too
                    # (DEC-214): its Honcho workspace and its named scope.
                    unit=unit,
                )
                for target in targets
            ), return_exceptions=True), timeout=self._read_timeout_seconds)
        except Exception:
            # A port that never ANSWERS, which the per-member handling below
            # cannot see: that catches a raise. Costs the whole index and
            # nothing else — the names still render.
            log.exception("proactive: the roster index exceeded its bound")
            self._inc(
                "nora_errors_total", stage=STAGE_PROACTIVE_ROSTER_INDEX,
                platform=platform,
            )
            return {}
        index: dict[str, tuple[str, ...]] = {}
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException) or not result.reachable:
                log.warning(
                    "proactive: roster-index recall failed for one member (%s)",
                    "unreachable" if not isinstance(result, BaseException)
                    else type(result).__name__,
                )
                self._inc(
                    "nora_errors_total", stage=STAGE_PROACTIVE_ROSTER_INDEX,
                    platform=platform,
                )
                continue
            if result.conclusions:
                index[target] = result.conclusions
        return index

    async def _pass_reason(
        self, key: str, rooms: Sequence[UnitRoom], open_notes: Sequence[Note],
        *, now: datetime, now_ms: int,
    ) -> tuple[PassReason, int | None]:
        """Why this tick renders, or why it does not (module docstring), and
        the pass clock it measured that against — `commit`'s reconciliation
        window."""
        if self._clock_store is None:
            return "unclocked", None
        last = await self._clock_store.last_fired(adaptor_name=PASS_CLOCK_NAME, key=key)
        if last is None:
            # Never rendered for this unit: the first pass is the floor.
            return "daily", None
        local_now = now.astimezone(self._tz)
        local_last = datetime.fromtimestamp(last / 1000, tz=self._tz)
        if local_now.date() > local_last.date() and local_now.hour >= self._daily_hour:
            return "daily", last
        if not await self._changed_since(key, rooms, open_notes, last_ms=last, now_ms=now_ms):
            return "unchanged", last
        if not await self._quiet(rooms, now_ms=now_ms):
            return "held_quiet", last
        return "changed", last

    async def _changed_since(
        self, key: str, rooms: Sequence[UnitRoom], open_notes: Sequence[Note],
        *, last_ms: int, now_ms: int,
    ) -> bool:
        """Whether the ORIENT state moved since `last_ms`: a note created,
        revised on the TAG PATH (a background revision — the debrief's
        append, the curator's own write — is not a change, for D11's reason),
        or newly past its `revisit_at`; or a room's Standing rewritten."""
        for note in open_notes:
            if note.created_at > last_ms:
                return True
            if note.revisit_at is not None and last_ms < note.revisit_at <= now_ms:
                return True
            head = await self._notes.read(unit=key, note_id=note.id)
            if head is None:
                continue
            revision = head[1]
            if revision.written_at > last_ms and is_tag_path_revision(revision.established_by):
                return True
        if self._standing is not None:
            for room in rooms:
                try:
                    position = await self._standing.read(
                        platform=room.platform, conversation_id=room.conversation_id,
                    )
                except Exception:  # noqa: BLE001, S112 — the render path logs and counts it
                    # A room whose position cannot be read is not evidence
                    # that it moved.
                    continue
                if position is not None and position.updated_at > last_ms:
                    return True
        return False

    async def _quiet(self, rooms: Sequence[UnitRoom], *, now_ms: int) -> bool:
        """The HOME ROOM silent for `quiet_seconds` — its newest row, whoever
        wrote it. No record store wired treats the unit as quiet; a
        failed read treats the room as not quiet (held, retried next tick),
        never as silent.

        THE HOME ROOM AND NOT EVERY ROOM (revised 2026-09-10 by the operator;
        design 39 D10 said "every room of the unit"). A proactive row
        fires into the home room — `inputs` renders exactly one `<presence>`
        and `policy.py` refuses a row anywhere else — so the home room is the
        ONLY room this pass can interrupt. Every other room of the unit
        was a proxy for that, and on the live `friends` unit the proxy
        was the whole gate: Zone 42 is a games room on Discord, the note that
        moved was raised in it, and the pass that would have spoken into the
        Signal room was held `held_quiet` on Zone 42's traffic instead.
        Measured over the 14 days to 2026-09-10, 337 hourly ticks on that
        unit: quiet on every room 257 (76%), quiet on the home room 313
        (93%), and the longest unbroken unit-busy run was 6 hours — long
        enough to swallow a note anchored on the day it was raised, which is
        the case that produced this change.

        **Reopens if** a `changed` pass is answered as a barge-in: the
        protection this drops is «a sibling room is still settling the very
        thing she is about to raise», and the room that CHANGED is not read
        here — `_changed_since` returns a bool, not which room moved. Making
        the hold follow the changed room is the fix if that shows up; it is
        not built on one week of one unit.
        """
        if self._records is None:
            return True
        # `inputs` returns `no_home_room` before `_pass_reason` is reached, so
        # this is never empty on the live path — and it is a single-caller
        # invariant with no test of its own, which is why the empty case is
        # written out rather than left to the loop. An empty loop would fall
        # through to `return True` and read as QUIET, opening the gate on
        # exactly the wiring gap the guard exists to catch; the rest of this
        # method fails closed and so does this.
        homes = [r for r in rooms if r.home]
        if not homes:
            return False
        for room in homes:
            try:
                rows = await self._records.recent(
                    conversation_id=room.conversation_id, limit=1,
                )
            except Exception:
                log.exception("proactive: quiet read failed for %s",
                              room.label or room.conversation_id)
                return False
            if rows and now_ms - rows[-1].ts < self._quiet_ms:
                return False
        return True

    async def _cultures(self, rooms: Sequence[UnitRoom]) -> list[ContextBlock]:
        """One `<culture room=…>` per room of the unit with an account
        (DEC-207), in `rooms` order. A room never folded renders nothing; a
        failed read is counted per room (`stage="proactive_culture"`) and
        renders nothing either — `read_culture`'s contract."""
        out: list[ContextBlock] = []
        for room in rooms:
            block = render_culture(
                await read_culture(
                    self._culture.head(key=room.conversation_id)
                    if self._culture is not None else None,
                    stage="proactive_culture", platform=room.platform,
                    metrics=self._metrics,
                    timeout_seconds=self._read_timeout_seconds,
                ),
                now=self._clock(), room=_room_name(room),
            )
            if block is not None:
                out.append(restamp(block, _ROW))
        return out

    async def _standing_lines(
        self, rooms: Sequence[UnitRoom], *, now_ms: int,
    ) -> list[str]:
        if self._standing is None:
            return []
        lines: list[str] = []
        for room in rooms:
            try:
                position = await self._standing.read(
                    platform=room.platform, conversation_id=room.conversation_id,
                )
            except Exception:
                # Fail-soft, counted where the tag path counts it: a room
                # whose position could not be read is rendered as unread,
                # never as «no position».
                log.exception("proactive: standing read failed for %s",
                              room.label or room.conversation_id)
                self._inc(
                    "nora_errors_total", stage="proactive_standing",
                    platform=room.platform,
                )
                lines.append(render_rows(
                    [room], style=_ROOM_STYLE,
                    speaker=_room_name,
                    suffix=lambda r: f"({r.platform})",
                    body=lambda _r: "(could not read the position)",
                ))
                continue
            if position is None or not position.body.strip():
                continue
            # ONE ROOM, ONE LINE, through the shared renderer (#220). A
            # position is HER fold rather than a member's, and
            # `relevance_gate`'s argument for collapsing applies unchanged: a
            # fold summarises what people said, so the room's text reaches this
            # two hops away. Unlike the tag prefill's `<standing>`, which is
            # tag-delimited and holds its own newlines safely, this is one line
            # per ROOM — a newline here writes a whole extra room into a
            # unit listing, with a label it chose.
            #
            # The AGE sits mid-line here rather than at the head, so it rides on
            # `suffix` — the same seam the note-candidate index uses for its
            # room label. Collapse BEFORE the clip, so `STANDING_CHARS` bounds
            # what actually renders.
            age = _age(now_ms - position.updated_at)
            # Computed HERE, then bound as a default — the accessor must not
            # capture the loop's `position` (ruff B023) and must not do the work
            # in a default expression (B008).
            shown = _clip(one_line(position.body))
            lines.append(render_rows(
                [room], style=_ROOM_STYLE,
                speaker=_room_name,
                # Bound as defaults, not captured: these read the LOOP's
                # variables and a late-bound closure would render every room
                # with the last room's age and position (ruff B023).
                suffix=functools.partial(_room_suffix, age=age),
                # `partial`, not a closure: the accessor must not capture the
                # loop's `shown` (ruff B023).
                body=functools.partial(_room_body, text=shown),
            ))
        return lines

    async def _decision_log(
        self, rooms: Sequence[UnitRoom], *, now: datetime,
    ) -> ContextBlock:
        """Her own rows over the window, restricted to THIS unit's rooms
        (39 D7): `Reminder` has no unit column, so the join is the
        room map the composition root built from policy."""
        mine = {(room.platform, room.conversation_id) for room in rooms}
        # NOT fail-soft (audit 2026-09-09). The log is the one input that
        # says what she has already scheduled; a pass that could not read it
        # and went on to write rows would repeat yesterday on the strength of
        # nothing. R-27's shape is «I could not check» → refuse: the read
        # raises, `inputs` raises, and the runtime reports the run
        # `unavailable` — exactly what the unguarded `notes.standing` read
        # above already does. Standing and the self card stay fail-soft
        # because neither is a record of her own commitments.
        rows = await self._reminders.history(
            requester_person_id=self._ai_peer, since=now - self._history,
            limit=self._history_limit,
        )
        # The NEWEST of this unit's rows: the store's bound is over every
        # unit, so the cut is taken after the join, from the end.
        lines = [
            _decision_line(row, tz=self._tz)
            for row in rows
            if (row.platform, row.conversation_id) in mine
        ][-self._history_shown:]
        order, key = _ROW["decisions"]
        return ContextBlock(
            name="decisions", order=order, key=key,
            # HOW FAR BACK, as data. The description used to say «over the
            # last {days} days» with the number interpolated at inject
            # time; a description belongs to the type, so the number is an
            # attribute and the type annotates it. Dropping it outright
            # would leave «recently», which is the undated-input failure
            # the 2026-09-09/10 gate measurement is about.
            attrs=(("days", str(self._history.days)),),
            body=(
                "\n".join(lines) if lines
                else "(nothing decided in this window)"
            ),
        )

    async def _loop_decisions(
        self, key: str, rooms: Sequence[UnitRoom],
        home: UnitRoom, *, now: datetime,
    ) -> ContextBlock:
        """`<loop_decisions>` — what the OTHER half of the loop did in each of
        this unit's rooms over the last 24 hours (issue #199).

        **Not the same block as `<decisions>` one method up, and the two names
        are load-bearing.** `<decisions>` is her REMINDER rows: what she
        scheduled here and how it fired. This is the participation turn and
        the tag turn — the paths that answer when somebody else starts the
        conversation, which this pass has never been able to see. The
        incident is 2026-09-10: participation answered a question at 16:43, and
        this pass raised the same question in another room at 18:03 because
        nothing told it the answer had already been given.

        **Her own proactive rows are excluded**, and that is design 39 §1's
        self-echo brake applied to a new input rather than weakened. Three
        inputs already carry her own words back into this turn (Standing, the
        self card, and `<decisions>`); the brake on those is framing and
        measurement, not a predicate. Here a predicate is available, so it is
        used: a decide turn reading back its own last decide turn would be the
        fourth and the loudest, because the row says exactly what it concluded
        in the words it concluded it in.

        **Fail-SOFT, unlike `_decision_log`.** That one refuses the pass
        (R-27: «I could not check» → do not write rows on the strength of
        nothing) because it is the record of what she has already SCHEDULED,
        and a pass blind to it repeats yesterday. This block is a different
        kind of input: missing it means she may raise something already
        handled — the state this pass has been in since it shipped — so the
        honest degradation is a thinner prompt, which is also `_standing_lines`'
        and `_self_card`'s judgement.
        """
        if self._decisions is None:
            # No block and no claim, but the SLOT stays (`when_empty="nothing"`):
            # the hand join this replaced kept an empty string here, and the
            # two separators round it, in the one prompt that reaches a model
            # this way — the eval harness, which wires no log. Production
            # always does (`nora.app.composition`), so this is byte-compat for
            # the suite's baseline, not a behaviour anyone relies on; deleting
            # it moves every proactive eval prompt by two blank lines.
            order, key_ = _ROW["loop_decisions"]
            return ContextBlock(
                name="loop_decisions", order=order, key=key_, body="",
                when_empty="nothing",
            )
        # ONE unit-scoped read, not one per room. The pass is
        # unit-keyed, the bound is 20 rows over the whole unit rather than
        # 20 per room, and `idx_decision_log_unit_ts` serves it — see
        # `DecisionLogRecorder.read_unit` for why the room-scoped read
        # cannot answer this question at all.
        rows = await self._decisions.read_unit(
            platform=home.platform, unit=key,
            # In the QUERY. Post-filtering would let 20 rows of one busy
            # writer evict the one `participation spoke` row this pass exists
            # to see, and the block would say «nothing happened» about the
            # busiest room.
            writers=PROACTIVE_DECISION_WRITERS,
        )
        labels = {(room.platform, room.conversation_id): room.label for room in rooms}
        # RESTAMPED: `render_decisions` lives in `nora.domain.decision_log`,
        # shared with the tag path, and builds an unstamped block — where it
        # sits in THIS prompt is this occasion's fact, not that module's.
        return restamp(
            render_decisions(
                [
                    (
                        labels.get((row.platform, row.conversation_id), ""),
                        (row,),
                    )
                    for row in rows
                ],
                now=now,
                # `writers` is passed to the QUERY above and NOT to the render
                # (#314). The render used to take it too, so its framing
                # sentence could name exactly the deciders this block
                # carries; that sentence belongs to the block's type now and
                # says in words what the argument used to say by omission.
                empty="(nothing happened in these rooms in this window)",
            ),
            _ROW,
        )

    async def _self_card(self, key: str, *, platform: str) -> ContextBlock | None:
        """`<self_card>`, DEC-96's own framing: Honcho's `(nora -> nora)`
        distillation of her own replies — a record she may contradict, never
        a description of who she is. Fail-open and silent, like the tag path's."""
        if self._memory is None or self._self_card_conclusions <= 0:
            return None
        try:
            recall = await self._memory.representation(
                peer_id=self._ai_peer,
                search_query=f"what I said I would do for the {key} group",
                max_conclusions=self._self_card_conclusions,
                unit=key,
            )
        except Exception:
            log.exception("proactive: self-card recall failed")
            self._inc(
                "nora_errors_total", stage="proactive_self_card", platform=platform,
            )
            return None
        if not recall.reachable:
            return None
        return restamp(render_self_card(recall.conclusions), _ROW)

    # -- accept -------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]],
    ) -> Sequence[object]:
        """Typed parse into `_Decision`. Drop, log — never raise, never guess.

        `note` is a HANDLE from this key's last `inputs`, or absent/null for a
        unit-level entry. An unknown handle is a malformed item: the
        model cannot decide about a note it was not shown."""
        shown = self._shown.get(key) or _Shown({}, {})
        parsed: list[object] = []
        for item in items:
            action = item.get("action")
            why = item.get("why", "")
            handle = item.get("note")
            if (
                not isinstance(action, str) or action not in DECISION_ACTIONS
                or not isinstance(why, str)
            ):
                log.warning("proactive: dropping a malformed decision item")
                continue
            verb: DecisionAction = "remind" if action == "remind" else "decline"
            note_id: str | None = None
            if handle is not None:
                if not isinstance(handle, str | int) or str(handle) not in shown.by_handle:
                    log.warning("proactive: dropping a decision about an unknown note")
                    continue
                note_id = shown.by_handle[str(handle)]
            parsed.append(_Decision(note_id=note_id, action=verb, why=why.strip()))
        return tuple(parsed)

    # -- commit -------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> int:
        """The sink (39 D4/D6): ONE `record_wake` per note the pass decided
        to RAISE — a `remind` decision — never for a `decline`. D6's number
        is «I have raised this three times and nobody replied»: a strike is
        a raise the room did not answer, so a morning that looked at a note
        and left it must not charge one. `record_wake` itself decides whether
        the wake is progress (a tag-path revision since the last wake resets
        strikes) or a strike. Returns wakes recorded. Nothing else is
        written: the rows were written by `remind` inside the loop, and note
        content is not this adaptor's to touch.

        The curator records its own daily wake on every due note
        (`note_curator.py`), so a note both passes raise is struck twice a
        day at most; with the curator off — the live state on 2026-09-09 —
        this is the only writer of strikes.

        `commit(key, ())` is legal and records nothing — nothing was decided
        about, so no note was woken.

        THE ENVELOPE IS NOT THE GROUND TRUTH (audit 2026-09-10). `remind`
        writes its row inside the model loop, and three paths reach that write
        and never reach a `remind` DECISION here: a run where no sample was
        readable returns `unavailable` before `commit` is called at all, an
        item the Dreamer's screen rejects never reaches `accept`, and an item
        naming a handle this pass did not show is dropped in `accept`. On each
        of those the row exists and the note has no wake, so the note goes
        uncharged: the NEXT pass's raise would be its first strike, not its
        second. (`_may_wake_today` never stopped a second ROW either way; it
        gates strikes. `remind` refuses a second open row itself, #158.)
        So after the envelope is walked the rows themselves are read back — the
        real property, not the model's account of it — and any note raised
        without a wake is charged one. The window starts at the pass clock this
        tick READ (`_Shown.since_ms`), not at this pass, so rows orphaned by an
        `unavailable` run are covered by the next `commit`: that run never
        stamped the clock, so the window still reaches over them. **That
        guarantee is why the pass clock is stamped LAST** — see the comment at
        the stamp; stamped first it was false for a `commit` the runtime's
        own wall clock cancelled.
        """
        shown = self._shown.get(key) or _Shown({}, {})
        seen: set[str] = set()
        wakes = 0
        for proposal in proposals:
            decision = proposal.payload
            if not isinstance(decision, _Decision):
                continue
            self._inc("nora_proactive_decisions_total", action=decision.action)
            await self._log_decision(key, decision)
            if decision.action != "remind":
                continue
            if decision.note_id is None or decision.note_id in seen:
                continue
            seen.add(decision.note_id)
            if await self._wake(key, decision.note_id, shown=shown, now_ms=now_ms):
                wakes += 1
        for note_id in await self._raised_since(key, shown=shown, now_ms=now_ms):
            if note_id in seen:
                continue
            seen.add(note_id)
            # Counted apart from the envelope's own decisions: this is a row
            # the model wrote and did not report, and the rate of it is how
            # anyone learns that a screen or a sample is dropping decisions.
            self._inc("nora_proactive_reconciled_total")
            if await self._wake(key, note_id, shown=shown, now_ms=now_ms):
                wakes += 1
        if shown.rendered and self._clock_store is not None:
            # This pass RENDERED (a model was called, whatever it answered):
            # stamp it, so the next tick measures change against now. A blank
            # tick never reaches here with `rendered`, and a run the runtime
            # reported `unavailable` never reaches `commit` at all — so it is
            # retried at the next tick rather than counted as seen.
            #
            # **LAST, and it was FIRST until 2026-09-11 (cold review of the
            # issue-#60 bound).** `DreamerRuntime` now runs `commit` under a
            # wall clock, so a store that stops answering CANCELS this method
            # part-way — and stamped first, the pass was recorded as fired
            # with zero wakes written. The next tick then reads `last = now`,
            # finds the unit unchanged for the rest of the local day, and
            # does not render; the `remind` rows already written inside the
            # model loop are orphaned permanently, because the reconciliation
            # window above starts at the pass clock and that clock has moved
            # past them. The docstring's own guarantee — «that run never
            # stamped the clock, so the window still reaches over them» — was
            # false in exactly the case the bound introduces.
            #
            # Stamped last, every cancellation leaves the slot open: the next
            # tick re-renders, `_raised_since` reaches back over the orphaned
            # rows, and `_may_wake_today` still stops a second STRIKE. It never
            # stopped a second ROW, which is what this comment used to claim:
            # it gates the wake, keyed on the Hong Kong day of the last one,
            # and reads nothing `remind` writes. Production booked one note at
            # 23:01 and again at 01:01, and another at 07:02 and again at
            # 18:04 the same day. A second row is refused at
            # the write, by `remind` and `uq_reminders_proactive_open_note`
            # (issue #158). The cost is one re-render after a genuine timeout,
            # which is the cheap direction.
            await self._clock_store.record_fire(
                adaptor_name=PASS_CLOCK_NAME, key=key, at_ms=now_ms,
            )
        return wakes

    async def _log_decision(self, key: str, decision: _Decision) -> None:
        """One envelope decision, as a decision-log row (issue #199).

        **A `decline` is logged as loudly as a `remind`, and that is the whole
        value of this writer.** A row saying «I looked at this and decided not
        to raise it, because X» is the one thing that stops the next pass — or
        the gate, reading the same room tomorrow — from re-deriving the same
        conclusion from scratch. A log that recorded only the raises would
        answer «nothing has happened here» for a unit she has considered
        and passed on four days running.

        It rides beside `nora_proactive_decisions_total{action}`, not instead
        of it: the counter answers how many and how often, this answers WHICH
        and WHY, and AGENTS.md's rule is that those are different instruments.

        **Into the HOME ROOM**, because that is where a proactive row would
        fire (39 D2/D3) and so the room a later decider has to weigh it
        against. A decision with no home room never reaches here — `inputs`
        returns `""` and the pass does not run.

        **The model's own `why` is NOT written, and that is a reversal from
        this branch's first cut.** It is the one model-authored string that
        would have reached this table, it is written ABOUT a note, and it
        therefore carries note titles into a block whose whole claim is that it
        holds no notes. `note_id` is what a reader needs from a decide row —
        the participation turn has `<notes>` in the same prompt to resolve it
        against — and a reader that cannot resolve an id has no business being
        handed a title. So the row carries the verdict and the note it was
        about; the reasoning stays in the Langfuse trace, where it is
        re-derivable and not in front of a model.
        """
        if self._decisions is None:
            return
        home = next((room for room in self._rooms_of(key) if room.home), None)
        if home is None:
            return
        await self._decisions.record(
            writer="proactive_decide", platform=home.platform,
            conversation_id=home.conversation_id,
            verdict="remind" if decision.action == "remind" else "decline",
            note_id=decision.note_id,
        )

    async def _wake(
        self, key: str, note_id: str, *, shown: _Shown, now_ms: int,
    ) -> bool:
        """One note's wake, day-gated. True iff a wake was recorded."""
        if not await self._may_wake_today(key, note_id, now_ms=now_ms):
            return False
        anchor = shown.anchor_date.get(note_id)
        if anchor is None and note_id not in shown.anchor_date:
            # A note this pass did not render — a row orphaned by an earlier
            # one. Read its anchor rather than revisit it as if it had none.
            try:
                head = await self._notes.read(unit=key, note_id=note_id)
            except Exception:
                log.exception("proactive: note read failed for %s; continuing", note_id)
                head = None
            if head is not None:
                anchor = head[0].anchor_date
        next_at = default_revisit_at(anchor_date=anchor, now_ms=now_ms)
        try:
            outcome = await self._notes.record_wake(
                unit=key, note_id=note_id,
                next_revisit_at=next_at, now_ms=now_ms,
            )
        except Exception:
            log.exception(
                "proactive: record_wake failed for note %s; continuing", note_id,
            )
            return False
        if outcome is None:
            return False
        if self._clock_store is not None:
            await self._clock_store.record_fire(
                adaptor_name=WAKE_CLOCK_NAME, key=f"{key}/{note_id}", at_ms=now_ms,
            )
        return True

    async def _raised_since(
        self, key: str, *, shown: _Shown, now_ms: int,
    ) -> tuple[str, ...]:
        """The notes this unit's PROACTIVE rows were written against over
        the reconciliation window — what `remind` actually did, read from the
        store.

        Fail-soft, unlike the decision log `inputs` reads: there the read is an
        input a decision rests on and a failure must refuse the pass; here the
        envelope's own wakes are already recorded and refusing would undo them.
        A failed read leaves the pass exactly as it was before this reconcile
        existed, and logs.
        """
        if not shown.rendered:
            return ()
        since_ms = shown.since_ms if shown.since_ms is not None else shown.at_ms
        since = datetime.fromtimestamp(since_ms / 1000, tz=UTC)
        mine = {
            (room.platform, room.conversation_id) for room in self._rooms_of(key)
        }
        try:
            rows = await self._reminders.history(
                requester_person_id=self._ai_peer, since=since,
                limit=self._history_limit,
            )
        except Exception:
            log.exception("proactive: reconcile read failed (key=%s)", key)
            return ()
        out: list[str] = []
        for row in rows:
            if row.kind != "proactive":
                continue
            if (row.platform, row.conversation_id) not in mine:
                continue
            payload = row.payload if isinstance(row.payload, Mapping) else {}
            note_id = payload.get("note_id")
            if isinstance(note_id, str) and note_id and note_id not in out:
                out.append(note_id)
        return tuple(out)

    async def _may_wake_today(self, key: str, note_id: str, *, now_ms: int) -> bool:
        """At most one wake per note per Hong Kong day (39 D6 as amended by
        issue #136): a change-driven pass may decide the same note several
        times a day, and each of those is one decision the room has not yet
        had a chance to answer.

        A STRIKE gate, not a row gate: it does not stop a second `remind`
        about the note. That is `remind`'s own one-open-row check (#158)."""
        if self._clock_store is None:
            return True
        last = await self._clock_store.last_fired(
            adaptor_name=WAKE_CLOCK_NAME, key=f"{key}/{note_id}",
        )
        if last is None:
            return True
        today = datetime.fromtimestamp(now_ms / 1000, tz=self._tz).date()
        return datetime.fromtimestamp(last / 1000, tz=self._tz).date() < today


# -- rendering ----------------------------------------------------------------


#: **It is `<notes>`, and the second name is gone** (#314). It was
#: `<open_notes>` from the day this pass was written — hand-built as an
#: f-string, months after the tag path had already named its block `<notes>`,
#: with nothing choosing between them. #292 routed both through the injector
#: and kept each caller's tag, because a rename is a prompt change; #314 then
#: declared both as types, and a registry that can only see names recorded the
#: accident as a distinction.
#:
#: The argument for keeping it was written down here and BOTH HALVES WERE
#: FALSE, checked 2026-09-17: the decide turn's objective does not address this
#: block by name — `instructions.dreamer` serves `objective.body`, whose only
#: mentions are the prose «the notes» and «open-notes list», with every
#: `<open_notes>` hit sitting in unserved front matter — and `remind`/
#: `note_wake` resolve `[n]` against `_Shown.by_handle`, a dict built from the
#: same tuple, which no tag name touches.
#:
#: What the second name actually bought was room to drift. Same rows, same
#: renderer, and the two descriptions had grown 100 words apart: this one had
#: lost «where an entry here and the transcript disagree, the transcript is
#: what happened», «never name this bookkeeping itself» and «one still listed
#: here may already have settled», and annotated `title` differently. The tag
#: is what let that happen quietly, so the tag is what went.
#:
#: **`revisit` and `raised` are NAMED because the line that used to carry
#: their meaning is gone** (#292 audit). The flat entry said «; you decided to
#: raise this 2x before, no reply since» — the number arrived with its own
#: sentence. As an attribute it arrives as a bare `raised="2"`, and a first cut
#: of this block claimed in its docstring that the framing note explained it
#: while the note contained neither word. A count whose meaning is not stated is
#: a count a model will interpret, and the interpretation that matters is the
#: one this tier is careful about: a wake is charged when the decide turn writes
#: the row, BEFORE the act turn speaks or declines, so it counts decisions the
#: room never answered — never messages sent to them.
#:
#: **This block gained `untrusted="true"` at #292 and no commit message said
#: so.** Master's hand-built `<open_notes …>` carried no marker; routing the
#: pass through `ContextBlock` took the type's default. That is the right
#: classification under either reading of the word (#314) — the entries are
#: model-authored FROM member text — but it is a byte change to a prompt the
#: `proactive_decide` suite grades, and it arrived unannounced while
#: `<tag_message>` gaining the same marker got four paragraphs. Recorded here
#: because an unrecorded prompt change is indistinguishable from an accident
#: the next time somebody reads the diff.


def _note_origin(
    note: Note, rooms: Sequence[UnitRoom],
) -> tuple[tuple[str, str | None], ...]:
    """Where a note was born, and whether she may speak there unprompted.

    **The pass was deciding WHERE-dependent questions with no where in its
    input.** Langfuse `42f7e8bd` (2026-09-23 20:09 UTC): two plans Miles stated
    in Instagram Ridgeline, a room with no `proactive` grant. The pass called
    `remind` for both, both came back `refused_origin_not_proactive`, it tried
    the duplicate note's title for a third and hit the `remind` ceiling — and
    spent most of two minutes of reasoning guessing which room the plans were
    said in, wrongly suspecting the test room it was served from. The refusal
    is right (design/dreamer-agent-loop/40: a note-linked row goes to its
    origin room and never falls back); what was missing is that the list never
    said it would come.

    `room` is the origin's label, and `unprompted="not_allowed"` rides only
    where `remind` would refuse — no `proactive` grant, or an origin outside
    this unit's rooms (refused as unresolvable or cross-unit). An origin this
    unit does not hold carries no `room`, because there is no label to say.
    """
    for room in rooms:
        if note.established_in in (room.ids or (room.conversation_id,)):
            return (
                ("room", _room_name(room)),
                ("unprompted", None if room.proactive else "not_allowed"),
            )
    return (("unprompted", "not_allowed"),)


def _open_notes_block(
    notes: Sequence[Note], *, now: datetime, tz: ZoneInfo,
    rooms: Sequence[UnitRoom],
) -> ContextBlock:
    """The unit's open notes, through the ONE index renderer (#292).

    **Everything this pass needs beyond the shared row is an ATTRIBUTE now**,
    not a second grammar. `revisit` and `raised` were `; revisit in 3 h` and
    `; you decided to raise this 2x before` appended to a flat line whose field
    separator was an em dash a title may contain; they are now named attributes
    on the same `<note>` element the tag path renders, so the two prompts differ
    in what they carry rather than in how they are read.

    `raised` carries a bare integer, so its meaning is stated in
    `OPEN_NOTES_NOTE` — see the argument there. The docstring said that before
    the note actually said it, which is the half worth remembering.

    **`notes` is ALREADY filtered, and must be the same tuple `inputs` numbered
    its handle map from.** `n` here feeds `_Shown.by_handle`, which `remind` and
    `note_wake` resolve a WRITE against — so a second `is_standing` pass in the
    renderer would be a second derivation of the number that decides which note
    gets written to. `standing_only` is the one definition and `inputs` calls it
    once.

    Renders `(none)` rather than no block at all, unlike the tag path. This
    prompt's blocks are fixed — the objective walks them in order and an absent
    one reads as a missing section rather than as «nothing is open».
    """
    today = int(now.astimezone(tz).strftime("%Y%m%d"))

    def _extra(note: Note) -> tuple[tuple[str, str | None], ...]:
        revisit = None
        if note.revisit_at is not None:
            due_in = note.revisit_at - int(now.timestamp() * 1000)
            revisit = "due" if due_in <= 0 else f"in {_age(due_in)}"
        return (
            ("revisit", revisit),
            ("raised", str(note.strikes) if note.strikes else None),
            *_note_origin(note, rooms),
        )

    order, key = _ROW["notes"]
    return ContextBlock(
        name="notes", order=order, key=key,
        body=render_note_rows(notes, today=today, extra=_extra) or "(none)",
    )


def _decision_line(row: Reminder, *, tz: ZoneInfo) -> str:
    payload = row.payload if isinstance(row.payload, Mapping) else {}
    about = payload.get("note_title")
    about_clause = f" about «{escape(str(about))}»" if isinstance(about, str) else ""
    how = row.outcome or row.state.value
    return (
        f"- decided {row.created_at.astimezone(tz):%-d %b %H:%M}, for "
        f"{row.due_at.astimezone(tz):%-d %b %H:%M}{about_clause}: "
        f"«{escape(row.text)}» — {escape(how)}"
    )


def _age(ms: int) -> str:
    seconds = max(0, ms) // 1000
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 86_400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86_400} d"


__all__ = [
    "DECISION_ACTIONS",
    "PASS_CLOCK_NAME",
    "PASS_REASONS",
    "PROACTIVE_ADAPTOR_NAME",
    "PROACTIVE_TOOLS",
    "WAKE_CLOCK_NAME",
    "ProactiveAdaptor",
    "UnitRoom",
]
