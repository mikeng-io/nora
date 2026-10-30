"""`SqlTagTraceStore` — write-once archival retention for `TagTrace`.

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlNoteStore`'s contract-suite affordance: no config, no
clock, no metrics. `created_at` arrives on the `TagTrace` itself, not on this
constructor.

**`transcript` needs its own (de)serialisation, not a bare `row.__dict__`.**
`TranscriptRow.delivery_status` is a `DeliveryStatus` Enum member, not a JSON
primitive — writing it verbatim into a JSONB column either fails the encode
outright or hands back a bare string on
read where `TagTrace.transcript` promises `TranscriptRow`s with a real Enum.
`_row_to_json`/`_json_to_row` are the one place that conversion happens, so
`save` and `recent` cannot drift into disagreeing about the shape.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import ROW, Lineage, unresolved
from nora.domain.models import DeliveryStatus, RowEdit, TranscriptRow
from nora.domain.note_body import NoteBodyError
from nora.domain.tag_trace import (
    FactItem,
    TagTrace,
    TurnFacts,
    reduce_turn_facts,
)
from nora.domain.turn_record import (
    DisclosureRequirement,
    cited_turn_lineage,
    turn_handles_in,
)
from nora.infra.sql.schema import TAG_TRACE


async def body_turn_lineage(
    conn: AsyncConnection, *, body: str, unit: str, strict: bool = True,
) -> Lineage:
    lineage = Lineage()
    for handle in sorted(turn_handles_in(body)):
        row = (await conn.execute(sa.select(TAG_TRACE).where(
            TAG_TRACE.c.turn_id == int(handle[1:]), TAG_TRACE.c.unit == unit,
        ))).first()
        try:
            reached = cited_turn_lineage(
                SqlTagTraceStore._trace(row) if row is not None else None, unit=unit,
            )
        except NoteBodyError:
            if strict:
                raise
            reached = Lineage.of({ROW: (unresolved(None),)})
        lineage = lineage.union(reached)
    return lineage


def _row_to_json(row: TranscriptRow) -> dict[str, Any]:
    d = dict(row.__dict__)
    d["delivery_status"] = row.delivery_status.value
    # An EDITED row carries a `RowEdit` — a dataclass, not a JSON primitive,
    # like `delivery_status`'s Enum. Left in, it would fail the JSONB encode
    # for every tag whose window held an edited message, and the save is
    # spawned, so the trace would vanish with only a log line.
    d["edit"] = dataclasses.asdict(row.edit) if row.edit is not None else None
    return d


def _json_to_row(d: dict[str, Any]) -> TranscriptRow:
    d = dict(d)
    d["delivery_status"] = DeliveryStatus(d["delivery_status"])
    edit = d.get("edit")
    d["edit"] = RowEdit(**edit) if isinstance(edit, dict) else None
    return TranscriptRow(**d)


class SqlTagTraceStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, trace: TagTrace) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(TAG_TRACE).values(
                id=str(uuid4()),
                tag_id=trace.tag_id,
                conversation_id=trace.conversation_id,
                messages=list(trace.messages),
                transcript=[_row_to_json(row) for row in trace.transcript],
                outcome=trace.outcome,
                created_at=trace.created_at,
                started_at=trace.started_at,
                occasion=trace.occasion, platform=trace.platform, unit=trace.unit,
                bound_rooms=list(trace.bound_rooms), requirements=trace.requirements,
                sent_message_ids=list(trace.sent_message_ids),
            ))

    async def recent(
        self, *, conversation_id: str, limit: int,
    ) -> tuple[TagTrace, ...]:
        """Newest-first within `conversation_id`, matching
        `idx_tag_trace_conversation_created`'s own column order — the same
        never-negative-`LIMIT` clamp every capped read in this package
        applies, since a negative `LIMIT` errors outright in Postgres rather
        than answering `()`."""
        if limit <= 0:
            return ()
        stmt = (
            sa.select(TAG_TRACE)
            .where(TAG_TRACE.c.conversation_id == conversation_id)
            .order_by(TAG_TRACE.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(self._trace(r) for r in rows)

    async def recent_unit(
        self, *, unit: str, limit: int,
    ) -> tuple[TagTrace, ...]:
        """Newest-first across the whole UNIT — every occasion (tag, fire,
        cognition session), every room — the unit-scoped read a shared view
        of producer conclusions needs; `recent` is one room only. Same
        never-negative-`LIMIT` clamp."""
        if limit <= 0:
            return ()
        stmt = (
            sa.select(TAG_TRACE)
            .where(TAG_TRACE.c.unit == unit)
            .order_by(TAG_TRACE.c.created_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(self._trace(r) for r in rows)

    async def turn_facts(
        self, *, unit: str, tool_names: Sequence[str],
        open_cap: int = 200, recent_cap: int = 20,
    ) -> TurnFacts:
        """The unit's CURRENT tool-fact projection, extracted structurally:
        lateral JSONB expansion pairs each `toolUse` with the `toolResult`
        sharing its `toolUseId` in the same trace, `DISTINCT ON` the
        (name, subject, action) key keeps only the NEWEST pair per key (a
        delivered `ok` closes its earlier unresolved, a different key's
        never does), so the transfer is bounded by distinct facts, not by
        the unit's history. The shared `reduce_turn_facts` then splits
        current-open vs accepted tail."""
        if not tool_names:
            return TurnFacts()
        stmt = sa.text("""
            WITH uses AS (
                SELECT t.id AS trace_row_id, t.turn_id, t.tag_id,
                       t.occasion, t.created_at, t.requirements,
                       c.block AS use_block, m.m_idx, c.b_idx
                FROM tag_trace t,
                     LATERAL jsonb_array_elements(t.messages)
                        WITH ORDINALITY m(msg, m_idx),
                     LATERAL jsonb_array_elements(m.msg->'content')
                        WITH ORDINALITY c(block, b_idx)
                WHERE t.unit = :unit AND c.block ? 'toolUse'
                  AND c.block->'toolUse'->>'name' = ANY(:names)
            ), paired AS (
                SELECT u.*, r.res_block,
                       CASE WHEN COALESCE(
                                NULLIF(u.use_block->'input'->>'subject',''),'')
                                 = ''
                            AND COALESCE(
                                NULLIF(u.use_block->'input'->>'action',''),'')
                                 = ''
                       THEN 'call:' ||
                            (u.use_block->'toolUse'->>'toolUseId')
                       ELSE COALESCE(u.use_block->'input'->>'subject','')
                            || chr(31) ||
                            COALESCE(u.use_block->'input'->>'action','')
                       END AS fkey
                FROM uses u
                LEFT JOIN LATERAL (
                    SELECT rb.block AS res_block
                    FROM tag_trace t2,
                         jsonb_array_elements(t2.messages) m2(msg),
                         jsonb_array_elements(m2.msg->'content') rb(block)
                    WHERE t2.id = u.trace_row_id
                      AND rb.block ? 'toolResult'
                      AND rb.block->'toolResult'->>'toolUseId'
                          = u.use_block->'toolUse'->>'toolUseId'
                    LIMIT 1
                ) r ON true
            )
            SELECT DISTINCT ON (
                use_block->'toolUse'->>'name', fkey
            )
                turn_id, tag_id, occasion, created_at, requirements,
                use_block, res_block, m_idx, b_idx
            FROM paired
            ORDER BY use_block->'toolUse'->>'name', fkey,
                     created_at DESC, turn_id DESC NULLS LAST,
                     m_idx DESC, b_idx DESC
        """)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                stmt, {"unit": unit, "names": tuple(tool_names)},
            )).all()
        items = [
            FactItem(
                name=r.use_block["toolUse"]["name"],
                call_id=r.use_block["toolUse"]["toolUseId"],
                arguments=r.use_block["toolUse"].get("input", {}),
                result=r.res_block["toolResult"] if r.res_block else None,
                requirements=tuple(
                    DisclosureRequirement.from_json(req)
                    for req in (r.requirements or {}).get(
                        r.use_block["toolUse"]["toolUseId"], ())
                ),
                turn_id=r.turn_id,
                handle=f"t{r.turn_id}" if r.turn_id is not None else None,
                tag_id=r.tag_id, occasion=r.occasion,
                created_at=r.created_at,
                ordinal=(r.created_at, r.turn_id or 0, r.m_idx, r.b_idx),
            )
            for r in rows
        ]
        return reduce_turn_facts(
            items, open_cap=open_cap, recent_cap=recent_cap)
    @staticmethod
    def _trace(r: Any) -> TagTrace:
        return TagTrace(
                tag_id=r.tag_id,
                conversation_id=r.conversation_id,
                messages=tuple(r.messages),
                transcript=tuple(_json_to_row(row) for row in r.transcript),
                outcome=r.outcome,
                created_at=r.created_at,
                started_at=r.started_at,
                occasion=r.occasion, platform=r.platform, unit=r.unit,
                bound_rooms=tuple(r.bound_rooms), requirements=r.requirements,
                sent_message_ids=tuple(r.sent_message_ids), turn_id=r.turn_id,
            )

    async def get(self, *, turn_id: int, unit: str) -> TagTrace | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(TAG_TRACE).where(
                TAG_TRACE.c.turn_id == turn_id, TAG_TRACE.c.unit == unit,
            ))).first()
        return self._trace(row) if row is not None else None
