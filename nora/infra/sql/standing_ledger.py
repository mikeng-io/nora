"""Ledger operations: lock, validate, compare and append in one transaction."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import ROW, Lineage, LineageEntry, RenderRoom, message, room_key
from nora.domain.models import TranscriptRow
from nora.domain.note_body import NoteBodyError, Shown, cited_ids_in, rollup_keys_in
from nora.domain.standing_ledger import LedgerEntry, LedgerKind, LedgerOp, LedgerResult, ThreadSpan
from nora.domain.standing_revise import Refusal, check_ledger_op
from nora.domain.turn_record import turn_handles_in
from nora.infra.sql.audit import transcript_row, transcript_select
from nora.infra.sql.event_evidence import rollup_lineage
from nora.infra.sql.schema import (
    LEDGER_COMMITMENT,
    LEDGER_COMMITMENT_REVISION,
    LEDGER_DECISION,
    LEDGER_DECISION_REVISION,
    LEDGER_THREAD,
    LEDGER_THREAD_REVISION,
    MESSAGES,
    NOTE,
)
from nora.infra.sql.tag_traces import body_turn_lineage

_TABLES = {
    "decision": (LEDGER_DECISION, LEDGER_DECISION_REVISION),
    "commitment": (LEDGER_COMMITMENT, LEDGER_COMMITMENT_REVISION),
    "thread": (LEDGER_THREAD, LEDGER_THREAD_REVISION),
}


def _entry(row: Any, kind: LedgerKind) -> LedgerEntry:
    values = dict(row._mapping)
    values.pop("parent_revision")
    values.pop("recorded_at", None)
    if "topic" in values:
        values["subject"] = values.pop("topic")
    if "made_at" in values:
        values["opened_at"] = values.pop("made_at")
    values["lineage"] = Lineage.from_json(values["lineage"])
    for key in ("exclude_persons", "owed_to", "note_ids", "decision_ids", "message_ids"):
        if key in values:
            values[key] = tuple(values[key])
    if "spans" in values:
        values["spans"] = tuple(ThreadSpan(**s) for s in values["spans"])
    return LedgerEntry(kind=kind, **values)


def _values(entry: LedgerEntry, table: sa.Table) -> dict[str, Any]:
    values = asdict(entry)
    values["lineage"] = entry.lineage.to_json()
    values["topic"] = entry.subject
    values["made_at"] = entry.opened_at
    values["parent_revision"] = entry.revision - 1 if entry.revision > 1 else None
    return {c.name: values[c.name] for c in table.columns if c.name in values}


def _origins(entries: tuple[LineageEntry, ...]) -> tuple[str, ...]:
    return tuple(
        origin
        for e in entries
        for origin in (
            *((e.origin,) if e.origin else ()),
            *_origins(e.reads),
        )
    )


async def _evidence(
    conn: AsyncConnection,
    entry: LedgerEntry,
    prior: LedgerEntry | None,
    target: RenderRoom,
) -> LedgerEntry:
    """Resolve citations from the source stores; writer-supplied lineage is never accepted."""
    ids = cited_ids_in(entry.body) | frozenset(entry.message_ids)
    rows = (
        (
            await conn.execute(
                sa.select(
                    MESSAGES.c.id,
                    MESSAGES.c.platform,
                    MESSAGES.c.conversation_id,
                    MESSAGES.c.ts,
                ).where(MESSAGES.c.id.in_(ids), MESSAGES.c.hidden_at.is_(None))
            )
        ).all()
        if ids
        else []
    )
    by_id = {r.id: r for r in rows}
    # Previously cited messages may have been pruned; their recorded lineage survives.
    carried = cited_ids_in(prior.body) | frozenset(prior.message_ids) if prior else frozenset()
    if ids - by_id.keys() - carried:
        raise NoteBodyError("unknown_id", "a cited message cannot be resolved")
    lineage = prior.lineage if prior else Lineage()
    lineage = lineage.union(
        Lineage.of({ROW: tuple(message(room_key(r.platform, r.conversation_id)) for r in rows)})
    )
    prior_body = prior.body if prior else ""
    for key in rollup_keys_in(entry.body) - rollup_keys_in(prior_body):
        reached = await rollup_lineage(conn, key=key, unit=entry.unit)
        if reached is None:
            raise NoteBodyError("unknown_rollup_key", "a cited Rollup cannot be resolved")
        lineage = lineage.union(reached)
    # Unchanged citations retain their recorded requirements after source retention.
    new_turns = (
        "Evidence ["
        + ", ".join(
            f"turn({h})" for h in sorted(turn_handles_in(entry.body) - turn_handles_in(prior_body))
        )
        + "]"
    )
    lineage = lineage.union(await body_turn_lineage(conn, body=new_turns, unit=entry.unit))
    for note_id in set(entry.note_ids) | ({entry.note_id} if entry.note_id else set()):
        note = (
            await conn.execute(
                sa.select(NOTE.c.lineage, NOTE.c.exclude_persons).where(
                    NOTE.c.id == note_id,
                    NOTE.c.unit == entry.unit,
                )
            )
        ).first()
        if note is None:
            raise NoteBodyError("unknown_id", "the linked note is not in this unit")
        lineage = lineage.union(
            Lineage.of(
                {
                    ROW: (
                        LineageEntry(
                            "perception",
                            reads=Lineage.from_json(note.lineage).section(ROW),
                            exclude_persons=tuple(note.exclude_persons),
                        ),
                    )
                }
            )
        )
    for decision_id in entry.decision_ids:
        decision = (
            await conn.execute(
                sa.select(LEDGER_DECISION.c.lineage).where(
                    LEDGER_DECISION.c.id == decision_id,
                    LEDGER_DECISION.c.unit == entry.unit,
                )
            )
        ).first()
        if decision is None:
            raise NoteBodyError("unknown_id", "the linked decision is not in this unit")
        lineage = lineage.union(Lineage.from_json(decision.lineage))
    # The policy is the unit boundary, including recursively cited turn requirements.
    if any(len(target.resolve(origin)) != 1 for origin in _origins(lineage.section(ROW))):
        raise NoteBodyError("unknown_id", "cited evidence belongs outside this unit")
    spans = prior.spans if prior else ()
    if entry.kind == "thread":
        grouped: dict[tuple[str, str], list[Any]] = {}
        for row in rows:
            grouped.setdefault((row.platform, row.conversation_id), []).append(row)
        previous = {(s.platform, s.conversation_id): s for s in spans}
        for (platform, conversation_id), members in grouped.items():
            old = previous.get((platform, conversation_id))
            first = min(members, key=lambda r: (r.ts, r.id))
            last = max(members, key=lambda r: (r.ts, r.id))
            previous[platform, conversation_id] = ThreadSpan(
                platform,
                conversation_id,
                old.first_id
                if old and (old.first_at, old.first_id) < (first.ts, first.id)
                else first.id,
                old.last_id if old and (old.last_at, old.last_id) > (last.ts, last.id) else last.id,
                min(first.ts, old.first_at) if old else first.ts,
                max(last.ts, old.last_at) if old else last.ts,
            )
        spans = tuple(previous[k] for k in sorted(previous))
        if not spans:
            raise NoteBodyError("uncited", "a thread must point at messages")
    return replace(
        entry,
        lineage=lineage,
        spans=spans,
        as_of=max((r.ts for r in rows), default=entry.as_of),
        last_moved_at=max((s.last_at for s in spans), default=None),
    )


class SqlStandingLedger:
    def __init__(self, engine: AsyncEngine, *, metrics: Any) -> None:
        self._engine = engine
        self._metrics = metrics

    async def read(
        self,
        *,
        unit: str,
        platform: str,
        conversation_id: str,
    ) -> tuple[LedgerEntry, ...]:
        entries: list[LedgerEntry] = []
        async with self._engine.connect() as conn:
            for kind, (table, _) in _TABLES.items():
                rows = (
                    await conn.execute(
                        sa.select(table).where(
                            table.c.unit == unit,
                            sa.or_(
                                table.c.scope == "unit",
                                sa.and_(
                                    table.c.platform == platform,
                                    table.c.conversation_id == conversation_id,
                                ),
                            ),
                        )
                    )
                ).all()
                entries.extend(_entry(r, kind) for r in rows)  # type: ignore[arg-type]
        return tuple(entries)

    async def span_rows(
        self,
        span: ThreadSpan,
        *,
        limit: int,
        before_ts: int | None = None,
    ) -> list[TranscriptRow]:
        stmt = transcript_select().where(
            MESSAGES.c.platform == span.platform,
            MESSAGES.c.conversation_id == span.conversation_id,
            sa.tuple_(MESSAGES.c.ts, MESSAGES.c.id) >= (span.first_at, span.first_id),
            sa.tuple_(MESSAGES.c.ts, MESSAGES.c.id) <= (span.last_at, span.last_id),
        )
        if before_ts is not None:
            stmt = stmt.where(MESSAGES.c.ts < before_ts)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    stmt.order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc()).limit(limit + 1)
                )
            ).all()
        return [transcript_row(r) for r in reversed(rows)]

    async def history(self, *, unit: str, kind: LedgerKind, id: str) -> tuple[LedgerEntry, ...]:
        table = _TABLES[kind][1]
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(table)
                    .where(
                        table.c.unit == unit,
                        table.c.id == id,
                    )
                    .order_by(table.c.revision)
                )
            ).all()
        return tuple(_entry(r, kind) for r in rows)

    def _refused(self, reason: Refusal) -> LedgerResult:
        self._metrics.inc(
            "nora_cognition_refused_total", verb="standing_revise", rule=reason.reason
        )
        return LedgerResult(refusal=reason)

    async def apply(
        self,
        op: LedgerOp,
        *,
        target: RenderRoom,
        shown: Shown,
        now_ms: int,
    ) -> LedgerResult:
        if not isinstance(op, LedgerOp):
            return self._refused(Refusal("not_an_op", "Standing writes must name an operation"))
        if target.room is None or not target.room.unit or op.kind not in _TABLES:
            return self._refused(Refusal("bad_scope", "the writing room needs a known unit"))
        room = target.room
        table, revisions = _TABLES[op.kind]
        async with self._engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        sa.select(table)
                        .where(
                            table.c.id == op.id,
                            table.c.unit == room.unit,
                            sa.or_(
                                table.c.scope == "unit",
                                sa.and_(
                                    table.c.platform == room.platform,
                                    table.c.conversation_id == room.primary_id,
                                ),
                            ),
                        )
                        .with_for_update()
                    )
                ).first()
                if op.id
                else None
            )
            prior = _entry(row, op.kind) if row else None
            revised = check_ledger_op(
                op,
                prior,
                shown=shown,
                unit=room.unit,
                platform=room.platform,
                conversation_id=room.primary_id,
                now_ms=now_ms,
                id={"decision": "d", "commitment": "c", "thread": "th"}[op.kind] + uuid4().hex,
            )
            if isinstance(revised, Refusal):
                return self._refused(revised)
            known = {m.id for m in shown.rendered} | set(shown.carried)
            if set(op.message_ids) - known:
                return self._refused(Refusal("unknown_id", "thread messages were not shown"))
            try:
                revised = await _evidence(conn, revised, prior, target)
            except NoteBodyError as exc:
                return self._refused(
                    Refusal(exc.rule, "entry evidence could not be resolved in this unit")
                )
            await conn.execute(sa.insert(revisions).values(**_values(revised, revisions)))
            if prior:
                await conn.execute(
                    sa.update(table)
                    .where(table.c.id == prior.id)
                    .values(
                        **_values(revised, table),
                    )
                )
            else:
                await conn.execute(sa.insert(table).values(**_values(revised, table)))
        return LedgerResult(entry=revised)
