"""Read and retire graph lessons and report tensions."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

import nora.memory as memory
from nora.agent.egress import LeakGuard
from nora.app.wake_rooms import WakeRoomSlots
from nora.doctrine.loader import Doctrine
from nora.domain.doctrine_check import Tension
from nora.domain.prompt import one_line
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, LESSON_TALLY
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.infra.sql.engine import create_readonly_sql_engine, create_sql_engine
from nora.memory import PassContext, Refusal, Retire, Scope, Unavailable
from nora.persons import Persons
from nora.policy import Policy


class GraphLessonError(Exception):
    """A graph lesson command could not be applied."""


def _live() -> sa.ColumnElement[bool]:
    c = CLAIM.c
    return sa.and_(c.state == "live", sa.or_(c.valid_to.is_(None), c.valid_to > sa.func.now()))


async def _read_lessons(
    url: str, where: sa.ColumnElement[bool], *, only_one: str | None = None,
) -> int:
    c, t, e = CLAIM.c, LESSON_TALLY.c, EDGE.c
    evidence = sa.case((c.state == "retired", c.replaces), else_=c.id)
    rows = sa.select(
        c.id, c.unit, c.state, c.content, t.support, t.contradiction,
        evidence.label("evidence"),
    ).select_from(CLAIM.outerjoin(
        LESSON_TALLY, sa.and_(
            t.unit == c.unit, t.claim_id == evidence, visible_to_reads(LESSON_TALLY),
        ),
    )).where(
        c.part == "lesson", visible_to_reads(CLAIM), where,
    ).order_by(c.asserted_at.desc(), c.id)
    engine = create_readonly_sql_engine(url)
    try:
        async with engine.connect() as conn:
            lessons = (await conn.execute(rows)).all()
            if only_one is not None and len(lessons) != 1:
                raise GraphLessonError(only_one)
            cites = await conn.execute(sa.select(e.unit, e.src_id, e.dst_id).where(
                sa.tuple_(e.unit, e.src_id).in_([(x.unit, x.evidence) for x in lessons]),
                e.type == "cites", e.state == "live", visible_to_reads(EDGE),
            ).order_by(e.dst_id))
            citations: dict[tuple[str, str], list[str]] = {}
            for unit, source, target in cites:
                citations.setdefault((unit, source), []).append(target)
    finally:
        await engine.dispose()
    for row in lessons:
        support = row.support if row.support is not None else 0
        contradiction = row.contradiction if row.contradiction is not None else 0
        print(f"{row.id} [{row.state}] unit={row.unit} "
              f"support={support} contradiction={contradiction}")
        print(f"  {row.content}")
        print("  citations: " + ", ".join(citations.get((row.unit, row.evidence), ())))
    return len(lessons)


async def list_lessons(url: str, unit: str, *, everything: bool) -> int:
    c = CLAIM.c
    found = await _read_lessons(url, sa.and_(
        c.unit == unit, sa.or_(_live(), c.state == "retired") if everything else _live(),
    ))
    if not found:
        print(f"No graph lessons in {unit}.")
    return 0


async def show_lesson(url: str, policy: Policy, graph_id: str) -> int:
    """One graph lesson in a unit the operator's policy names, live or retired, with its
    tally and citations; an id that is absent, outside policy or in two units is refused."""
    c = CLAIM.c
    await _read_lessons(url, sa.and_(
        c.unit.in_(policy.unit_workspaces()), c.id == graph_id,
        sa.or_(_live(), c.state == "retired"),
    ), only_one=f"no unique graph lesson: {graph_id}")
    return 0


async def retire_lesson(
    url: str, policy: Policy, graph_id: str, *, apply: bool, why: str,
) -> int:
    c = CLAIM.c
    engine = create_sql_engine(url) if apply else create_readonly_sql_engine(url)
    try:
        async with engine.connect() as conn:
            units = (await conn.execute(sa.select(c.unit).where(
                c.unit.in_(policy.unit_workspaces()), c.id == graph_id, c.part == "lesson",
                _live(), visible_to_reads(CLAIM),
            ).limit(2))).scalars().all()
        if len(units) != 1:
            raise GraphLessonError(f"no unique live graph lesson: {graph_id}")
        [unit] = units
        if not apply:
            print(f"Would retire {graph_id} in {unit}; nothing written.")
            return 0
        result = await _retire(engine, policy, unit, graph_id)
    finally:
        await engine.dispose()
    print(json.dumps({"status": "ok", "id": result, "base": graph_id, "why": why}))
    return 0


async def _retire(engine: AsyncEngine, policy: Policy, unit: str, graph_id: str) -> str:
    slots = WakeRoomSlots(policy, ConversationSlots(maxlen=1, spawn=asyncio.create_task))
    guard = LeakGuard(Doctrine((), "", "", "", "", 0), "")
    admission = memory.wire.admission(engine, Persons({}, {}), policy, guard,
                               content_checks=False, room_slots=slots)
    context = PassContext(
        unit, None, False, "operator", "operator", f"operator:{uuid4()}",
        "", "", "", "", "", "", "", "",
    )
    retire = Retire(
        id=f"claim:{uuid4().hex}", base=graph_id, basis="inferred",
        scope=Scope(unit, exposure=None), reasoning="deduced", confidence="high",
        expected_kind="claim",
    )
    [outcome] = (await admission.admit((retire,), context, (graph_id,))).outcomes
    if isinstance(outcome, Refusal):
        raise GraphLessonError(f"graph lesson retirement refused: {outcome.reason}")
    if isinstance(outcome, Unavailable):
        raise GraphLessonError("graph lesson retirement unavailable")
    return str(outcome.id)


def render_tensions(tensions: tuple[Tension, ...]) -> list[str]:
    """Each refused lesson, the law rule it pulled against (file and line), the
    check's reason and the evidence it cited, newest first."""
    if not tensions:
        return ["no tensions"]
    out: list[str] = []
    for t in tensions:
        out.append(f"{t.id}  unit={t.unit}  written_at={t.written_at}  "
                   f"law={t.doctrine_file}:{t.doctrine_line}  check={t.check_model}")
        out.append(f"    would have said: {one_line(t.would_have_said)}")
        out.append(f"    law: {one_line(t.doctrine_text)}")
        out.append(f"    why: {one_line(t.why)}")
        out.append(f"    cites: {', '.join(t.citations)}")
    return out
