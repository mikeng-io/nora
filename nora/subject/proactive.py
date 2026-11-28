"""Drive a seeded graph intention through the composed proactive poller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import sqlalchemy as sa

import nora.memory as memory
from nora.domain.rollup import room_key
from nora.domain.ticks import tick_of_at, tick_start_ms
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS_CLAIM

from .obtain import Subject


@dataclass(frozen=True, slots=True)
class ActPass:
    token: Literal["sent", "silent"]
    said: tuple[str, ...]
    delivery: str


async def owe(
    subject: Subject, *, intent: str, decided_hours_ago: float = 6.0,
) -> str:
    """Seed the due request in the case's room."""
    platform, room = subject.room
    if not subject.deps.config_reload.policy.proactive_allowed(
        platform=platform, conversation_id=room,
    ):
        raise LookupError("failed_policy: the case room has no proactive grant")
    now = subject.deps.clock()
    due = datetime.fromtimestamp(tick_start_ms(tick_of_at(now)) / 1000, UTC)
    engine = create_sql_engine(subject.database_url)
    try:
        async with engine.begin() as conn:
            return await memory.wire.seed_scheduled_intention(
                conn, unit=subject.unit, room=room_key(platform, room),
                requester="nora", text=intent, due_at=due,
                created_at=now - timedelta(hours=decided_hours_ago),
            )
    finally:
        await engine.dispose()


async def act(subject: Subject, *, intention: str, after_id: int = 0) -> ActPass:
    """Publish the case schedule and read its closed claim and authored sends."""
    from .knowledge import heard

    engine = create_sql_engine(subject.database_url)
    try:
        await memory.wire.graph_clock(engine, subject.deps.clock).run_once()
        await subject.deps.proactive_poller.poll(intention=intention)
        claims = GRAPH_STIMULUS_CLAIM.c
        async with engine.connect() as conn:
            state = await conn.scalar(sa.select(claims.state).where(
                claims.unit == subject.unit, claims.graph_id == intention,
                claims.claimant == "proactive",
            ))
        if state != "closed":
            raise LookupError("The case's due intention did not complete a proactive pass")
        replies = await heard(subject, after_id=after_id)
        return ActPass(
            token="sent" if replies else "silent", said=tuple(body for body, _ in replies),
            delivery=",".join(sorted({status for _, status in replies})),
        )
    finally:
        await engine.dispose()
