"""`nora cognition compare --since <ISO>` — reads both sides of
`nora.domain.cognition_compare.compare` over a window and prints matched /
cognition-only / proactive-only, then the notes that say what those three
counts leave out (cancelled, moved, unkeyed, unkeyable) (migration step 3a-ii).

**Read-only, and off the live path.** This module runs a plain SQL SELECT
against `cognition_remind_mirror` (cognition's held mirror) and `reminders`
(the live proactive pass's own decisions, `kind = 'proactive'`) — an
operator command, not a scheduled process, matching `nora.app.assess_run`'s
own shape for "read what already happened and report it".
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.cognition_compare import (
    CompareResult,
    MirroredAct,
    MirrorRow,
    ProactiveAct,
    compare,
    resolve_chains,
)
from nora.domain.cognition_shadow_compare import (
    CLOSING_REASONS,
    ClockedWrite,
    Close,
    CloseLedger,
    CognitionWrite,
    Refusal,
    RoomDirectory,
    ShadowReport,
    TierResult,
    act_tier,
    compare_tier,
    room_id,
    standing_writer,
)
from nora.infra.sql.schema import (
    COGNITION_REMIND_MIRROR,
    COGNITION_SESSION,
    COGNITION_SESSION_CLOSE,
    COGNITION_WOULD_WRITE,
    CULTURE_REVISION,
    DECISION_LOG,
    EVENT_REVISION,
    EVENTS,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
    ROW_HISTORY,
    STANDING_REVISION,
)


@dataclass(frozen=True)
class CompareWindow:
    since_ms: int
    until_ms: int


async def read_mirrored(engine: AsyncEngine, window: CompareWindow) -> tuple[MirroredAct, ...]:
    """The mirrored `remind` chains that BEGAN in the window, each at its
    latest state: the moves and cancels that followed (up to the window's end)
    are read too, so a cancelled remind is not counted as an act and a moved
    one is read at its latest time."""
    t = COGNITION_REMIND_MIRROR
    began = sa.select(t.c.reminder_ref).where(
        t.c.verb == "remind", t.c.outcome == "recorded",
        t.c.recorded_at >= _at(window.since_ms), t.c.recorded_at < _at(window.until_ms),
    )
    stmt = sa.select(
        t.c.verb, t.c.reminder_ref, t.c.platform, t.c.conversation_id, t.c.subject_rows,
        t.c.at_ms, t.c.recorded_at, t.c.outcome,
    ).where(
        t.c.reminder_ref.in_(began), t.c.recorded_at < _at(window.until_ms),
        t.c.verb.in_(("remind", "remind_move", "remind_cancel")),
    ).order_by(t.c.id)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return resolve_chains([
        MirrorRow(
            verb=r.verb, reminder_ref=r.reminder_ref or "", platform=r.platform,
            conversation_id=r.conversation_id, subject_rows=tuple(r.subject_rows or ()),
            at_ms=int(r.at_ms) if r.at_ms is not None else int(r.recorded_at.timestamp() * 1000),
            outcome=r.outcome,
        )
        for r in rows
    ])


async def read_proactive(engine: AsyncEngine, window: CompareWindow) -> tuple[ProactiveAct, ...]:
    """Every proactive reminder in the window, keyed by the note id and the
    event id its payload names. One naming neither is KEPT (`unkeyed`), never
    dropped."""
    t = REMINDERS
    stmt = sa.select(t.c.id, t.c.platform, t.c.conversation_id, t.c.payload, t.c.created_at).where(
        t.c.kind == "proactive", t.c.created_at >= _at(window.since_ms),
        t.c.created_at < _at(window.until_ms),
    ).order_by(t.c.created_at)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    out = []
    for r in rows:
        payload = r.payload or {}
        note_id, event_id = payload.get("note_id"), payload.get("event_id")
        out.append(ProactiveAct(
            reminder_id=str(r.id), platform=r.platform, conversation_id=r.conversation_id,
            note_id=note_id if isinstance(note_id, str) else "",
            event_id=event_id if isinstance(event_id, str) else "",
            at_ms=int(r.created_at.timestamp() * 1000),
        ))
    return tuple(out)


async def run_compare(engine: AsyncEngine, window: CompareWindow) -> CompareResult:
    mirrored, proactive = await read_mirrored(engine, window), await read_proactive(engine, window)
    return compare(mirrored, proactive)


def render(result: CompareResult, window: CompareWindow) -> list[str]:
    counts = result.counts
    lines = [
        f"cognition compare {_iso(window.since_ms)} .. {_iso(window.until_ms)}",
        f"  matched:         {counts['matched']}",
        f"  cognition_only:  {counts['cognition_only']}",
        f"  proactive_only:  {counts['proactive_only']}",
    ]
    for m in result.cognition_only[:10]:
        lines.append(
            f"    cognition_only  {m.platform}:{m.conversation_id}  "
            f"subjects={list(m.subject_rows)}  ref={m.reminder_ref}"
        )
    for p in result.proactive_only[:10]:
        lines.append(
            f"    proactive_only  {p.platform}:{p.conversation_id}  "
            f"note={p.note_id or '-'} event={p.event_id or '-'}  id={p.reminder_id}"
        )
    lines.append("  notes (counted apart, or unmatched by construction):")
    lines.append(
        f"    cancelled:  {len(result.cancelled)} mirrored remind(s) cognition cancelled "
        "before the window closed; in neither matched nor cognition_only"
    )
    lines.append(
        f"    moved:      {len(result.moved)} counted mirrored remind(s) were moved; "
        "each is read at its latest time"
    )
    lines.append(
        f"    unkeyed:    {len(result.unkeyed)} proactive reminder(s) name neither a note "
        "nor an event; they cannot be matched and are not in proactive_only"
    )
    lines.append(
        f"    unkeyable:  {len(result.unkeyable)} of cognition_only cite no note or event "
        "row, so they cannot match a proactive reminder by construction"
    )
    for p in result.unkeyed[:10]:
        lines.append(f"      unkeyed  {p.platform}:{p.conversation_id}  id={p.reminder_id}")
    return lines


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _iso(ms: int) -> str:
    return _at(ms).isoformat()


# ---------------------------------------------------------------------------
# The shadow week, per tier and per clocked writer (review B1, D5). Every read
# below is a plain SELECT; nothing here writes.
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _obj(raw: Any) -> dict[str, Any]:
    """A `jsonb` column as a dict. asyncpg returns text without a codec, and the
    session tables hold some rows written as a JSON string inside the jsonb
    (m1, 2026-09-30): both read."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _anchor(date: Any, entities: Any) -> tuple[int | None, tuple[str, ...]]:
    return (
        int(date) if isinstance(date, int) else None,
        tuple(sorted({str(e) for e in (entities or ())})),
    )


async def read_would_writes(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None,
) -> tuple[list[CognitionWrite], list[Refusal]]:
    t = COGNITION_WOULD_WRITE
    stmt = sa.select(
        t.c.id, t.c.session_id, t.c.tier, t.c.verb, t.c.platform, t.c.conversation_id,
        t.c.args, t.c.outcome, t.c.candidate, t.c.recorded_at,
    ).where(t.c.recorded_at >= _at(since_ms), t.c.recorded_at < _at(until_ms))
    if unit is not None:
        stmt = stmt.where(t.c.unit == unit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt.order_by(t.c.id))).all()
    writes: list[CognitionWrite] = []
    refusals: list[Refusal] = []
    for r in rows:
        at = _ms(r.recorded_at)
        if r.outcome != "would_commit":
            refusals.append(Refusal(tier=r.tier, room=r.conversation_id, at_ms=at))
            continue
        cand, args = _obj(r.candidate), _obj(r.args)
        key, anchor, title = "", None, ""
        if r.verb == "note_update":
            key = str(cand.get("note_id") or args.get("note_id") or "")
        elif r.verb in ("event_revise", "event_set_status"):
            key = str(cand.get("id") or "")
        elif r.verb == "note_create":
            anchor = _anchor(cand.get("anchor_date"), cand.get("anchor_entity_ids"))
            title = str(cand.get("title") or "")
        elif r.verb == "event_propose":
            title = str(cand.get("title") or "")
        writes.append(CognitionWrite(
            id=str(r.id), session_id=r.session_id, tier=r.tier, verb=r.verb,
            room=r.conversation_id, at_ms=at, key=key, anchor=anchor, title=title,
        ))
    return writes, refusals


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _cited(item: dict[str, Any]) -> frozenset[str]:
    """The ids an agenda item names: the tokens of its summary and cues, and any
    `note_id` / `event_id` field. The agenda records no id field of its own today,
    so this is what the text says; a close that names nothing explains only
    rows that carry no id."""
    texts = [str(item.get("summary") or ""), *(str(c) for c in item.get("cues") or ())]
    texts += [str(item[k]) for k in ("note_id", "event_id") if item.get(k)]
    return frozenset(tok for text in texts for tok in _TOKEN.findall(text))


async def read_closes(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None,
) -> tuple[list[Close], int]:
    """The explaining closes, and how many had an agenda this could not read
    (they explain nothing)."""
    c, s = COGNITION_SESSION_CLOSE, COGNITION_SESSION
    stmt = sa.select(
        c.c.session_id, c.c.item_index, c.c.kind, c.c.reason, c.c.recorded_at, s.c.agenda,
    ).join(
        s, s.c.id == c.c.session_id,
    ).where(
        c.c.reason.in_(CLOSING_REASONS),
        c.c.recorded_at >= _at(since_ms), c.c.recorded_at < _at(until_ms),
    )
    if unit is not None:
        stmt = stmt.where(s.c.unit == unit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt.order_by(c.c.id))).all()
    closes: list[Close] = []
    unreadable = 0
    for r in rows:
        items = _obj(r.agenda).get("items")
        item = (
            items[r.item_index]
            if isinstance(items, list) and 0 <= r.item_index < len(items) else None
        )
        cid = item.get("conversation_id") if isinstance(item, dict) else None
        if not isinstance(cid, str) or not cid:
            unreadable += 1
            continue
        closes.append(Close(
            session_id=r.session_id, room=cid, reason=r.reason, at_ms=_ms(r.recorded_at),
            kind=r.kind, cited=_cited(item) if isinstance(item, dict) else frozenset(),
        ))
    return closes, unreadable


async def read_clocked_notes(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None,
) -> list[ClockedWrite]:
    """Note revisions by a clocked adaptor (`established_by = dream:<writer>:...`).
    The tag path (`tag:`) and live cognition (`cognition:`) are not clocked
    writers and are not read."""
    rev = NOTE_REVISION
    stmt = sa.select(
        rev.c.id, rev.c.note_id, rev.c.parent_revision_id, rev.c.recorded_at, rev.c.established_by,
        NOTE.c.established_in, NOTE.c.anchor_date, NOTE.c.anchor_entity_ids, NOTE.c.title,
    ).join(NOTE, NOTE.c.id == rev.c.note_id).where(
        rev.c.recorded_at >= _at(since_ms), rev.c.recorded_at < _at(until_ms),
        rev.c.established_by.like("dream:%"),
    )
    if unit is not None:
        stmt = stmt.where(NOTE.c.unit == unit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt.order_by(rev.c.recorded_at, rev.c.id))).all()
    as_of = await _notes_as_of(engine, [(str(r.note_id), r.recorded_at) for r in rows
                                        if r.parent_revision_id is None])
    out: list[ClockedWrite] = []
    for r in rows:
        create = r.parent_revision_id is None
        anchor, title = _anchor(r.anchor_date, r.anchor_entity_ids), r.title
        if create:
            # The note row is the note NOW; a curation since may have moved its
            # anchor or retitled it. A create is matched on what it wrote.
            image = as_of.get((str(r.note_id), r.recorded_at))
            if image is not None:
                anchor = _anchor(image.get("anchor_date"), image.get("anchor_entity_ids"))
                title = str(image.get("title") or "")
        out.append(ClockedWrite(
            writer=r.established_by.split(":")[1], tier="note", id=str(r.id),
            room=r.established_in, at_ms=_ms(r.recorded_at), key=str(r.note_id),
            create=create, anchor=anchor, title=title,
        ))
    return out


async def _notes_as_of(
    engine: AsyncEngine, wanted: list[tuple[str, datetime]],
) -> dict[tuple[str, datetime], dict[str, Any]]:
    """Each wanted note's image in `row_history` AS OF the given time: the latest
    version recorded at or before it, else the OLDEST version held (the closest
    thing to the state it was created in), else absent (the caller then keeps the
    current row)."""
    if not wanted:
        return {}
    h = ROW_HISTORY
    ids = sorted({n for n, _ in wanted})
    stmt = sa.select(h.c.row_key, h.c.image, h.c.recorded_at).where(
        h.c.table_name == "note", h.c.row_key["id"].astext.in_(ids),
    ).order_by(h.c.recorded_at, h.c.id)
    async with engine.connect() as conn:
        versions = (await conn.execute(stmt)).all()
    by_note: dict[str, list[Any]] = {}
    for v in versions:
        by_note.setdefault(str(_obj(v.row_key).get("id")), []).append(v)
    out: dict[tuple[str, datetime], dict[str, Any]] = {}
    for note_id, at in wanted:
        chain = by_note.get(note_id)
        if not chain:
            continue
        chosen = [v for v in chain if v.recorded_at <= at]
        out[(note_id, at)] = _obj((chosen[-1] if chosen else chain[0]).image)
    return out


async def read_clocked_events(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, unit: str | None,
) -> list[ClockedWrite]:
    rev = EVENT_REVISION
    stmt = sa.select(
        rev.c.id, rev.c.event_id, rev.c.parent_revision_id, rev.c.title, rev.c.written_by,
        rev.c.recorded_at, EVENTS.c.conversation_id,
    ).join(EVENTS, EVENTS.c.id == rev.c.event_id).where(
        rev.c.recorded_at >= _at(since_ms), rev.c.recorded_at < _at(until_ms),
        rev.c.written_by != "backfill",
    )
    if unit is not None:
        stmt = stmt.where(EVENTS.c.unit_id == unit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt.order_by(rev.c.recorded_at, rev.c.id))).all()
    return [
        ClockedWrite(
            writer=f"event:{r.written_by}", tier="event", id=str(r.id), room=r.conversation_id,
            at_ms=_ms(r.recorded_at), key=str(r.event_id), create=r.parent_revision_id is None,
            title=r.title,
        )
        for r in rows
    ]


async def read_clocked_standing(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, rooms: frozenset[str] | None,
) -> list[ClockedWrite]:
    t = STANDING_REVISION
    previous = sa.func.lag(t.c.fold_count).over(
        partition_by=(t.c.platform, t.c.conversation_id), order_by=(t.c.recorded_at, t.c.id),
    )
    inner = sa.select(
        t.c.id, t.c.platform, t.c.conversation_id, t.c.fold_count, t.c.source, t.c.recorded_at,
        previous.label("previous_fold_count"),
    ).subquery()
    stmt = sa.select(inner).where(
        inner.c.source == "fold",
        inner.c.recorded_at >= _at(since_ms), inner.c.recorded_at < _at(until_ms),
    ).order_by(inner.c.recorded_at, inner.c.id)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [
        ClockedWrite(
            writer=standing_writer(
                fold_count=r.fold_count, previous_fold_count=r.previous_fold_count,
            ),
            tier="standing", id=str(r.id), room=r.conversation_id, at_ms=_ms(r.recorded_at),
        )
        for r in rows if rooms is None or room_id(r.conversation_id) in rooms
    ]


async def read_clocked_culture(
    engine: AsyncEngine, *, since_ms: int, until_ms: int, rooms: frozenset[str] | None,
) -> list[ClockedWrite]:
    t = CULTURE_REVISION
    stmt = sa.select(t.c.id, t.c.culture_key, t.c.recorded_at).where(
        t.c.recorded_at >= _at(since_ms), t.c.recorded_at < _at(until_ms),
    ).order_by(t.c.recorded_at, t.c.id)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [
        ClockedWrite(
            writer="culture", tier="culture", id=str(r.id), room=r.culture_key,
            at_ms=_ms(r.recorded_at),
        )
        for r in rows if rooms is None or room_id(r.culture_key) in rooms
    ]


async def read_proactive_decisions(
    engine: AsyncEngine, window: CompareWindow, *, unit: str | None,
) -> tuple[ProactiveAct, ...]:
    """`decision_log` rows where the proactive decide turn chose to remind."""
    t = DECISION_LOG
    stmt = sa.select(t.c.id, t.c.platform, t.c.conversation_id, t.c.note_id, t.c.recorded_at).where(
        t.c.writer == "proactive_decide", t.c.verdict == "remind",
        t.c.recorded_at >= _at(window.since_ms), t.c.recorded_at < _at(window.until_ms),
    )
    if unit is not None:
        stmt = stmt.where(t.c.unit == unit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt.order_by(t.c.recorded_at, t.c.id))).all()
    return tuple(
        ProactiveAct(
            reminder_id=str(r.id), platform=r.platform, conversation_id=r.conversation_id,
            note_id=r.note_id or "", at_ms=_ms(r.recorded_at),
        )
        for r in rows
    )


async def _try(call: Awaitable[_T]) -> tuple[_T | None, str | None]:
    """Fail closed: a source that cannot be read is an ERROR carrying only the
    exception's class name (a message can quote a row), never an empty read."""
    try:
        return await call, None
    except Exception as exc:  # noqa: BLE001 - any failure is this source's ERROR
        return None, type(exc).__name__


def _in_rooms(act: Any, rooms: frozenset[str] | None) -> bool:
    return rooms is None or room_id(act.conversation_id) in rooms


async def run_shadow_compare(
    engine: AsyncEngine, window: CompareWindow, *, tolerance_ms: int, unit: str | None,
    directory: RoomDirectory,
) -> ShadowReport:
    rooms = directory.rooms_of(unit) if unit is not None else None
    lo, hi = window.since_ms - tolerance_ms, window.until_ms + tolerance_ms
    common: dict[str, Any] = {"tolerance_ms": tolerance_ms, "since_ms": window.since_ms,
                              "until_ms": window.until_ms}
    written, err_written = await _try(
        read_would_writes(engine, since_ms=lo, until_ms=hi, unit=unit),
    )
    closed, err_closed = await _try(read_closes(engine, since_ms=lo, until_ms=hi, unit=unit))
    err_cognition = err_written or err_closed
    closes: list[Close] = closed[0] if closed else []
    unreadable = closed[1] if closed else 0
    close_note = (
        (f"{unreadable} close row(s) had an unreadable agenda and explain nothing",)
        if unreadable else ()
    )
    tiers: list[TierResult] = []
    ledger = CloseLedger(closes, tolerance_ms)

    readers: dict[str, Callable[[], Awaitable[list[ClockedWrite]]]] = {
        "note": lambda: read_clocked_notes(engine, since_ms=lo, until_ms=hi, unit=unit),
        "event": lambda: read_clocked_events(engine, since_ms=lo, until_ms=hi, unit=unit),
        "standing": lambda: read_clocked_standing(engine, since_ms=lo, until_ms=hi, rooms=rooms),
    }
    for tier, reader in readers.items():
        clocked, err_clocked = await _try(reader())
        error = err_cognition or err_clocked
        if error is not None or written is None or clocked is None:
            tiers.append(TierResult.errored(tier, error or "unreadable"))
            continue
        tiers.append(compare_tier(
            tier, cognition=[c for c in written[0] if c.tier == tier], refusals=written[1],
            clocked=clocked, closes=ledger, notes=close_note, **common,
        ))

    culture, err_culture = await _try(
        read_clocked_culture(engine, since_ms=lo, until_ms=hi, rooms=rooms),
    )
    if err_culture is not None or culture is None:
        tiers.append(TierResult.errored("culture", err_culture or "unreadable"))
    else:
        tiers.append(compare_tier(
            "culture", cognition=[], refusals=[], clocked=culture, closes=[], explainable=False,
            notes=(
                "cognition has no culture verb: every clocked write is clocked-only "
                "by construction",
            ),
            **common,
        ))

    mirrored, err_mirror = await _try(read_mirrored(engine, window))
    proactive, err_proactive = await _try(read_proactive(engine, window))
    decisions, err_decisions = await _try(read_proactive_decisions(engine, window, unit=unit))
    mirrored = tuple(m for m in (mirrored or ()) if _in_rooms(m, rooms))
    for tier, live, err_live in (
        ("act", proactive, err_proactive), ("act-decision", decisions, err_decisions),
    ):
        error = err_mirror or err_live or err_closed
        if error is not None or live is None:
            tiers.append(TierResult.errored(tier, error or "unreadable"))
            continue
        tiers.append(act_tier(
            tier, compare(mirrored, tuple(a for a in live if _in_rooms(a, rooms))),
            closes=ledger, tolerance_ms=tolerance_ms, extra_notes=close_note,
        ))
    return ShadowReport(
        since_ms=window.since_ms, until_ms=window.until_ms, tolerance_ms=tolerance_ms,
        unit=unit, tiers=tuple(tiers),
    )
