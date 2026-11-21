"""Append-only act and outcome items in the Cognitive Graph."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.cognitive_graph_schema import NORA_ACT, NORA_ACT_OUTCOME, NORA_PASS_OUTPUT
from nora.infra.sql.item_registration import SqlItemRegistration, item_graph_id
from nora.memory.adapters.admission_store import _stamp
from nora.memory.domain.acts import (
    Act,
    ActAuthority,
    ActIdentity,
    ActKind,
    Guarantee,
    Outcome,
    Verdict,
)
from nora.memory.domain.authored_output import without_reasoning

ACT = NORA_ACT
OUTCOME = NORA_ACT_OUTCOME
_PROVENANCE = ("writer", "occasion", "pass", "trace", "model", "rev_code",
               "rev_instruction", "rev_skill", "rev_config", "rev_identity", "rev_policy")


class SqlActStore:
    def __init__(self, engine: AsyncEngine, registration: SqlItemRegistration) -> None:
        self._engine = engine
        self._registration = registration

    async def by_key(self, executor: str, key: str) -> Act | None:
        return await self._find(executor, ACT.c.idempotency_key == key)

    async def by_destination(self, executor: str, destination: str) -> Act | None:
        return await self._find(executor, ACT.c.destination == destination)

    async def _find(self, executor: str, predicate: sa.ColumnElement[bool]) -> Act | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(ACT).where(ACT.c.executor == executor, predicate),
            )).mappings().one_or_none()
        if row is None:
            return None
        return Act(
            ActIdentity(row["executor"], row["trigger_id"], row["ordinal"]),
            cast(ActKind, row["act_kind"]), row["destination"],
            ActAuthority(row["unit"], row["authority_person"], row["authority_intention"],
                         row["origin_room"]),
            cast(Guarantee, row["guarantee"]), row["idempotency_key"],
        )

    async def reserve(self, act: Act) -> bool:
        identity, authority = act.identity, act.authority
        stmt = insert(ACT).values(
            id=identity.id, executor=identity.executor, trigger_id=identity.trigger_id,
            ordinal=identity.ordinal, act_kind=act.kind, destination=act.destination,
            authority_person=authority.person, authority_intention=authority.intention,
            guarantee=act.guarantee, idempotency_key=act.key,
        ).on_conflict_do_nothing(index_elements=[ACT.c.id])
        async with self._engine.begin() as conn, conn.begin_nested() as attempt:
            planes = await self._registration.planes(
                conn, ACT, unit=authority.unit, occurred_at=datetime.now(UTC),
                persons=(authority.person,), room=authority.room, key=(identity.id,),
            )
            if authority.pass_context is None:
                planes["writer"] = identity.executor
            else:
                planes.update(_stamp(authority.pass_context))
            inserted = (await conn.execute(stmt.values(**planes).returning(ACT.c.id))).scalar()
            if inserted is None:
                await attempt.rollback()
        return inserted is not None

    def _owned(self, act: Act) -> sa.ColumnElement[bool]:
        return sa.and_(
            ACT.c.id == act.identity.id, ACT.c.unit == act.authority.unit,
            ACT.c.guarantee == act.guarantee, ACT.c.idempotency_key == act.key,
        )

    async def latest(self, act: Act) -> Outcome | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(OUTCOME.c.verdict, OUTCOME.c.result)
                .select_from(ACT.outerjoin(OUTCOME, ACT.c.id == OUTCOME.c.act_id))
                .where(self._owned(act)).order_by(OUTCOME.c.id.desc()).limit(1),
            )).first()
        if row is None:
            return None
        return Outcome(cast(Verdict, row.verdict or "uncertain"), row.result)

    async def observe(self, act: Act, outcome: Outcome) -> None:
        async with self._engine.begin() as conn:
            parent = (await conn.execute(
                sa.select(ACT).where(self._owned(act)).with_for_update(),
            )).mappings().one()
            latest = (await conn.execute(
                sa.select(OUTCOME.c.verdict).where(OUTCOME.c.act_id == parent["id"])
                .order_by(OUTCOME.c.id.desc()).limit(1),
            )).scalar()
            if latest is not None and (latest != "uncertain" or outcome.verdict == latest):
                return
            planes = await self._registration.planes(
                conn, OUTCOME, unit=parent["unit"], occurred_at=datetime.now(UTC),
                persons=parent["persons"], room=parent["origin_room"],
                failed=outcome.verdict == "failed",
            )
            planes.update({column: parent[column] for column in _PROVENANCE})
            await conn.execute(sa.insert(OUTCOME).values(
                **planes, act_id=parent["id"], verdict=outcome.verdict,
                result=without_reasoning(outcome.result),
            ))

    async def append_pass(
        self, authority: ActAuthority, kind: str, content: list[str] | None,
    ) -> None:
        context = authority.pass_context
        if context is None:
            raise ValueError("pass output requires pass provenance")
        table = NORA_PASS_OUTPUT
        row_id = item_graph_id(table.name, (context.unit, context.pass_id, kind), context.unit)
        async with self._engine.begin() as conn:
            planes = await self._registration.planes(
                conn, table, unit=context.unit, occurred_at=datetime.now(UTC),
                persons=(authority.person,), room=context.room, key=(row_id,),
            )
            planes.update(_stamp(context))
            await conn.execute(
                insert(table).values(**planes, id=row_id, output_kind=kind, content=content)
                .on_conflict_do_nothing(index_elements=[table.c.id]),
            )

    async def read_pass(self, unit: str, pass_id: str) -> dict[str, list[str] | None]:
        table = NORA_PASS_OUTPUT
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                sa.select(table.c.output_kind, table.c.content)
                .where(table.c.unit == unit, table.c["pass"] == pass_id),
            )
            return {row.output_kind: row.content for row in rows}
