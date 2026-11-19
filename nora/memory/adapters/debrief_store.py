"""The debrief journal, scoped pass evidence and commit readback."""

from datetime import datetime
from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.audit import transcript_select
from nora.infra.sql.cognitive_graph_schema import DEBRIEF_OBLIGATION, EDGE, INTENTION
from nora.infra.sql.schema import MESSAGES
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.app.acts import PassItem
from nora.memory.domain.admission_proposals import Occasion
from nora.memory.domain.authored_output import without_reasoning
from nora.memory.domain.debrief import MAX_SLOT_ATTEMPTS, DebriefObligation, ProposalId

JOURNAL = DEBRIEF_OBLIGATION
_C = JOURNAL.c


class SqlDebriefStore:
    def __init__(self, engine: AsyncEngine, acts: SqlActStore) -> None:
        self._engine = engine
        self._acts = acts

    async def begin(self, row: DebriefObligation) -> bool:
        async with self._engine.begin() as conn:
            inserted = await conn.scalar(insert(JOURNAL).values(
                unit=row.unit, room=row.room, pass_id=row.pass_id, occasion=row.occasion,
                intention=row.intention, created=row.created, attempts=1,
                first_message_id=row.first_message_id, last_message_id=row.last_message_id,
            ).on_conflict_do_nothing().returning(_C.pass_id))
        return inserted is not None

    async def take_oldest(self, unit: str, room: str) -> DebriefObligation | None:
        oldest = sa.select(_C.pass_id).where(
            _C.unit == unit, _C.room == room, _C.state == "pending",
            _C.attempts < MAX_SLOT_ATTEMPTS,
        ).order_by(_C.created, _C.pass_id).limit(1).with_for_update().scalar_subquery()
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.update(JOURNAL).where(
                    _C.unit == unit, _C.pass_id == oldest,
                    _C.state == "pending", _C.attempts < MAX_SLOT_ATTEMPTS,
                ).values(attempts=_C.attempts + 1).returning(*_C),
            )).mappings().one_or_none()
        if row is None:
            return None
        return DebriefObligation(
            row["unit"], row["room"], row["pass_id"], cast(Occasion, row["occasion"]),
            row["intention"], row["created"], row["first_message_id"], row["last_message_id"],
            None if row["proposal_ids"] is None else tuple(row["proposal_ids"]),
        )

    async def remember(
        self, row: DebriefObligation, proposal_ids: tuple[ProposalId, ...],
    ) -> None:
        await self._update(row, proposal_ids=list(proposal_ids))

    async def settle(self, row: DebriefObligation, now: datetime) -> None:
        await self._update(row, state="settled", settled=now)

    async def _update(self, row: DebriefObligation, **values: object) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.update(JOURNAL).where(
                _C.unit == row.unit, _C.pass_id == row.pass_id, _C.state == "pending",
            ).values(**values))

    async def confirmed(self, row: DebriefObligation) -> bool:
        proposals = row.proposal_ids or ()
        intention_ids = [ref["id"] for ref in proposals if ref["witness"] is None]
        pairs = [(ref["id"], ref["witness"]) for ref in proposals if ref["witness"] is not None]
        matching = sa.select(INTENTION.c.id).where(
            INTENTION.c.unit == row.unit, INTENTION.c.id.in_(intention_ids),
            INTENTION.c["pass"] == row.pass_id, INTENTION.c.occasion == "debrief",
        )
        closed = sa.select(EDGE.c.src_id, EDGE.c.dst_id).where(
            EDGE.c.unit == row.unit, EDGE.c.type == "closed_by", EDGE.c.state == "live",
            sa.tuple_(EDGE.c.src_id, EDGE.c.dst_id).in_(pairs),
        )
        async with self._engine.connect() as conn:
            written = set(await conn.scalars(matching))
            witnessed = set((await conn.execute(closed)).all())
        return written == set(intention_ids) and witnessed == set(pairs)

    async def items(self, row: DebriefObligation) -> tuple[PassItem, ...]:
        return await self._acts.read_pass_items(row.unit, row.pass_id)

    async def messages(self, row: DebriefObligation) -> tuple[PassItem, ...]:
        platform, _, room = row.room.partition(":")
        m = MESSAGES.c
        async with self._engine.connect() as conn:
            since = await conn.scalar(sa.select(sa.func.max(_C.settled)).where(
                _C.unit == row.unit, _C.room == row.room, _C.settled < row.created,
            ))
            scope = (m.unit == row.unit, m.platform == platform, m.conversation_id == room)
            window: sa.ColumnElement[bool]
            if since is None:
                if row.first_message_id is None or row.last_message_id is None:
                    return ()
                ends = (await conn.execute(sa.select(m.ts, m.id).where(
                    *scope, m.id.in_((row.first_message_id, row.last_message_id)),
                ).order_by(m.ts, m.id))).all()
                if len(ends) != len({row.first_message_id, row.last_message_id}):
                    raise LookupError("pass transcript bounds are unavailable")
                window = sa.tuple_(m.ts, m.id).between(tuple(ends[0]), tuple(ends[-1]))
            else:
                window = m.ts > int(since.timestamp() * 1000)
            transcript = transcript_select(
                edits_before=int(row.created.timestamp() * 1000),
            ).add_columns(m.graph_id).where(
                *scope, m.state == "live", m.exposure != "operator",
                m.asserted_at <= row.created, window,
            ).order_by(m.ts, m.id)
            rows = (await conn.execute(transcript)).mappings()
            return tuple(
                PassItem(
                    item["graph_id"] or f"message:{item['id']}", "message",
                    datetime.fromtimestamp(item["ts"] / 1000, tz=row.created.tzinfo),
                    "nora" if item["is_nora"] else item["person_id"] or "unidentified person",
                    without_reasoning(item["edit_body"] if item["edit_ts"] is not None
                                      else item["body"]),
                )
                for item in rows
            )
