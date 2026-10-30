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
)
from nora.domain.turn_record import (
    DisclosureRequirement,
    cited_turn_lineage,
    turn_handles_in,
)
from nora.infra.sql.schema import TAG_TRACE

TURN_FACTS_SQL = """
    WITH blocks AS MATERIALIZED (
        SELECT t.id AS trace_row_id, t.turn_id, t.tag_id,
               t.occasion, t.created_at, t.requirements,
               c.block, m.m_idx, c.b_idx
        FROM tag_trace t,
             LATERAL jsonb_array_elements(t.messages)
                WITH ORDINALITY m(msg, m_idx),
             LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(m.msg->'content') = 'array'
                     THEN m.msg->'content' ELSE '[]'::jsonb END
             ) WITH ORDINALITY c(block, b_idx)
        WHERE t.unit = :unit
          AND (c.block ? 'toolUse' OR c.block ? 'toolResult')
    ), uses AS (
        SELECT b.*, b.block->'toolUse'->>'name' AS name,
               b.block->'toolUse'->>'toolUseId' AS call_id,
               CASE WHEN jsonb_typeof(b.block->'toolUse'->'input') = 'object'
                    THEN b.block->'toolUse'->'input'
                    ELSE '{}'::jsonb END AS arguments
        FROM blocks b
        WHERE b.block ? 'toolUse'
          AND b.block->'toolUse'->>'name' = ANY(:names)
    ), paired AS (
        SELECT u.*, r.result,
               COALESCE(p.result_status, 'unknown') AS raw_status
        FROM uses u
        LEFT JOIN LATERAL (
            SELECT b.block->'toolResult' AS result
            FROM blocks b
            WHERE b.trace_row_id = u.trace_row_id
              AND b.block->'toolResult'->>'toolUseId' = u.call_id
            ORDER BY b.m_idx DESC, b.b_idx DESC
            LIMIT 1
        ) r ON true
        LEFT JOIN LATERAL (
            SELECT CASE
                WHEN jsonb_typeof(c.block->'json') = 'object'
                    THEN c.block->'json'->>'status'
                WHEN jsonb_typeof(c.block->'text') = 'string'
                    AND pg_input_is_valid(c.block->>'text', 'jsonb')
                    THEN (c.block->>'text')::jsonb->>'status'
                ELSE 'unknown'
            END AS result_status
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(r.result->'content') = 'array'
                     THEN r.result->'content' ELSE '[]'::jsonb END
            ) WITH ORDINALITY c(block, idx)
            WHERE jsonb_typeof(c.block->'json') = 'object'
               OR jsonb_typeof(c.block->'text') = 'string'
            ORDER BY c.idx
            LIMIT 1
        ) p ON true
    ), identities AS (
        SELECT p.*,
               (COALESCE(arguments->>'subject', '') <> ''
                AND (name = 'decision_conclude'
                     OR COALESCE(arguments->>'action', '') <> '')) AS named
        FROM paired p
    ), keyed AS (
        SELECT i.*,
               CASE WHEN named THEN 'subject' ELSE 'call' END AS key_kind,
               CASE WHEN named THEN arguments->>'subject' ELSE tag_id END AS key_subject,
               CASE WHEN named THEN COALESCE(arguments->>'action', '')
                    ELSE call_id END AS key_action,
               CASE WHEN raw_status IN ('ok', 'unresolved', 'refused', 'unavailable')
                    THEN raw_status ELSE 'unknown' END AS result_status
        FROM identities i
    ), latest AS (
        SELECT DISTINCT ON (name, key_kind, key_subject, key_action) *
        FROM keyed
        ORDER BY name, key_kind, key_subject, key_action,
                 created_at DESC, turn_id DESC NULLS LAST, m_idx DESC, b_idx DESC
    ), open_facts AS (
        SELECT *, 'open' AS bucket FROM latest
        WHERE result_status <> 'ok'
        ORDER BY created_at DESC, turn_id DESC NULLS LAST, m_idx DESC, b_idx DESC
        LIMIT :open_limit
    ), recent_facts AS (
        SELECT *, 'recent' AS bucket FROM keyed
        WHERE name = 'decision_conclude' AND result_status = 'ok'
        ORDER BY created_at DESC, turn_id DESC NULLS LAST, m_idx DESC, b_idx DESC
        LIMIT :recent_limit
    )
    SELECT * FROM open_facts UNION ALL SELECT * FROM recent_facts
"""


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
        """Reduce current identities in SQL before bounding transferred items.

        Recent accepted conclusions remain independent Record observations;
        they do not replace competing conclusions about the same subject.
        """
        if not tool_names:
            return TurnFacts()
        open_cap, recent_cap = max(0, open_cap), max(0, recent_cap)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sa.text(TURN_FACTS_SQL), {
                "unit": unit, "names": tuple(tool_names),
                "open_limit": open_cap + 1, "recent_limit": recent_cap,
            })).all()
        buckets: dict[str, list[FactItem]] = {"open": [], "recent": []}
        for r in rows:
            buckets[r.bucket].append(FactItem(
                name=r.name, call_id=r.call_id, arguments=r.arguments,
                result=r.result,
                requirements=tuple(
                    DisclosureRequirement.from_json(req)
                    for req in (r.requirements or {}).get(r.call_id, ())
                ),
                turn_id=r.turn_id,
                handle=f"t{r.turn_id}" if r.turn_id is not None else None,
                tag_id=r.tag_id, occasion=r.occasion, created_at=r.created_at,
                ordinal=(r.created_at, r.turn_id or 0, r.m_idx, r.b_idx),
            ))
        return TurnFacts(
            open_items=tuple(buckets["open"][:open_cap]),
            recent_items=tuple(buckets["recent"]),
            capped=len(buckets["open"]) > open_cap,
        )

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
