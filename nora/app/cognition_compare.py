"""`nora cognition compare --since <ISO>` — reads both sides of
`nora.domain.cognition_compare.compare` over a window and prints matched /
cognition-only / proactive-only (migration step 3a-ii).

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

from nora.domain.cognition_compare import CompareResult, MirroredAct, ProactiveAct, compare
from nora.infra.sql.schema import COGNITION_REMIND_MIRROR, REMINDERS


@dataclass(frozen=True)
class CompareWindow:
    since_ms: int
    until_ms: int


async def read_mirrored(engine: AsyncEngine, window: CompareWindow) -> tuple[MirroredAct, ...]:
    t = COGNITION_REMIND_MIRROR
    stmt = sa.select(
        t.c.reminder_ref, t.c.platform, t.c.conversation_id, t.c.subject_rows, t.c.recorded_at,
    ).where(
        t.c.verb == "remind", t.c.outcome == "recorded",
        t.c.recorded_at >= _at(window.since_ms), t.c.recorded_at < _at(window.until_ms),
    ).order_by(t.c.recorded_at)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return tuple(
        MirroredAct(
            reminder_ref=r.reminder_ref or "", platform=r.platform,
            conversation_id=r.conversation_id, subject_rows=tuple(r.subject_rows or ()),
            at_ms=int(r.recorded_at.timestamp() * 1000),
        )
        for r in rows
    )


async def read_proactive(engine: AsyncEngine, window: CompareWindow) -> tuple[ProactiveAct, ...]:
    t = REMINDERS
    stmt = sa.select(t.c.id, t.c.platform, t.c.conversation_id, t.c.payload, t.c.created_at).where(
        t.c.kind == "proactive", t.c.created_at >= _at(window.since_ms),
        t.c.created_at < _at(window.until_ms),
    ).order_by(t.c.created_at)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    out = []
    for r in rows:
        note_id = (r.payload or {}).get("note_id")
        if not isinstance(note_id, str) or not note_id:
            continue  # a proactive row this comparison cannot key on
        out.append(ProactiveAct(
            reminder_id=str(r.id), platform=r.platform, conversation_id=r.conversation_id,
            note_id=note_id, at_ms=int(r.created_at.timestamp() * 1000),
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
            f"note={p.note_id}  id={p.reminder_id}"
        )
    return lines


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _iso(ms: int) -> str:
    return _at(ms).isoformat()
