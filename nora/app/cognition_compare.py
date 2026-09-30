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

from dataclasses import dataclass
from datetime import UTC, datetime

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
from nora.infra.sql.schema import COGNITION_REMIND_MIRROR, REMINDERS


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
