"""`SqlDecisionLogStore` — the shared decision log's Postgres half.

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlStandingStore`/`SqlTagTraceStore`: no config, no
clock, no metrics. The clock is on the row; the bound and the fail-soft rule
are `nora.domain.decision_log.DecisionLogRecorder`'s, one layer up, because a
store that swallowed its own failures could not be contract-tested against a
double that raises.

**There is no `update`, no `delete` and no `ON CONFLICT` — only `append`, a
plain INSERT.** That absence IS the append-only property, the same way
`DreamSink` has no delete: a path that
does not exist cannot be taken by someone who has not read the docstring. A
second append for the same `id` raises `IntegrityError` from the primary key,
which is the correct outcome — it means a caller reused an id, and silently
replacing would lose a decision that happened.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.decision_log import RECENT_LIMIT, DecisionRow
from nora.infra.sql.schema import DECISION_LOG


def _row(record: sa.Row[tuple[Any, ...]]) -> DecisionRow:
    return DecisionRow(
        id=record.id,
        writer=record.writer,
        platform=record.platform,
        conversation_id=record.conversation_id,
        unit=record.unit,
        note_id=record.note_id,
        verdict=record.verdict,
        reason=record.reason,
        ts=record.ts,
    )


class SqlDecisionLogStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(self, *, row: DecisionRow) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(DECISION_LOG).values(
                id=row.id,
                writer=row.writer,
                platform=row.platform,
                conversation_id=row.conversation_id,
                unit=row.unit,
                note_id=row.note_id,
                verdict=row.verdict,
                reason=row.reason,
                ts=row.ts,
            ))

    async def recent(
        self, *, conversation_id: str, since_ms: int, limit: int = RECENT_LIMIT,
    ) -> tuple[DecisionRow, ...]:
        """One room's decisions in the window, the NEWEST `limit`, OLDEST-FIRST.

        **The two orderings are the whole read and they are opposite.** The
        query orders `ts DESC, id DESC` so `LIMIT` takes the newest rows —
        ordering ascending and limiting answers a busy room's OLDEST decisions,
        rendered under a «last 24 hours» note, with nothing saying the block is
        about yesterday. The rows are then reversed, because the block is read
        top-to-bottom beside a transcript that `RecordStore.recent` hands over
        oldest-first, and one document with two directions of time is exactly
        the input a temporal judgement cannot be made from.

        `idx_decision_log_conv_ts` is `(conversation_id, ts DESC, id DESC)` —
        the leading column, the ordering and the tie-break, so this is an index
        scan with a `LIMIT` on top rather than a sort.
        """
        return await self._read(DECISION_LOG.c.conversation_id == conversation_id,
                                since_ms=since_ms, limit=limit)

    async def recent_for_unit(
        self, *, unit: str, since_ms: int,
        writers: Sequence[str] = (), limit: int = RECENT_LIMIT,
    ) -> tuple[DecisionRow, ...]:
        """The same read across every room of one unit, served by
        `idx_decision_log_unit_ts`.

        **`writers` narrows in the WHERE, so `LIMIT` counts rows the caller can
        actually use.** Every reader excludes its own writer, and a busy room's
        dominant writer can out-produce the rest by an order of magnitude —
        filtered after the read, its rows fill the bound and the caller gets
        nothing. Empty means every writer.

        The `IN` sits after `unit` in the predicate and before the sort, so
        `idx_decision_log_unit_ts` still drives the scan; `writer` is a
        low-cardinality filter over what that index already ordered.
        """
        return await self._read(DECISION_LOG.c.unit == unit,
                                since_ms=since_ms, limit=limit, writers=writers)

    async def _read(
        self, scope: sa.ColumnElement[bool], *, since_ms: int, limit: int,
        writers: Sequence[str] = (),
    ) -> tuple[DecisionRow, ...]:
        where = [scope, DECISION_LOG.c.ts >= since_ms]
        if writers:
            where.append(DECISION_LOG.c.writer.in_(tuple(writers)))
        stmt = (
            sa.select(DECISION_LOG)
            .where(*where)
            .order_by(DECISION_LOG.c.ts.desc(), DECISION_LOG.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(_row(record) for record in reversed(rows))
