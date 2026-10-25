"""Durable Record arrival outbox and fenced, atomic batch publication.

No retrieval API and no index projection. All reads here are writer inputs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.message_readings import VERSION
from nora.domain.models import NormalizedMessage
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_EDIT, MESSAGES
from nora.infra.sql.schema import MESSAGE_READING as READING
from nora.infra.sql.schema import MESSAGE_READING_ATTEMPT as ATTEMPT
from nora.infra.sql.schema import MESSAGE_READING_CLAIM as CLAIM
from nora.infra.sql.schema import MESSAGE_READING_QUEUE as QUEUE


async def enqueue(
    conn: AsyncConnection,
    *,
    message_id: int,
    message: NormalizedMessage,
    unit: str,
    is_source: bool = True,
) -> None:
    """The audit INSERT's transaction; a replay never owes a second reading."""
    source_key = json.dumps(
        [message.message_id] if message.message_id else [message.sender_id, message.timestamp]
    )
    await conn.execute(
        insert(QUEUE)
        .values(
            message_id=message_id,
            platform=message.platform,
            origin_room=message.conversation_id,
            unit=unit,
            source_key=source_key,
            is_source=is_source,
        )
        .on_conflict_do_nothing()
    )


async def source_rows(conn: AsyncConnection, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    # Locks serialize publication with hide/edit/delete. Invalidation triggers
    # take the same source row lock before retiring the dependent reading.
    rows = (
        (
            await conn.execute(
                sa.select(MESSAGES)
                .where(
                    MESSAGES.c.id.in_(ids),
                    MESSAGES.c.hidden_at.is_(None),
                    sa.or_(MESSAGES.c.is_nora == 0, MESSAGES.c.delivery_status == "sent"),
                    ~sa.exists().where(MESSAGE_DELETE.c.message_id == MESSAGES.c.id),
                )
                .order_by(MESSAGES.c.id)
                .with_for_update(read=True)
            )
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        edit = (
            (
                await conn.execute(
                    sa.select(MESSAGE_EDIT)
                    .where(
                        MESSAGE_EDIT.c.message_id == row["id"],
                    )
                    .order_by(MESSAGE_EDIT.c.id.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        result.append(
            {
                "id": row["id"],
                "person_id": row["person_id"],
                "ts": row["ts"],
                "body": edit["body"] if edit else row["body"],
                "mentions": json.loads(
                    (edit["mention_persons_json"] if edit else row["mention_persons_json"]) or "[]"
                ),
                "revision": edit["id"] if edit else None,
                "is_nora": bool(row["is_nora"]),
            }
        )
    return result


class SqlMessageReadings:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def take_reading_batch(
        self,
        *,
        model: str,
        batch_size: int,
        context_size: int,
        lease_seconds: float,
        settle_seconds: float,
    ) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            # ponytail: one short transaction lock for batch formation, never a
            # model await. Per-room formation if worker contention is measured.
            await conn.execute(sa.text("SELECT pg_advisory_xact_lock(1290081)"))
            now = datetime.now(UTC)
            reading = (
                (
                    await conn.execute(
                        sa.select(READING)
                        .where(
                            READING.c.state == "pending",
                            READING.c.due_at <= now,
                        )
                        .order_by(READING.c.due_at, READING.c.id)
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if reading is None:
                first = (
                    (
                        await conn.execute(
                            sa.select(QUEUE)
                            .where(
                                QUEUE.c.reading_id.is_(None),
                                QUEUE.c.is_source.is_(True),
                                QUEUE.c.message_id.is_not(None),
                                QUEUE.c.queued_at <= now - timedelta(seconds=settle_seconds),
                            )
                            .order_by(QUEUE.c.queued_at, QUEUE.c.message_id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
                if first is None:
                    return None
                ids = list(
                    (
                        await conn.execute(
                            sa.select(QUEUE.c.message_id)
                            .where(
                                QUEUE.c.reading_id.is_(None),
                                QUEUE.c.is_source.is_(True),
                                QUEUE.c.message_id.is_not(None),
                                QUEUE.c.unit == first["unit"],
                                QUEUE.c.platform == first["platform"],
                                QUEUE.c.origin_room == first["origin_room"],
                            )
                            .order_by(QUEUE.c.message_id)
                            .limit(batch_size)
                        )
                    ).scalars()
                )
                # Context is from the same immutable queued owner, so a later
                # policy reassignment cannot import another unit's history.
                context_ids = list(
                    (
                        await conn.execute(
                            sa.select(QUEUE.c.message_id)
                            .where(
                                QUEUE.c.unit == first["unit"],
                                QUEUE.c.platform == first["platform"],
                                QUEUE.c.origin_room == first["origin_room"],
                                QUEUE.c.message_id < min(ids),
                            )
                            .order_by(QUEUE.c.message_id.desc())
                            .limit(context_size)
                        )
                    ).scalars()
                )
                messages = await source_rows(conn, ids)
                context = await source_rows(conn, context_ids)
                reading = (
                    (
                        await conn.execute(
                            sa.insert(READING)
                            .values(
                                unit=first["unit"],
                                platform=first["platform"],
                                origin_room=first["origin_room"],
                                source_ids=[r["id"] for r in messages],
                                context_ids=[r["id"] for r in context],
                                snapshot={"messages": messages, "context": context},
                                version=VERSION,
                                model=model,
                                state="pending" if messages else "retired",
                                retired_at=None if messages else now,
                            )
                            .returning(READING)
                        )
                    )
                    .mappings()
                    .one()
                )
                await conn.execute(
                    sa.update(QUEUE)
                    .where(QUEUE.c.message_id.in_(ids))
                    .values(reading_id=reading["id"])
                )
                if not messages:
                    return None
            attempt = reading["attempts"] + 1
            await conn.execute(
                sa.update(READING)
                .where(READING.c.id == reading["id"])
                .values(
                    attempts=attempt,
                    due_at=now + timedelta(seconds=lease_seconds),
                )
            )
            attempt_id = (
                await conn.execute(
                    sa.insert(ATTEMPT)
                    .values(
                        reading_id=reading["id"],
                        attempt=attempt,
                        model=reading["model"],
                    )
                    .returning(ATTEMPT.c.id)
                )
            ).scalar_one()
            return {**reading, "attempts": attempt, "attempt_id": attempt_id}

    async def set_trace(self, job: dict[str, Any], trace_id: str | None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(ATTEMPT)
                .where(
                    ATTEMPT.c.id == job["attempt_id"],
                )
                .values(trace_id=trace_id)
            )

    async def record_usage(
        self,
        job: dict[str, Any],
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        provider_model: str | None,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(ATTEMPT)
                .where(ATTEMPT.c.id == job["attempt_id"])
                .values(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    provider_model=provider_model,
                )
            )

    async def publish_reading_batch(
        self, job: dict[str, Any], *, claims: list[dict[str, Any]]
    ) -> int:
        async with self.engine.begin() as conn:
            # Sources before reading: the invalidator's lock order, including
            # edits inserted while the model was running.
            messages = await source_rows(conn, job["source_ids"])
            context = await source_rows(conn, job["context_ids"])
            current = (
                (
                    await conn.execute(
                        sa.select(READING)
                        .where(
                            READING.c.id == job["id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if current["state"] != "pending" or current["attempts"] != job["attempts"]:
                await conn.execute(
                    sa.update(ATTEMPT)
                    .where(
                        ATTEMPT.c.id == job["attempt_id"],
                        ATTEMPT.c.outcome == "started",
                    )
                    .values(outcome="superseded", finished_at=sa.func.now())
                )
                return 0  # a retired batch or a stale lease cannot publish
            if {"messages": messages, "context": context} != job["snapshot"]:
                await conn.execute(
                    sa.update(READING)
                    .where(READING.c.id == job["id"])
                    .values(
                        state="retired",
                        retired_at=sa.func.now(),
                    )
                )
                # Still-eligible arrival rows are owed their first valid reading.
                await conn.execute(
                    sa.update(QUEUE)
                    .where(
                        QUEUE.c.reading_id == job["id"],
                    )
                    .values(reading_id=None)
                )
                await conn.execute(
                    sa.update(ATTEMPT)
                    .where(
                        ATTEMPT.c.id == job["attempt_id"],
                    )
                    .values(outcome="superseded", finished_at=sa.func.now())
                )
                return 0
            for ordinal, content in enumerate(claims):
                await conn.execute(
                    sa.insert(CLAIM).values(
                        reading_id=job["id"],
                        ordinal=ordinal,
                        unit=job["unit"],
                        platform=job["platform"],
                        origin_room=job["origin_room"],
                        source_ids=content["source_ids"],
                        content=content,
                    )
                )
            await conn.execute(
                sa.update(READING)
                .where(READING.c.id == job["id"])
                .values(
                    state="done",
                    built_at=sa.func.now(),
                    model=job["model"],
                )
            )
            await conn.execute(
                sa.update(ATTEMPT)
                .where(ATTEMPT.c.id == job["attempt_id"])
                .values(
                    outcome="ok",
                    finished_at=sa.func.now(),
                )
            )
            return len(claims)

    async def fail(self, job: dict[str, Any]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(READING)
                .where(
                    READING.c.id == job["id"],
                    READING.c.state == "pending",
                    READING.c.attempts == job["attempts"],
                )
                .values(
                    due_at=datetime.now(UTC)
                    + timedelta(
                        seconds=min(3600, 2 ** min(job["attempts"], 12)),
                    )
                )
            )
            await conn.execute(
                sa.update(ATTEMPT)
                .where(ATTEMPT.c.id == job["attempt_id"])
                .values(
                    outcome="error",
                    finished_at=sa.func.now(),
                )
            )
