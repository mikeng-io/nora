"""Postgres claims and reply-outcome readback for due intentions."""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.cognitive_graph_schema import (
    DEBRIEF_OBLIGATION,
    GRAPH_STIMULUS,
    GRAPH_STIMULUS_CLAIM,
    INTENTION,
    NORA_ACT,
    NORA_ACT_OUTCOME,
    NORA_PASS_OUTPUT,
)
from nora.memory.adapters.debrief_store import insert_pending
from nora.memory.domain.debrief import DebriefObligation
from nora.memory.domain.proactive import RECHECK_UNAVAILABLE, DueIntention, ProactiveClaim

S = GRAPH_STIMULUS.c
C = GRAPH_STIMULUS_CLAIM.c
_INTENTIONS = INTENTION.c
A = NORA_ACT.c
_OUTCOMES = NORA_ACT_OUTCOME.c
P = NORA_PASS_OUTPUT.c
KEY = ("unit", "kind", "graph_id", "occurrence")
_MATCH = sa.and_(*(C[key] == S[key] for key in KEY), C.claimant == "proactive")
_RECHECK_ATTEMPT_LIMIT = 3  # Provisional until there is runtime evidence.


def _silence(unit: sa.ColumnElement, pass_id: sa.ColumnElement, reason: sa.ColumnElement):
    return sa.exists(sa.select(1).where(
        P.unit == unit, P["pass"] == pass_id, P.output_kind == "silence", reason,
    ))


def _reply_recorded(unit: sa.ColumnElement, pass_id: sa.ColumnElement) -> sa.ColumnElement[bool]:
    latest = (sa.select(_OUTCOMES.verdict).where(_OUTCOMES.act_id == A.id, _OUTCOMES.unit == A.unit)
              .order_by(_OUTCOMES.id.desc()).limit(1).scalar_subquery())
    return sa.or_(
        _silence(unit, pass_id, P.silence_reason.is_distinct_from(RECHECK_UNAVAILABLE)),
        sa.exists(sa.select(1).where(A.unit == unit, A["pass"] == pass_id,
                                     A.act_kind == "delivery",
                                     sa.func.coalesce(latest, "uncertain") != "failed")),
    )


def _available(now: datetime) -> sa.Select:
    return (sa.select(S.unit, S.graph_id, S.occurrence, S.eligible_at,
                      _INTENTIONS.origin_room, _INTENTIONS.owed_to)
            .select_from(GRAPH_STIMULUS.join(
                INTENTION, (_INTENTIONS.unit == S.unit) & (_INTENTIONS.id == S.graph_id),
            ).outerjoin(GRAPH_STIMULUS_CLAIM, _MATCH))
            .where(S.kind == "intention_due", S.eligible_at <= now, _INTENTIONS.state == "open",
                   sa.or_(C.state.is_(None), C.state == "released")))


class SqlProactiveClaims:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def recover(self, now: datetime, bound: timedelta) -> None:
        async with self._engine.begin() as conn:
            expired = (await conn.execute(sa.select(C.unit, C.pass_id).where(
                C.claimant == "proactive", C.state == "claimed", C.claimed <= now - bound,
            ).with_for_update(skip_locked=True))).all()
            if expired:
                await _settle_claims(conn, now, sa.tuple_(C.unit, C.pass_id).in_(expired))

    async def due_intentions(self, now: datetime) -> tuple[DueIntention, ...]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(_available(now).order_by(
                S.eligible_at, S.unit, S.graph_id, S.occurrence,
            ))
            return tuple(DueIntention(*row) for row in rows)

    async def claim(self, due: DueIntention, now: datetime) -> ProactiveClaim | None:
        async with self._engine.begin() as conn:
            key = (S.unit == due.unit, S.graph_id == due.intention,
                   S.occurrence == due.occurrence)
            row = (await conn.execute(_available(now).where(*key).with_for_update(
                of=(GRAPH_STIMULUS, INTENTION), skip_locked=True,
            ))).one_or_none()
            if row is None:
                return None
            previous = await conn.scalar(sa.select(C.claimed).where(
                C.unit == due.unit, C.kind == "intention_due", C.graph_id == due.intention,
                C.occurrence == due.occurrence, C.claimant == "proactive",
            ))
            claimed = max(now, previous + timedelta(microseconds=1)) if previous else now
            claim = ProactiveClaim(due, claimed)
            stmt = insert(GRAPH_STIMULUS_CLAIM).values(
                unit=due.unit, graph_id=due.intention, occurrence=due.occurrence,
                kind="intention_due", claimant="proactive", state="claimed", claimed=claimed,
                pass_id=claim.pass_id,
            ).on_conflict_do_update(
                index_elements=[*(C[key] for key in KEY), C.claimant],
                set_={"state": "claimed", "claimed": claimed, "settled": None,
                      "pass_id": claim.pass_id}, where=C.state == "released",
            ).returning(C.pass_id)
            return claim if await conn.scalar(stmt) is not None else None

    async def stage_debrief(self, claim: ProactiveClaim, row: DebriefObligation) -> bool:
        async with self._engine.begin() as conn:
            active = await conn.scalar(sa.select(C.pass_id).where(
                *_claim_key(claim),
            ).with_for_update())
            if active is None:
                return False
            return await insert_pending(conn, row, attempts=0)

    async def settle(self, claim: ProactiveClaim, now: datetime) -> str | None:
        async with self._engine.begin() as conn:
            results = await _settle_claims(conn, now, *_claim_key(claim), count_recheck=True)
            return results[0][2] if results else None


def _claim_key(claim: ProactiveClaim) -> tuple[sa.ColumnElement[bool], ...]:
    return (
        C.unit == claim.due.unit, C.kind == "intention_due", C.graph_id == claim.due.intention,
        C.occurrence == claim.due.occurrence, C.claimant == "proactive", C.pass_id == claim.pass_id,
        C.state == "claimed",
    )


async def _settle_claims(
    conn: AsyncConnection, now: datetime, *where: sa.ColumnElement[bool],
    count_recheck: bool = False,
) -> list[tuple[str, str, str]]:
    recorded = _reply_recorded(C.unit, C.pass_id)
    attempts: sa.ColumnElement[int] = C.attempts
    if count_recheck:
        incomplete = _silence(C.unit, C.pass_id, P.silence_reason == RECHECK_UNAVAILABLE)
        attempts = C.attempts + sa.case((~recorded & incomplete, 1), else_=0)
    updated = await conn.execute(sa.update(GRAPH_STIMULUS_CLAIM).where(*where).values(
        state=sa.case((recorded, "closed"), (attempts >= _RECHECK_ATTEMPT_LIMIT, "parked"),
                      else_="released"),
        attempts=attempts,
        settled=now,
    ).returning(C.unit, C.pass_id, C.state))
    results = [(unit, pass_id, state) for unit, pass_id, state in updated]
    unfinished = [(unit, pass_id) for unit, pass_id, state in results if state != "closed"]
    if unfinished:
        pending = DEBRIEF_OBLIGATION.c
        await conn.execute(sa.delete(DEBRIEF_OBLIGATION).where(
            sa.tuple_(pending.unit, pending.pass_id).in_(unfinished), pending.attempts == 0,
        ))
    return results
