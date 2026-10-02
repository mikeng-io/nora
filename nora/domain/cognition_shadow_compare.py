"""The shadow-week comparison, per tier and per clocked writer (migration step
3a's gate: "over a shadow week every clocked-adaptor write is matched or
deliberately closed"; step 4 retires the clocked writers ONE AT A TIME, so each
writer needs its own verdict, review B1 and D5, 2026-09-30).

`nora.domain.cognition_compare` compares only cognition's mirrored `remind`
against the live proactive pass. This module is the rest: the Cognition
session's `cognition_would_write` rows (note, event, Standing; a would-commit is
what it WOULD have written) against what the LIVE clocked writers actually
wrote in the same window. Pure: no store, no clock. The caller
(`nora.app.cognition_compare`) reads both sides and hands them in.

**It measures; it does not decide the gate.** Every writer gets three numbers
and one line. Whether 3 clocked-only rows is acceptable is the operator's read.

THE MATCHING RULES (one per tier; a pair is one-to-one, a row is in at most one)

- `note` (writers `note-candidates`, `note-curation`, `note-merge`, read off a
  revision's `established_by = dream:<writer>:...`). A would-write
  `note_update` matches a clocked REVISION (parent not null) of the same note
  id. A would-write `note_create` matches a clocked FIRST revision in the same
  room whose note carried, AS OF THAT REVISION, the same anchor (`anchor_date`
  and the sorted `anchor_entity_ids`) AND either the same title (casefolded,
  whitespace collapsed) or a non-empty, equal entity set: a date alone is not an
  identity (two notes for the same day are two notes), and the note row a later
  curation has since moved is not the note the writer created, so the caller
  reads the anchor and title from `row_history` at the revision's own time.
  Room is required for a create and NOT for an update: a note belongs to the
  unit, `established_in` is a routing key, and the same note is revised from any
  room.
- `event`. No clocked writer writes events: `written_by = 'tag'` is the LIVE tag
  path, which step 4 does not retire, so it is reported in its own section
  ("tag path (not retired)") and is in no per-writer gate verdict. A
  `written_by` other than `tag` and `backfill` would be a clocked writer and is
  reported as `event:<written_by>`. `event_propose` matches a clocked first
  revision in the same room with the same title; `event_revise` matches a
  clocked later revision of the same event id.
- `standing` (writers `standing-debrief`, `standing-ingest-fold`). Keyed by
  room. `standing_revision` does not name its writer, so the writer is DERIVED
  from `fold_count`: the debrief fold increments it, the rollup (ingest) fold
  carries it (`increments_fold_count=False`, DEC-239); a room's first revision
  counts as a debrief when `fold_count >= 1`. `source = 'operator'` edits are
  not a clocked writer and are not read. A Standing pair is room plus time
  ONLY: the recorded write does not say which messages the fold read, so "the
  cognition would-write came after the clocked write's input window" cannot be
  checked, and a pair is therefore reported as COINCIDENT, never as matched: both
  arms revised that room's Standing at about the same time, which is not the
  same as agreeing on what it should say. `matched` is 0 for a Standing writer
  by construction.
- `culture` (writer `culture`). Cognition has no culture verb (its tiers are
  note, event and Standing; step 4 moves culture to `character_revise`), so
  every clocked write is clocked-only by construction and is reported as such,
  never as a zero.
- `act` (writer `proactive`, `reminders kind='proactive'`) and `act-decision`
  (writer `proactive-decide`, `decision_log` verdict `remind`): cognition's
  mirrored `remind` chains, keyed by room plus note or event id, exactly
  `nora.domain.cognition_compare.compare`. **No time tolerance**: the match is
  by key inside the window, because a mirrored remind's own time is when it is
  set FOR, not when it was decided. A proactive row that names neither a note
  nor an event cannot be keyed: it is clocked-only with `keyless`, never
  dropped.

The keyed tiers match inside a TIME TOLERANCE: a clocked write and a
would-write pair only when their recorded times are within `tolerance_ms`.
Matching is greedy and deterministic: clocked rows in time order, each taking
the nearest unused compatible would-write (ties by id), so the same input is
always the same pairing. A pair counts as matched when EITHER side is in the
window; an unmatched row counts only when it is itself in the window.

WHAT `matched` MEANS, AND WHAT IT DOES NOT (`MATCHED_MEANS`, printed with every
report). A matched pair is two writes of the same KIND (the same tier, both a
creation or both a revision) about the same SUBJECT (the same note id or event
id; for a creation, the same room, anchor and title or entity set), recorded
within the tolerance. It does NOT establish that the two say the same thing (the
body, the venue, the time are never compared) or that either is right; it is
"both arms touched this thing about now". Standing is never matched at all, only
coincident (the recorded write does not say what the fold read).

DELIBERATELY CLOSED, AND WHO SAYS SO

A clocked-only row can be explained by a `cognition_session_close` row, but a
close is the MODEL's statement ("nothing needed writing", "another item's write
covers it", "I cannot settle it"), and the model is the thing under measurement:
a count of such closes is self-attestation (AGENTS.md: no self-attestation; DEC-252
(3) admits it). So an explaining close is reported in one of two ways, never summed:

- `closed_by_code`: the close is `covered` and the SAME session recorded a
  `would_commit` write in the same room and tier (and, for a row with a note or
  event id, for that same id). That is "another item's write already handles it"
  proven from the would-write rows, not from the model's word. It is the one
  close code can prove from the store.
- `closed_self_attested`: every other explaining close (`no_change`, `waiting`,
  and a `covered` no write backs). The model said it; nothing independent shows
  it. It is NOT evidence and is not counted as `closed`.

A `struck` close is code closing an item at `max_strikes`: it says the model
never settled the item, so it proves nothing about the clocked row either way
and explains none.

A clocked-only row is explained by a close only when ALL of these hold:

1. the close's agenda item is in the row's room;
2. the item's kind maps to the row's tier (`CLOSE_KIND_TIERS`: a `message` item
   is about notes, events, Standing and the act tiers, a `time` item about notes,
   events and the act tiers, a `rollup` item about Standing; `self`, `social` and
   `play` are about none of them; culture is never explained);
3. where the row carries a note or event id, the item names that id (an id in its
   summary, cues, or `note_id` / `event_id` fields). A close that names no id
   explains only rows that carry none (Standing, a keyless proactive row);
4. the close was recorded AFTER the clocked write (the session looked at what was
   there) and within the tolerance of it;
5. it explains ONE row. Rows are taken in time order, each claims the nearest
   unused eligible close, ties by reason then session. One `waiting` close is one
   explained row, not every row in its room.

`written` does not close a row (it claims a write happened, and B3 means nothing
proves it did) and `struck` does not (see above). A refused would-write nearby does not
close a row either: it is named beside it (`refused_nearby`) and the row stays open.
A clocked-only row no close explains is `open`. A cognition-only row is never closed: it is the
other arm's extra write, not an omission. The pool of closes is shared by every
tier of one report (tiers in report order), so a close is spent once.

Refused would-writes (`outcome = 'refused'`) are not writes cognition made.
They are not matched and not cognition-only; each clocked-only row carries how
many were refused in its room within the tolerance (`refused_nearby`), because
"cognition tried and was refused" is a different finding from "cognition never
looked".

FAIL-CLOSED, AND COVERAGE

A window in which NO Cognition session ran is an ERROR, never a table of zeros:
there is nothing to compare, and a column of "0 matched, 0 cognition-only" over no
sessions reads as a clean week. Every report also says how much of the week the
sessions covered (`SessionCoverage`: sessions, units, per day, wakes that never
became a session, and how many sessions ran with no skill injected, so a shadow
week with `AgendaSkills(by_kind={})` is never read as the full design).

THE TOLERANCE. The default is `DEFAULT_TOLERANCE_MINUTES` (65): the cadence of
the HOURLY clocked writers (`note-candidates`, and `note-merge` in shadow, both
"hourly" in `config.example.toml`) plus one scheduler tick
(`[dreamer.schedule] tick_seconds = 300`). A clocked write records when the
hourly pass ran; the cognition session records when the wake fired (seconds to
`quiet_seconds` after the stimulus); the two can differ by up to one cadence.
The DAILY writers (`note-curation` at the 00:00 floor, the Standing folds at
`fold_interval_seconds = 86400`) run once a day, so a 65-minute default will
show their rows as clocked-only more often than a wider one; widen
`--tolerance-minutes` (1445 covers a day plus a tick) to read them, and read the
result as a widening, not a pass. A narrow tolerance errs toward OPEN.

FAIL-CLOSED

A tier whose source cannot be read is `ERROR`, and every writer under it is
`ERROR`: its counts are absent (`None`), never zero. The caller builds that
with `TierResult.errored`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from nora.domain.cognition_compare import CompareResult

#: A `cognition_session_close` reason that explains a clocked-only row.
CLOSING_REASONS: tuple[str, ...] = ("no_change", "waiting", "covered")

#: The default tolerance, derived (see THE TOLERANCE): the hourly clocked
#: writers' cadence plus one scheduler tick.
DEFAULT_TOLERANCE_MINUTES = 65

#: Which tiers an agenda item of a kind is about. A kind absent here explains
#: nothing; culture is in no set (cognition has no culture verb).
CLOSE_KIND_TIERS: Mapping[str, frozenset[str]] = {
    "message": frozenset({"note", "event", "standing", "act", "act-decision"}),
    "time": frozenset({"note", "event", "act", "act-decision"}),
    "rollup": frozenset({"standing"}),
}

#: Writers that are LIVE paths, not clocked writers step 4 retires.
TAG_PATH_WRITERS: frozenset[str] = frozenset({"event:tag"})

#: The writers each tier is ALWAYS reported with, so a writer that wrote nothing
#: reads `0 matched ... 0 clocked-only` and never as an absent line.
EXPECTED_WRITERS: Mapping[str, tuple[str, ...]] = {
    "note": ("note-candidates", "note-curation", "note-merge"),
    "event": (),
    "standing": ("standing-debrief", "standing-ingest-fold"),
    "culture": ("culture",),
    "act": ("proactive",),
    "act-decision": ("proactive-decide",),
}

Status = Literal["compared", "error"]

_PLATFORM_PREFIX = re.compile(r"^[a-z][a-z0-9_-]*:")


def room_id(raw: str) -> str:
    """The room's bare conversation id. Three id spaces are live for one room
    (`platform:conversation_id`, the pair, the bare id: review M11), and only
    the bare id is on every table, so a leading `<platform>:` is dropped and
    two rows are the same room when their bare ids are equal."""
    match = _PLATFORM_PREFIX.match(raw)
    return raw[match.end():] if match else raw


def standing_writer(*, fold_count: int, previous_fold_count: int | None) -> str:
    """Which writer produced a `source = 'fold'` Standing revision."""
    before = 0 if previous_fold_count is None else previous_fold_count
    return "standing-debrief" if fold_count > before else "standing-ingest-fold"


@dataclass(frozen=True, slots=True)
class RoomDirectory:
    """Room labels and unit membership, from the policy. Output names a room by
    label and never by conversation id; a room with no label is `room#<hash>`,
    stable, and not reversible to the id."""

    labels: Mapping[str, str]
    unit_rooms: Mapping[str, frozenset[str]]

    def label(self, room: str) -> str:
        bare = room_id(room)
        return self.labels.get(bare) or f"room#{hashlib.sha256(bare.encode()).hexdigest()[:8]}"

    def rooms_of(self, unit: str) -> frozenset[str]:
        return self.unit_rooms.get(unit, frozenset())


@dataclass(frozen=True, slots=True)
class CognitionWrite:
    """One `would_commit` would-write."""

    id: str
    tier: str
    verb: str
    room: str
    at_ms: int
    session_id: int | None = None
    #: `note_update`: the note id. `event_revise`: the event id.
    key: str = ""
    #: `note_create`: `(anchor_date, sorted anchor_entity_ids)`. Never printed.
    anchor: tuple[int | None, tuple[str, ...]] | None = None
    #: `event_propose`: the title. Never printed.
    title: str = ""


@dataclass(frozen=True, slots=True)
class ClockedWrite:
    """One row a live clocked writer wrote."""

    writer: str
    tier: str
    id: str
    room: str
    at_ms: int
    key: str = ""
    create: bool = False
    anchor: tuple[int | None, tuple[str, ...]] | None = None
    title: str = ""
    keyless: bool = False


@dataclass(frozen=True, slots=True)
class Refusal:
    tier: str
    room: str
    at_ms: int


@dataclass(frozen=True, slots=True)
class Close:
    """A `cognition_session_close` row, with the room its agenda item is in."""

    session_id: int | None
    room: str
    reason: str
    at_ms: int
    #: The item's kind (`message`, `time`, `rollup`, ...): which tiers it is about.
    kind: str = "message"
    #: Ids the item names (tokens of its summary and cues, and any `note_id` /
    #: `event_id` field). A close that names none explains only id-less rows.
    cited: frozenset[str] = frozenset()
    #: `(tier, room, key)` of every `would_commit` write the close's own session
    #: recorded: what a `covered` close is proven against. Empty when the close is
    #: not `covered` or its session wrote nothing, so the close is self-attested.
    session_writes: frozenset[tuple[str, str, str]] = frozenset()


@dataclass(frozen=True, slots=True)
class Gap:
    """One unmatched row, either side. Ids, a room, times; never text."""

    side: Literal["cognition", "clocked"]
    tier: str
    writer: str
    id: str
    room: str
    at_ms: int
    verb: str = ""
    key: str = ""
    session_id: int | None = None
    closed_by: str | None = None
    #: The close that explains it is one code proved (`covered` and a same-session
    #: same-room same-tier write); False for a self-attested close.
    closed_by_code: bool = False
    refused_nearby: int = 0
    keyless: bool = False


@dataclass(frozen=True, slots=True)
class WriterVerdict:
    writer: str
    status: Status
    matched: int | None
    clocked_only: tuple[Gap, ...] = ()
    #: Standing only: a pair by room and time, which does not show the two arms
    #: agreed (see the module docstring). Never counted in `matched`.
    coincident: int | None = 0

    @property
    def closed(self) -> int | None:
        """Rows closed BY CODE (`closed_by_code`). A close the model wrote is not in
        this number: it is `closed_self_attested`."""
        if self.status == "error":
            return None
        return sum(1 for g in self.clocked_only if g.closed_by and g.closed_by_code)

    @property
    def closed_self_attested(self) -> int | None:
        """Rows a close the model wrote explains and code did not prove: its own
        say-so, not evidence."""
        if self.status == "error":
            return None
        return sum(1 for g in self.clocked_only if g.closed_by and not g.closed_by_code)

    @property
    def open(self) -> int | None:
        if self.status == "error":
            return None
        return sum(1 for g in self.clocked_only if not g.closed_by)


@dataclass(frozen=True, slots=True)
class TierResult:
    tier: str
    status: Status
    writers: tuple[WriterVerdict, ...]
    cognition_only: tuple[Gap, ...] = ()
    #: Would-writes refused, in the window; not a write either arm made.
    refused: int | None = 0
    error: str | None = None
    notes: tuple[str, ...] = ()
    #: The live tag path's writes, reported apart: not a writer step 4 retires,
    #: so in no per-writer gate verdict.
    tag_path: tuple[WriterVerdict, ...] = ()

    @classmethod
    def errored(cls, tier: str, error: str) -> TierResult:
        return cls(
            tier=tier, status="error", error=error, refused=None,
            writers=tuple(
                WriterVerdict(writer=w, status="error", matched=None, coincident=None)
                for w in EXPECTED_WRITERS[tier]
            ),
        )


#: What `matched` establishes, said in every report (module docstring).
MATCHED_MEANS: tuple[str, ...] = (
    "matched = the same KIND (tier; both a creation or both a revision) and the same "
    "SUBJECT (note id or event id; for a creation the room, anchor and title or entity "
    "set), recorded within the tolerance; an act is matched on room plus note or event id",
    "matched does NOT mean the two writes say the same thing (body, venue, time and "
    "intent are not compared) or that either is right; a Standing pair is only coincident",
)

#: The line every verdict block carries: which closes are evidence.
CLOSE_IS_NOT_EVIDENCE = (
    "closes: only `closed by code` (a `covered` close that a same-session write backs) is "
    "evidence; a close the model wrote (no_change, waiting, an unbacked covered) is its own "
    "say-so, counted apart as `self-attested`, and is never closed or matched"
)


@dataclass(frozen=True, slots=True)
class SessionCoverage:
    """How much of the window the Cognition sessions covered: the denominator every
    number in the report is read against. Counts only; no unit, room or person."""

    sessions: int
    #: Distinct units with at least one session, and how many units the policy
    #: names (None: the policy could not be read).
    units_with_sessions: int
    policy_units: int | None
    #: The window's length in days, and how many distinct days held a session.
    days: float
    active_days: int
    #: Wakes recorded in the window, those that never became a session (an empty
    #: agenda, a failure before the session row, a wake with no hook wired), and
    #: of those the ones that FAILED or timed out.
    wakes: int
    wakes_without_session: int
    wakes_failed_without_session: int
    #: Sessions that ran with no skill injected (`skill_versions` empty).
    sessions_without_skills: int

    @property
    def per_day(self) -> float:
        return self.sessions / self.days if self.days > 0 else 0.0


def coverage_lines(c: SessionCoverage) -> list[str]:
    units = (
        f"{c.units_with_sessions} unit(s) with a session"
        + ("" if c.policy_units is None else f" of {c.policy_units} in policy")
    )
    lines = [
        f"coverage: {c.sessions} session(s), {units}, {c.per_day:.1f} per day over "
        f"{c.days:.1f} day(s) ({c.active_days} with a session); "
        f"{c.wakes_without_session} of {c.wakes} wake(s) never became a session "
        f"({c.wakes_failed_without_session} of those failed)",
    ]
    if c.sessions_without_skills:
        lines.append(
            f"coverage: no skills injected in {c.sessions_without_skills} of {c.sessions} "
            "session(s): they read the base objective only, which is not the full design "
            "(30 §4); read nothing below as the design's verdict"
        )
    return lines


def coverage_json(c: SessionCoverage) -> dict[str, object]:
    return {
        "sessions": c.sessions, "units_with_sessions": c.units_with_sessions,
        "policy_units": c.policy_units, "days": round(c.days, 3), "per_day": round(c.per_day, 3),
        "active_days": c.active_days, "wakes": c.wakes,
        "wakes_without_session": c.wakes_without_session,
        "wakes_failed_without_session": c.wakes_failed_without_session,
        "sessions_without_skills": c.sessions_without_skills,
    }


@dataclass(frozen=True, slots=True)
class ShadowReport:
    since_ms: int
    until_ms: int
    tolerance_ms: int
    unit: str | None
    tiers: tuple[TierResult, ...]
    #: None only when the coverage could not be READ, which is `error`, never a
    #: report with a blank where the denominator should be. Required: a report
    #: cannot be built without saying what it is a report OF.
    coverage: SessionCoverage | None
    #: Set when there is nothing to compare (no session ran in the window, or the
    #: coverage was unreadable): the report is an ERROR with no tiers.
    error: str | None = None

    @property
    def any_error(self) -> bool:
        return (
            self.error is not None or self.coverage is None
            or any(t.status == "error" for t in self.tiers)
        )


class CloseLedger:
    """The closes of one report, each spendable ONCE. `claim` hands a clocked-only
    row the nearest unused close that is eligible for it (module docstring,
    DELIBERATELY CLOSED); the caller claims rows in time order, so the result is
    a pure function of its input."""

    def __init__(self, closes: Sequence[Close], tolerance_ms: int) -> None:
        self._closes = sorted(
            (c for c in closes if c.reason in CLOSING_REASONS),
            key=lambda c: (c.at_ms, c.session_id if c.session_id is not None else 0, c.reason),
        )
        self._used: set[int] = set()
        self._tolerance_ms = tolerance_ms

    def claim(self, *, tier: str, room: str, at_ms: int, key: str) -> str | None:
        claimed = self.claim_with_proof(tier=tier, room=room, at_ms=at_ms, key=key)
        return None if claimed is None else claimed[0]

    def claim_with_proof(
        self, *, tier: str, room: str, at_ms: int, key: str,
    ) -> tuple[str, bool] | None:
        """`(reason, proven)`: the close that explains the row, and whether code
        proves it (a `covered` close whose own session wrote the same tier in the
        same room, and the same id when the row has one). A proven close is taken
        before a self-attested one at the same distance, so evidence is not
        spent on a say-so."""
        best: tuple[int, int, str, int, int] | None = None
        for index, close in enumerate(self._closes):
            if index in self._used or room_id(close.room) != room_id(room):
                continue
            if tier not in CLOSE_KIND_TIERS.get(close.kind, frozenset()):
                continue
            if key and key not in close.cited:
                continue
            gap = close.at_ms - at_ms
            if gap < 0 or gap > self._tolerance_ms:
                continue
            proven = _proven_by_code(close, tier=tier, room=room, key=key)
            rank = (
                gap, 0 if proven else 1, close.reason,
                close.session_id if close.session_id is not None else 0, index,
            )
            if best is None or rank < best:
                best = rank
        if best is None:
            return None
        self._used.add(best[4])
        chosen = self._closes[best[4]]
        return chosen.reason, _proven_by_code(chosen, tier=tier, room=room, key=key)


def _proven_by_code(close: Close, *, tier: str, room: str, key: str) -> bool:
    """A `covered` close is "another item's write already handles it" (30 §2):
    code can check that from the would-write rows. True only when the close's own
    session recorded a `would_commit` write of this tier in this room, and, when
    the clocked row names a note or event, for that same id."""
    if close.reason != "covered":
        return False
    bare = room_id(room)
    return any(
        t == tier and room_id(r) == bare and (not key or k == key)
        for t, r, k in close.session_writes
    )


def _norm_title(title: str) -> str:
    return " ".join(title.casefold().split())


def _compatible(c: CognitionWrite, w: ClockedWrite) -> bool:
    if c.tier != w.tier:
        return False
    if c.verb in ("note_create", "event_propose"):
        if not w.create or room_id(c.room) != room_id(w.room):
            return False
        if c.verb == "note_create":
            if c.anchor is None or c.anchor != w.anchor:
                return False
            same_title = bool(_norm_title(c.title)) and _norm_title(c.title) == _norm_title(w.title)
            return same_title or bool(c.anchor[1])
        return bool(_norm_title(c.title)) and _norm_title(c.title) == _norm_title(w.title)
    if c.verb in ("note_update", "event_revise", "event_set_status"):
        return not w.create and bool(c.key) and c.key == w.key
    if c.verb == "standing_revise":
        return room_id(c.room) == room_id(w.room)
    return False


def match_writes(
    cognition: Sequence[CognitionWrite], clocked: Sequence[ClockedWrite], *, tolerance_ms: int,
) -> tuple[list[tuple[CognitionWrite, ClockedWrite]], list[CognitionWrite], list[ClockedWrite]]:
    """One-to-one, greedy, deterministic. See the module docstring."""
    unused = list(cognition)
    pairs: list[tuple[CognitionWrite, ClockedWrite]] = []
    clocked_only: list[ClockedWrite] = []
    for w in sorted(clocked, key=lambda x: (x.at_ms, x.id)):
        candidates = [
            c for c in unused
            if _compatible(c, w) and abs(c.at_ms - w.at_ms) <= tolerance_ms
        ]
        if not candidates:
            clocked_only.append(w)
            continue
        hit = min(candidates, key=lambda c: (abs(c.at_ms - w.at_ms), c.at_ms, c.id))
        unused.remove(hit)
        pairs.append((hit, w))
    return pairs, sorted(unused, key=lambda c: (c.at_ms, c.id)), clocked_only


def compare_tier(
    tier: str, *, cognition: Sequence[CognitionWrite], refusals: Sequence[Refusal],
    clocked: Sequence[ClockedWrite], closes: Sequence[Close] | CloseLedger, tolerance_ms: int,
    since_ms: int, until_ms: int, explainable: bool = True, notes: tuple[str, ...] = (),
) -> TierResult:
    """Match one tier and split the result per writer. `cognition` and `clocked`
    are read over the window widened by the tolerance on both sides; only rows in
    `[since_ms, until_ms)` are counted (a pair counts when either side is).
    `closes` is a `CloseLedger` shared across the tiers of one report, or a plain
    sequence for this tier alone."""
    ledger = closes if isinstance(closes, CloseLedger) else CloseLedger(closes, tolerance_ms)

    def inside(ms: int) -> bool:
        return since_ms <= ms < until_ms

    pairs, cog_left, clocked_left = match_writes(cognition, clocked, tolerance_ms=tolerance_ms)
    matched_by_writer: dict[str, int] = {}
    coincident_by_writer: dict[str, int] = {}
    for c, w in pairs:
        if inside(c.at_ms) or inside(w.at_ms):
            counts = coincident_by_writer if c.verb == "standing_revise" else matched_by_writer
            counts[w.writer] = counts.get(w.writer, 0) + 1

    cognition_only = tuple(
        Gap(
            side="cognition", tier=tier, writer="", id=c.id, room=c.room, at_ms=c.at_ms,
            verb=c.verb, key=c.key, session_id=c.session_id,
            keyless=(c.verb in ("note_update", "event_revise", "event_set_status") and not c.key),
        )
        for c in cog_left if inside(c.at_ms)
    )
    gaps_by_writer: dict[str, list[Gap]] = {}
    for w in clocked_left:
        if not inside(w.at_ms):
            continue
        refused = sum(
            1 for r in refusals
            if r.tier == tier and room_id(r.room) == room_id(w.room)
            and abs(r.at_ms - w.at_ms) <= tolerance_ms
        )
        claimed = (
            ledger.claim_with_proof(tier=tier, room=w.room, at_ms=w.at_ms, key=w.key)
            if explainable and w.writer not in TAG_PATH_WRITERS else None
        )
        gaps_by_writer.setdefault(w.writer, []).append(Gap(
            side="clocked", tier=tier, writer=w.writer, id=w.id, room=w.room, at_ms=w.at_ms,
            key=w.key, keyless=w.keyless, refused_nearby=refused,
            closed_by=None if claimed is None else claimed[0],
            closed_by_code=claimed is not None and claimed[1],
        ))
    seen = {w.writer for w in clocked} | set(matched_by_writer) | set(coincident_by_writer)

    def verdict(name: str) -> WriterVerdict:
        return WriterVerdict(
            writer=name, status="compared", matched=matched_by_writer.get(name, 0),
            coincident=coincident_by_writer.get(name, 0),
            clocked_only=tuple(gaps_by_writer.get(name, ())),
        )

    names = list(EXPECTED_WRITERS[tier]) + sorted(
        seen - set(EXPECTED_WRITERS[tier]) - TAG_PATH_WRITERS
    )
    return TierResult(
        tier=tier, status="compared", writers=tuple(verdict(n) for n in names),
        cognition_only=cognition_only,
        refused=sum(1 for r in refusals if r.tier == tier and inside(r.at_ms)), notes=notes,
        tag_path=tuple(verdict(n) for n in sorted(seen & TAG_PATH_WRITERS)),
    )


def act_tier(
    tier: str, result: CompareResult, *, closes: Sequence[Close] | CloseLedger,
    tolerance_ms: int, extra_notes: tuple[str, ...] = (),
) -> TierResult:
    """The act tiers, from `nora.domain.cognition_compare.compare`: matched by
    key inside the window, no time tolerance on the match (the tolerance only
    decides whether a close explains a clocked-only row). A live row naming no
    note and no event is clocked-only and `keyless`."""
    writer = EXPECTED_WRITERS[tier][0]
    ledger = closes if isinstance(closes, CloseLedger) else CloseLedger(closes, tolerance_ms)
    gaps_list: list[Gap] = []
    for p in sorted(
        (*result.proactive_only, *result.unkeyed), key=lambda a: (a.at_ms, a.reminder_id),
    ):
        claimed = ledger.claim_with_proof(
            tier=tier, room=p.conversation_id, at_ms=p.at_ms, key=p.note_id or p.event_id,
        )
        gaps_list.append(Gap(
            side="clocked", tier=tier, writer=writer, id=p.reminder_id,
            room=f"{p.platform}:{p.conversation_id}", at_ms=p.at_ms,
            key=p.note_id or p.event_id, keyless=not p.keys,
            closed_by=None if claimed is None else claimed[0],
            closed_by_code=claimed is not None and claimed[1],
        ))
    gaps = tuple(gaps_list)
    cognition_only = tuple(
        Gap(
            side="cognition", tier=tier, writer="", id=m.reminder_ref, verb="remind",
            room=f"{m.platform}:{m.conversation_id}", at_ms=m.at_ms,
            keyless=not any(
                ":" not in r or r.partition(":")[0] in ("note", "event") for r in m.subject_rows
            ),
        )
        for m in result.cognition_only
    )
    notes = (
        f"{len(result.cancelled)} mirrored remind(s) cognition cancelled are in neither arm",
        "match is by room plus note or event id inside the window; no time tolerance",
        *extra_notes,
    )
    return TierResult(
        tier=tier, status="compared", cognition_only=cognition_only, refused=0, notes=notes,
        writers=(WriterVerdict(
            writer=writer, status="compared", matched=len(result.matched), clocked_only=gaps,
        ),),
    )


def verdict_lines(report: ShadowReport) -> list[str]:
    """One line per clocked writer. A count, never a pass or a fail. A report with
    nothing to compare (no session in the window, or an unreadable coverage) is ONE
    ERROR line and no writer lines: a table of zeros there would read as a clean
    week."""
    if report.error is not None:
        return [f"ERROR: {report.error}"]
    if report.coverage is None:
        return ["ERROR: session coverage was not read; nothing to compare"]
    lines: list[str] = []
    for tier in report.tiers:
        for w in tier.writers:
            if w.status == "error":
                lines.append(
                    f"{w.writer}: ERROR ({tier.error or 'unreadable'}); not compared, not zero"
                )
                continue
            cog = f"{len(tier.cognition_only)} cognition-only (tier {tier.tier}, shared)"
            lines.append(
                f"{w.writer}: {w.matched} matched, {w.coincident} coincident, {cog}, "
                f"{len(w.clocked_only)} clocked-only ({w.closed} closed by code, "
                f"{w.closed_self_attested} closed self-attested: not evidence, {w.open} open)"
            )
    return lines


def tag_path_lines(report: ShadowReport) -> list[str]:
    """The live tag path's lines. Counts, and not a gate: nothing retires it."""
    lines: list[str] = []
    for tier in report.tiers:
        if tier.status == "error":
            lines.append(
                f"{tier.tier} tag path: ERROR ({tier.error or 'unreadable'}); not compared"
            )
            continue
        for w in tier.tag_path:
            lines.append(
                f"{w.writer}: {w.matched} matched, {len(w.clocked_only)} with no cognition "
                f"would-write nearby (the live tag path is not retired; no verdict)"
            )
    return lines


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _gap_json(g: Gap, directory: RoomDirectory) -> dict[str, object]:
    return {
        "side": g.side, "tier": g.tier, "writer": g.writer or None, "id": g.id,
        "verb": g.verb or None, "key": g.key or None, "room": directory.label(g.room),
        "at": _iso(g.at_ms), "session_id": g.session_id, "closed_by": g.closed_by,
        "closed_by_code": g.closed_by_code, "refused_nearby": g.refused_nearby,
        "keyless": g.keyless,
    }


def report_json(report: ShadowReport, directory: RoomDirectory) -> dict[str, object]:
    """The machine-readable form. `None` counts mean ERROR, never zero."""
    return {
        "since": _iso(report.since_ms), "until": _iso(report.until_ms),
        "tolerance_seconds": report.tolerance_ms // 1000, "unit": report.unit,
        "status": "error" if report.any_error else "compared",
        "error": report.error,
        "coverage": None if report.coverage is None else coverage_json(report.coverage),
        "matched_means": list(MATCHED_MEANS),
        "closes_are_evidence": CLOSE_IS_NOT_EVIDENCE,
        "verdicts": verdict_lines(report),
        "tag_path": tag_path_lines(report),
        "tiers": [
            {
                "tier": t.tier, "status": t.status, "error": t.error, "refused": t.refused,
                "cognition_only": [_gap_json(g, directory) for g in t.cognition_only],
                "notes": list(t.notes),
                "writers": [
                    {
                        "writer": w.writer, "status": w.status, "matched": w.matched,
                        "coincident": w.coincident,
                        "clocked_only": None if w.status == "error" else len(w.clocked_only),
                        "closed": w.closed, "closed_by_code": w.closed,
                        "closed_self_attested": w.closed_self_attested, "open": w.open,
                        "unmatched": [_gap_json(g, directory) for g in w.clocked_only],
                    }
                    for w in t.writers
                ],
                "tag_path": [
                    {
                        "writer": w.writer, "matched": w.matched,
                        "unmatched": [_gap_json(g, directory) for g in w.clocked_only],
                    }
                    for w in t.tag_path
                ],
            }
            for t in report.tiers
        ],
    }


def render_text(report: ShadowReport, directory: RoomDirectory) -> list[str]:
    lines = [
        f"cognition compare (shadow vs clocked writers) {_iso(report.since_ms)} .. "
        f"{_iso(report.until_ms)}  tolerance={report.tolerance_ms // 60_000}min"
        + (f"  unit={report.unit}" if report.unit else ""),
        "",
    ]
    if report.coverage is not None:
        lines += [*coverage_lines(report.coverage), ""]
    if report.error is not None or report.coverage is None:
        lines += [f"  {line}" for line in verdict_lines(report)]
        return lines
    lines += [f"matched: {m}" for m in MATCHED_MEANS]
    lines += [CLOSE_IS_NOT_EVIDENCE, "", "verdicts (counts only; the gate is yours):"]
    lines += [f"  {line}" for line in verdict_lines(report)]
    lines += ["", "tag path (not retired; excluded from the verdicts above):"]
    lines += [f"  {line}" for line in tag_path_lines(report)] or ["  none"]
    for tier in report.tiers:
        lines.append("")
        suffix = f" ({tier.error})" if tier.error else ""
        lines.append(f"[{tier.tier}] {tier.status.upper()}{suffix}")
        if tier.status == "error":
            continue
        lines.append(f"  refused would-writes in window: {tier.refused}")
        lines += [f"  note: {n}" for n in tier.notes]
        for g in tier.cognition_only:
            lines.append(
                f"  cognition-only  {g.verb}  id={g.id}  session={g.session_id}  "
                f"room={directory.label(g.room)}  at={_iso(g.at_ms)}"
                + (f"  key={g.key}" if g.key else "") + ("  KEYLESS" if g.keyless else "")
            )
        for w in tier.writers:
            for g in w.clocked_only:
                lines.append(
                    f"  clocked-only    {w.writer}  id={g.id}  room={directory.label(g.room)}  "
                    f"at={_iso(g.at_ms)}" + (f"  key={g.key}" if g.key else "")
                    + (
                        f"  closed_by={g.closed_by}"
                        + ("(code)" if g.closed_by_code else "(self-attested)")
                        if g.closed_by else "  OPEN"
                    )
                    + (f"  refused_nearby={g.refused_nearby}" if g.refused_nearby else "")
                    + ("  KEYLESS" if g.keyless else "")
                )
        for w in tier.tag_path:
            for g in w.clocked_only:
                lines.append(
                    f"  tag-path        {w.writer}  id={g.id}  room={directory.label(g.room)}  "
                    f"at={_iso(g.at_ms)}" + (f"  key={g.key}" if g.key else "")
                )
    return lines
