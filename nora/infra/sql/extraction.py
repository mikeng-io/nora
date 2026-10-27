"""Durable Record arrival outbox and fenced, atomic batch publication.

No retrieval API and no index projection. All reads here are writer inputs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.extraction import (
    CLAIM_DROP_REASONS,
    VERSION,
    ClaimDropReason,
    ExtractionFailureStage,
)
from nora.infra.sql.schema import EXTRACTION as EXTRACTION
from nora.infra.sql.schema import EXTRACTION_ATTEMPT as ATTEMPT
from nora.infra.sql.schema import EXTRACTION_CLAIM as CLAIM
from nora.infra.sql.schema import EXTRACTION_QUEUE as QUEUE
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_EDIT, MESSAGES

# ponytail: five attempts absorb transient outages without repeating deterministic
# bad output 13-15 times; re-queue failed sources after repair, rather than tune a lane.
MAX_ATTEMPTS = 5


def admission(
    row: Mapping[str, Any],
    unit_of: Callable[[str, str], str | None],
    *,
    sources_only: bool = False,
) -> tuple[str | None, str | None]:
    """One arrival/backfill admission; Nora supplies live/historical context, never sources.

    No person/body/category filter: unattributed humans and media-only messages
    are sources too. The stored visibility flags also govern source_rows.
    """
    if row.get("hidden_at") is not None:
        return None, "hidden"
    if row.get("deleted", False):
        return None, "deleted"
    if sources_only and row["is_nora"]:
        return None, "nora"
    unit = unit_of(row["platform"], row["conversation_id"])
    return (unit, None) if unit is not None else (None, "unmapped")


def source_key(row: Mapping[str, Any]) -> str:
    return json.dumps(
        [row["platform_message_id"]]
        if row["platform_message_id"] else [row["sender_id"], row["ts"]]
    )


async def enqueue(
    conn: AsyncConnection,
    *,
    row: Mapping[str, Any],
    unit: str,
    historical: bool = False,
) -> bool:
    """Shared arrival INSERT; both replay keys and message ids are idempotent."""
    return (await conn.execute(
        insert(QUEUE)
        .values(
            message_id=row["id"],
            platform=row["platform"],
            origin_room=row["conversation_id"],
            unit=unit,
            source_key=source_key(row),
            is_source=not row["is_nora"],
            historical=historical,
        )
        .on_conflict_do_nothing()
        .returning(QUEUE.c.id)
    )).scalar_one_or_none() is not None


async def source_rows(
    conn: AsyncConnection, ids: list[int], *, include_reply: bool = False,
) -> list[dict[str, Any]]:
    if not ids:
        return []
    # Locks serialize publication with hide/edit/delete. Invalidation triggers
    # take the same source row lock before retiring the dependent extraction.
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
                **({"quoted_ts": row["quoted_ts"]} if include_reply else {}),
            }
        )
    return result


def link_replies(messages: list[dict[str, Any]], context: list[dict[str, Any]]) -> None:
    targets: dict[int, list[int]] = {}
    for row in messages + context:
        targets.setdefault(row["ts"], []).append(row["id"])
    for row in messages:
        matches = targets.get(row["quoted_ts"], [])
        row["reply_to"] = matches[0] if len(matches) == 1 else None


class SqlExtraction:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def take_extraction_batch(
        self,
        *,
        model: str,
        messages_per_call: int,
        context_messages: int,
        lease_seconds: float,
        min_message_age_seconds: float,
    ) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            # ponytail: one short transaction lock for batch formation, never a
            # model await. Per-room formation if worker contention is measured.
            await conn.execute(sa.text("SELECT pg_advisory_xact_lock(1290081)"))
            now = datetime.now(UTC)
            live_waiting = sa.exists().where(
                QUEUE.c.extraction_id.is_(None), QUEUE.c.is_source.is_(True),
                QUEUE.c.message_id.is_not(None), QUEUE.c.historical.is_(False),
                QUEUE.c.queued_at <= now - timedelta(seconds=min_message_age_seconds),
            )
            historical_batch = sa.exists().where(
                QUEUE.c.extraction_id == EXTRACTION.c.id, QUEUE.c.historical.is_(True),
            )
            extraction = (
                (
                    await conn.execute(
                        sa.select(EXTRACTION)
                        .where(
                            EXTRACTION.c.state == "pending",
                            EXTRACTION.c.due_at <= now,
                            # A due historical retry must yield to unbatched live traffic too.
                            sa.or_(~historical_batch, ~live_waiting),
                        )
                        .order_by(EXTRACTION.c.due_at, EXTRACTION.c.id)
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if extraction is None:
                first = (
                    (
                        await conn.execute(
                            sa.select(QUEUE)
                            .where(
                                QUEUE.c.extraction_id.is_(None),
                                QUEUE.c.is_source.is_(True),
                                QUEUE.c.message_id.is_not(None),
                                QUEUE.c.queued_at
                                <= now - timedelta(seconds=min_message_age_seconds),
                            )
                            # ponytail: live arrivals precede history; an in-flight call/retry
                            # is not preempted. Add fairness if history starvation is measured.
                            .order_by(QUEUE.c.historical, QUEUE.c.queued_at, QUEUE.c.id)
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
                                QUEUE.c.extraction_id.is_(None),
                                QUEUE.c.is_source.is_(True),
                                QUEUE.c.message_id.is_not(None),
                                QUEUE.c.historical == first["historical"],
                                QUEUE.c.unit == first["unit"],
                                QUEUE.c.platform == first["platform"],
                                QUEUE.c.origin_room == first["origin_room"],
                            )
                            .order_by(QUEUE.c.queued_at, QUEUE.c.id)
                            .limit(messages_per_call)
                        )
                    ).scalars()
                )
                # Context is from the same immutable queued owner, so a later
                # policy reassignment cannot import another unit's history.
                messages = await source_rows(conn, ids, include_reply=True)
                quoted = {r["quoted_ts"] for r in messages if r["quoted_ts"] is not None}
                parent_ids = list((await conn.execute(
                    sa.select(MESSAGES.c.id)
                    .join(QUEUE, QUEUE.c.message_id == MESSAGES.c.id)
                    .where(
                        QUEUE.c.unit == first["unit"],
                        QUEUE.c.platform == first["platform"],
                        QUEUE.c.origin_room == first["origin_room"],
                        MESSAGES.c.ts.in_(quoted),
                    )
                    .order_by(MESSAGES.c.id)
                )).scalars())
                parents = await source_rows(conn, parent_ids)
                by_ts: dict[int, list[dict[str, Any]]] = {}
                for row in parents:
                    by_ts.setdefault(row["ts"], []).append(row)
                # Direct parents first, in new-message order; no recursive walk.
                context: list[dict[str, Any]] = []
                selected = set(ids)
                for row in messages:
                    matches = by_ts.get(row["quoted_ts"], [])
                    if len(matches) == 1 and len(context) < context_messages:
                        parent = matches[0]
                        if parent["id"] not in selected:
                            context.append(parent)
                            selected.add(parent["id"])
                before = min(ids)
                while len(context) < context_messages:
                    nearby = list((await conn.execute(
                        sa.select(QUEUE.c.message_id)
                        .where(
                            QUEUE.c.unit == first["unit"],
                            QUEUE.c.platform == first["platform"],
                            QUEUE.c.origin_room == first["origin_room"],
                            QUEUE.c.message_id < before,
                            QUEUE.c.message_id.not_in(selected),
                        )
                        .order_by(QUEUE.c.message_id.desc())
                        .limit(context_messages - len(context))
                    )).scalars())
                    if not nearby:
                        break
                    context.extend(await source_rows(conn, nearby))
                    selected.update(nearby)
                    before = min(nearby)
                context.sort(key=lambda r: r["id"])
                link_replies(messages, context)
                for row in messages:
                    if len(by_ts.get(row["quoted_ts"], [])) > 1:
                        row["reply_to"] = None
                extraction = (
                    (
                        await conn.execute(
                            sa.insert(EXTRACTION)
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
                            .returning(EXTRACTION)
                        )
                    )
                    .mappings()
                    .one()
                )
                await conn.execute(
                    sa.update(QUEUE)
                    .where(QUEUE.c.message_id.in_(ids))
                    .values(extraction_id=extraction["id"])
                )
                if not messages:
                    return None
            if extraction["attempts"] >= MAX_ATTEMPTS:
                # An exhausted lease (or a legacy over-ceiling batch) must not
                # buy another model call. The caller counts this committed transition.
                await conn.execute(
                    sa.update(EXTRACTION)
                    .where(EXTRACTION.c.id == extraction["id"])
                    .values(state="failed")
                )
                return {**extraction, "state": "failed"}
            attempt = extraction["attempts"] + 1
            await conn.execute(
                sa.update(EXTRACTION)
                .where(EXTRACTION.c.id == extraction["id"])
                .values(
                    attempts=attempt,
                    due_at=now + timedelta(seconds=lease_seconds),
                )
            )
            attempt_id = (
                await conn.execute(
                    sa.insert(ATTEMPT)
                    .values(
                        extraction_id=extraction["id"],
                        attempt=attempt,
                        model=extraction["model"],
                    )
                    .returning(ATTEMPT.c.id)
                )
            ).scalar_one()
            return {**extraction, "attempts": attempt, "attempt_id": attempt_id}

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

    async def record_drops(
        self, job: dict[str, Any], *, counts: dict[ClaimDropReason, int],
    ) -> None:
        """Counts only, tied to this attempt even if publication subsequently fails."""
        if any(reason not in CLAIM_DROP_REASONS or type(count) is not int or count <= 0
               for reason, count in counts.items()):
            raise ValueError("invalid claim drop counts")
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.update(ATTEMPT)
                .where(ATTEMPT.c.id == job["attempt_id"], ATTEMPT.c.outcome == "started")
                .values(dropped_claims=counts)
            )

    async def publish_extraction_batch(
        self, job: dict[str, Any], *, claims: list[dict[str, Any]]
    ) -> int:
        async with self.engine.begin() as conn:
            # Sources before extraction: the invalidator's lock order, including
            # edits inserted while the model was running.
            # Pre-change pending snapshots retain their exact frozen shape.
            with_reply = "quoted_ts" in job["snapshot"]["messages"][0]
            messages = await source_rows(conn, job["source_ids"], include_reply=with_reply)
            context = await source_rows(conn, job["context_ids"])
            if with_reply:
                links = {r["id"]: r["reply_to"] for r in job["snapshot"]["messages"]}
                for row in messages:
                    row["reply_to"] = links[row["id"]]
            current = (
                (
                    await conn.execute(
                        sa.select(EXTRACTION)
                        .where(
                            EXTRACTION.c.id == job["id"],
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
                    sa.update(EXTRACTION)
                    .where(EXTRACTION.c.id == job["id"])
                    .values(
                        state="retired",
                        retired_at=sa.func.now(),
                    )
                )
                # Still-eligible arrival rows are owed their first valid extraction.
                await conn.execute(
                    sa.update(QUEUE)
                    .where(
                        QUEUE.c.extraction_id == job["id"],
                    )
                    .values(extraction_id=None)
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
                        extraction_id=job["id"],
                        ordinal=ordinal,
                        unit=job["unit"],
                        platform=job["platform"],
                        origin_room=job["origin_room"],
                        source_ids=content["source_ids"],
                        content=content,
                    )
                )
            await conn.execute(
                sa.update(EXTRACTION)
                .where(EXTRACTION.c.id == job["id"])
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

    async def fail(
        self, job: dict[str, Any], *, stage: ExtractionFailureStage = "storage",
    ) -> bool:
        async with self.engine.begin() as conn:
            terminal = job["attempts"] >= MAX_ATTEMPTS
            changed = await conn.execute(
                sa.update(EXTRACTION)
                .where(
                    EXTRACTION.c.id == job["id"],
                    EXTRACTION.c.state == "pending",
                    EXTRACTION.c.attempts == job["attempts"],
                )
                .values(
                    state="failed" if terminal else "pending",
                    due_at=datetime.now(UTC)
                    + timedelta(
                        seconds=min(3600, 2 ** min(job["attempts"], 12)),
                    )
                )
            )
            await conn.execute(
                sa.update(ATTEMPT)
                .where(ATTEMPT.c.id == job["attempt_id"], ATTEMPT.c.outcome == "started")
                .values(
                    outcome="error",
                    failure_stage=stage,
                    finished_at=sa.func.now(),
                )
            )
            return terminal and changed.rowcount == 1

    async def requeue(self, extraction_id: int) -> bool:
        """Retain failed history and owe its sources a fresh batch/attempt allowance."""
        async with self.engine.begin() as conn:
            changed = await conn.execute(
                sa.update(EXTRACTION)
                .where(EXTRACTION.c.id == extraction_id, EXTRACTION.c.state == "failed")
                .values(state="retired", retired_at=sa.func.now())
            )
            if changed.rowcount != 1:
                return False
            await conn.execute(
                sa.update(QUEUE)
                .where(QUEUE.c.extraction_id == extraction_id)
                .values(extraction_id=None)
            )
            return True
