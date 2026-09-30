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
  room whose note carries the same anchor (`anchor_date` and the sorted
  `anchor_entity_ids`). Room is required for a create and NOT for an update: a
  note belongs to the unit, `established_in` is a routing key, and the same
  note is revised from any room.
- `event` (writer `event:<written_by>`, today always `event:tag`: no clocked
  writer sets `written_by`, so the tag path's own writes land here until one
  does). `event_propose` matches a clocked first revision in the same room with
  the same title; `event_revise` matches a clocked later revision of the same
  event id.
- `standing` (writers `standing-debrief`, `standing-ingest-fold`). Keyed by
  room. `standing_revision` does not name its writer, so the writer is DERIVED
  from `fold_count`: the debrief fold increments it, the rollup (ingest) fold
  carries it (`increments_fold_count=False`, DEC-239); a room's first revision
  counts as a debrief when `fold_count >= 1`. `source = 'operator'` edits are
  not a clocked writer and are not read.
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

The three keyed tiers match inside a TIME TOLERANCE: a clocked write and a
would-write pair only when their recorded times are within `tolerance_ms`.
Matching is greedy and deterministic: clocked rows in time order, each taking
the nearest unused compatible would-write (ties by id), so the same input is
always the same pairing. A pair counts as matched when EITHER side is in the
window; an unmatched row counts only when it is itself in the window.

DELIBERATELY CLOSED

A clocked-only row is `closed` when cognition looked at that room at about that
time and decided nothing needed writing: a `cognition_session_close` row with
reason `no_change`, `waiting` or `covered` whose agenda item is in the same
room, closed within the tolerance of the clocked row. `written` does not close
a row (it claims a write happened, and B3 means nothing proves it did) and
`struck` does not (code's own strike at `max_strikes`, not a decision). One
close can explain every clocked-only row in its room and window. A clocked-only
row no close explains is `open`. A cognition-only row is never closed: it is
the other arm's extra write, not an omission.

Refused would-writes (`outcome = 'refused'`) are not writes cognition made.
They are not matched and not cognition-only; each clocked-only row carries how
many were refused in its room within the tolerance (`refused_nearby`), because
"cognition tried and was refused" is a different finding from "cognition never
looked".

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

#: The writers each tier is ALWAYS reported with, so a writer that wrote nothing
#: reads `0 matched ... 0 clocked-only` and never as an absent line.
EXPECTED_WRITERS: Mapping[str, tuple[str, ...]] = {
    "note": ("note-candidates", "note-curation", "note-merge"),
    "event": ("event:tag",),
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
    refused_nearby: int = 0
    keyless: bool = False


@dataclass(frozen=True, slots=True)
class WriterVerdict:
    writer: str
    status: Status
    matched: int | None
    clocked_only: tuple[Gap, ...] = ()

    @property
    def closed(self) -> int | None:
        if self.status == "error":
            return None
        return sum(1 for g in self.clocked_only if g.closed_by)

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

    @classmethod
    def errored(cls, tier: str, error: str) -> TierResult:
        return cls(
            tier=tier, status="error", error=error, refused=None,
            writers=tuple(
                WriterVerdict(writer=w, status="error", matched=None)
                for w in EXPECTED_WRITERS[tier]
            ),
        )


@dataclass(frozen=True, slots=True)
class ShadowReport:
    since_ms: int
    until_ms: int
    tolerance_ms: int
    unit: str | None
    tiers: tuple[TierResult, ...]

    @property
    def any_error(self) -> bool:
        return any(t.status == "error" for t in self.tiers)


def _explain(room: str, at_ms: int, closes: Sequence[Close], tolerance_ms: int) -> str | None:
    """The reason of the nearest close in `room` within the tolerance, or None."""
    best: tuple[int, str, int] | None = None
    for close in closes:
        if close.reason not in CLOSING_REASONS or room_id(close.room) != room_id(room):
            continue
        gap = abs(close.at_ms - at_ms)
        if gap > tolerance_ms:
            continue
        rank = (gap, close.reason, close.session_id if close.session_id is not None else 0)
        if best is None or rank < best:
            best = rank
    return None if best is None else best[1]


def _compatible(c: CognitionWrite, w: ClockedWrite) -> bool:
    if c.tier != w.tier:
        return False
    if c.verb in ("note_create", "event_propose"):
        if not w.create or room_id(c.room) != room_id(w.room):
            return False
        if c.verb == "note_create":
            return c.anchor is not None and c.anchor == w.anchor
        return bool(c.title.strip()) and c.title.strip().casefold() == w.title.strip().casefold()
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
    clocked: Sequence[ClockedWrite], closes: Sequence[Close], tolerance_ms: int,
    since_ms: int, until_ms: int, explainable: bool = True, notes: tuple[str, ...] = (),
) -> TierResult:
    """Match one tier and split the result per writer. `cognition` and `clocked`
    are read over the window widened by the tolerance on both sides; only rows in
    `[since_ms, until_ms)` are counted (a pair counts when either side is)."""

    def inside(ms: int) -> bool:
        return since_ms <= ms < until_ms

    pairs, cog_left, clocked_left = match_writes(cognition, clocked, tolerance_ms=tolerance_ms)
    matched_by_writer: dict[str, int] = {}
    for c, w in pairs:
        if inside(c.at_ms) or inside(w.at_ms):
            matched_by_writer[w.writer] = matched_by_writer.get(w.writer, 0) + 1

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
        gaps_by_writer.setdefault(w.writer, []).append(Gap(
            side="clocked", tier=tier, writer=w.writer, id=w.id, room=w.room, at_ms=w.at_ms,
            key=w.key, keyless=w.keyless, refused_nearby=refused,
            closed_by=_explain(w.room, w.at_ms, closes, tolerance_ms) if explainable else None,
        ))
    seen = {w.writer for w in clocked} | set(matched_by_writer)
    names = list(EXPECTED_WRITERS[tier]) + sorted(seen - set(EXPECTED_WRITERS[tier]))
    writers = tuple(
        WriterVerdict(
            writer=name, status="compared", matched=matched_by_writer.get(name, 0),
            clocked_only=tuple(gaps_by_writer.get(name, ())),
        )
        for name in names
    )
    return TierResult(
        tier=tier, status="compared", writers=writers, cognition_only=cognition_only,
        refused=sum(1 for r in refusals if r.tier == tier and inside(r.at_ms)), notes=notes,
    )


def act_tier(
    tier: str, result: CompareResult, *, closes: Sequence[Close], tolerance_ms: int,
    extra_notes: tuple[str, ...] = (),
) -> TierResult:
    """The act tiers, from `nora.domain.cognition_compare.compare`: matched by
    key inside the window, no time tolerance on the match (the tolerance only
    decides whether a close explains a clocked-only row). A live row naming no
    note and no event is clocked-only and `keyless`."""
    writer = EXPECTED_WRITERS[tier][0]
    gaps = tuple(
        Gap(
            side="clocked", tier=tier, writer=writer, id=p.reminder_id,
            room=f"{p.platform}:{p.conversation_id}", at_ms=p.at_ms,
            key=p.note_id or p.event_id, keyless=not p.keys,
            closed_by=_explain(p.conversation_id, p.at_ms, closes, tolerance_ms),
        )
        for p in (*result.proactive_only, *result.unkeyed)
    )
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
    """One line per clocked writer. A count, never a pass or a fail."""
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
                f"{w.writer}: {w.matched} matched, {cog}, {len(w.clocked_only)} clocked-only "
                f"({w.closed} closed, {w.open} open)"
            )
    return lines


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _gap_json(g: Gap, directory: RoomDirectory) -> dict[str, object]:
    return {
        "side": g.side, "tier": g.tier, "writer": g.writer or None, "id": g.id,
        "verb": g.verb or None, "key": g.key or None, "room": directory.label(g.room),
        "at": _iso(g.at_ms), "session_id": g.session_id, "closed_by": g.closed_by,
        "refused_nearby": g.refused_nearby, "keyless": g.keyless,
    }


def report_json(report: ShadowReport, directory: RoomDirectory) -> dict[str, object]:
    """The machine-readable form. `None` counts mean ERROR, never zero."""
    return {
        "since": _iso(report.since_ms), "until": _iso(report.until_ms),
        "tolerance_seconds": report.tolerance_ms // 1000, "unit": report.unit,
        "status": "error" if report.any_error else "compared",
        "verdicts": verdict_lines(report),
        "tiers": [
            {
                "tier": t.tier, "status": t.status, "error": t.error, "refused": t.refused,
                "cognition_only": [_gap_json(g, directory) for g in t.cognition_only],
                "notes": list(t.notes),
                "writers": [
                    {
                        "writer": w.writer, "status": w.status, "matched": w.matched,
                        "clocked_only": None if w.status == "error" else len(w.clocked_only),
                        "closed": w.closed, "open": w.open,
                        "unmatched": [_gap_json(g, directory) for g in w.clocked_only],
                    }
                    for w in t.writers
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
        "verdicts (counts only; the gate is yours):",
    ]
    lines += [f"  {line}" for line in verdict_lines(report)]
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
                    + (f"  closed_by={g.closed_by}" if g.closed_by else "  OPEN")
                    + (f"  refused_nearby={g.refused_nearby}" if g.refused_nearby else "")
                    + ("  KEYLESS" if g.keyless else "")
                )
    return lines
