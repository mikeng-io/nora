"""Claim and settle reflection input in Postgres."""

from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS, GRAPH_STIMULUS_CLAIM
from nora.memory.domain.wake import (
    WakeClaim,
    WakeGroup,
    WakeHistory,
    WakeOutcome,
    WakePolicy,
    decide_wake,
)

S = GRAPH_STIMULUS.c
C = GRAPH_STIMULUS_CLAIM.c
KEY = ("unit", "kind", "graph_id", "occurrence")
_ATTEMPT_LIMIT = 3  # Provisional until there is runtime evidence.
_ORDER = (S.eligible_at, S.kind, S.graph_id, S.occurrence)


def _available(now: datetime, *where: sa.ColumnElement[bool]) -> sa.Select:
    match = sa.and_(*(C[key] == S[key] for key in KEY), C.claimant == "wake")
    return (sa.select(*S, C.attempts)
            .select_from(GRAPH_STIMULUS.outerjoin(GRAPH_STIMULUS_CLAIM, match))
            .where(S.kind != "intention_due", S.eligible_at <= now,
                   sa.or_(C.state.is_(None), C.state == "released"), *where))


def _stimulus(row: Any) -> GraphStimulus:
    return GraphStimulus(
        row.unit, row.kind, row.graph_id, row.eligible_at, row.source, row.room, row.identity,
        tuple(row.cues), row.revision_id, tuple(row.dependents), row.occurrence,
    )


async def _history(conn: AsyncConnection, unit: str) -> WakeHistory:
    in_flight = await conn.scalar(sa.select(sa.func.count(sa.distinct(C.pass_id))).where(
        C.claimant == "wake", C.state == "claimed",
    ))
    active, claimed, closed, settled = (await conn.execute(sa.select(
        sa.func.bool_or(C.state == "claimed"),
        sa.func.max(C.claimed),
        sa.func.max(C.claimed).filter(C.state == "closed"),
        sa.func.max(C.settled),
    ).where(C.claimant == "wake", C.unit == unit))).one()
    return WakeHistory(bool(active), in_flight or 0, claimed, closed, settled)


class SqlWakeClaims:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def groups(self, now: datetime) -> tuple[WakeGroup, ...]:
        groups = (_available(now, S.kind != "aggregate_crossing")
                  .with_only_columns(S.unit, S.room)
                  .group_by(S.unit, S.room)
                  .order_by(sa.func.min(S.eligible_at), S.unit, S.room))
        async with self._engine.connect() as conn:
            return tuple(WakeGroup(*row) for row in await conn.execute(groups))

    async def claim(
        self, group: WakeGroup, reading_room: str, now: datetime, policy: WakePolicy,
    ) -> WakeClaim | None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtextextended(f"wake:unit:{group.unit}", 0),
            )))
            # Claim transactions share capacity across pollers.
            await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtextextended("wake:global", 0),
            )))
            history = await _history(conn, group.unit)
            origin = S.room.is_not_distinct_from(group.room)
            starter = origin & (S.kind != "aggregate_crossing")
            eligible = (await conn.execute(_available(now, S.unit == group.unit, starter))).all()
            decision = decide_wake(
                tuple(_stimulus(row) for row in eligible), history, now, policy,
                attempts=max((row.attempts or 0 for row in eligible), default=0),
            )
            if decision.reason is None:
                return None
            rows = (await conn.execute(
                _available(now, S.unit == group.unit, starter | (S.kind == "aggregate_crossing"))
                .order_by(*_ORDER).limit(policy.batch_max)
                .with_for_update(of=GRAPH_STIMULUS, skip_locked=True),
            )).all()
            if not rows:
                return None
            claimed = now
            if history.latest_claim is not None:
                claimed = max(now, history.latest_claim + timedelta(microseconds=1))
            claim = WakeClaim(group, reading_room, tuple(map(_stimulus, rows)), claimed,
                              decision.reason)
            await _insert_claim(conn, claim)
            return claim

    async def settle(self, claim: WakeClaim, outcome: WakeOutcome, now: datetime) -> int:
        attempts: sa.ColumnElement[int] = C.attempts
        state: str | sa.ColumnElement = "closed"
        if outcome.state == "failed":
            attempts = C.attempts + int(outcome.cause != "room_busy")
            state = sa.case((attempts >= _ATTEMPT_LIMIT, "parked"), else_="released")
        async with self._engine.begin() as conn:
            updated = await conn.scalars(sa.update(GRAPH_STIMULUS_CLAIM).where(
                C.unit == claim.group.unit, C.claimant == "wake", C.pass_id == claim.pass_id,
                C.state == "claimed",
            ).values(state=state, attempts=attempts, settled=now).returning(C.state))
            return sum(value == "parked" for value in updated)

    async def recover(self, now: datetime, bound: timedelta) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.update(GRAPH_STIMULUS_CLAIM).where(
                C.claimant == "wake", C.state == "claimed", C.claimed <= now - bound,
            ).values(state="released", settled=now))


async def _insert_claim(conn: AsyncConnection, claim: WakeClaim) -> None:
    pass_id = claim.pass_id
    statement = insert(GRAPH_STIMULUS_CLAIM).values([
        {
            "unit": s.unit, "kind": s.kind, "graph_id": s.graph_id, "occurrence": s.occurrence,
            "claimant": "wake", "state": "claimed", "claimed": claim.claimed, "pass_id": pass_id,
        }
        for s in claim.stimuli
    ]).on_conflict_do_update(
        index_elements=[*(C[key] for key in KEY), C.claimant],
        set_={"state": "claimed", "claimed": claim.claimed, "settled": None, "pass_id": pass_id},
        where=C.state == "released",
    ).returning(C.pass_id)
    if len((await conn.scalars(statement)).all()) != len(claim.stimuli):
        raise RuntimeError("wake claim changed while its stimuli were locked")
