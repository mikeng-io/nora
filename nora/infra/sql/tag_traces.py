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
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.models import DeliveryStatus, RowEdit, TranscriptRow
from nora.domain.tag_trace import TagTrace
from nora.infra.sql.schema import TAG_TRACE


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
        return tuple(
            TagTrace(
                tag_id=r.tag_id,
                conversation_id=r.conversation_id,
                messages=tuple(r.messages),
                transcript=tuple(_json_to_row(row) for row in r.transcript),
                outcome=r.outcome,
                created_at=r.created_at,
            )
            for r in rows
        )
