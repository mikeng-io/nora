"""`SqlTurnVerdictStore` — write-once storage for the debrief's per-turn
judgement (`design/dreamer-agent-loop/26-standing.md` §3).

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlStandingStore`/`SqlTagTraceStore`/`SqlNoteStore`'s contract-suite
affordance: no config, no clock, no metrics. `created_at` arrives on the
`TurnVerdict`.

**There is no `write`, no upsert and no `ON CONFLICT` — only `save`, a plain
INSERT.** That absence IS the never-rewritten property (§3), the same way
`DreamSink` has no delete verb: a path that does not exist cannot be taken by
someone who has not read the docstring. A second save for the same `tag_id`
raises `IntegrityError` from Postgres, which is the correct outcome — it means
a turn was debriefed twice, and silently overwriting would hide that.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.turn_verdict import TurnVerdict
from nora.infra.sql.schema import TURN_VERDICT


class SqlTurnVerdictStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(self, *, verdict: TurnVerdict) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(TURN_VERDICT).values(
                tag_id=verdict.tag_id,
                conversation_id=verdict.conversation_id,
                grounded=verdict.grounded,
                unsupported=list(verdict.unsupported),
                created_at=verdict.created_at,
            ))

    async def read(self, *, tag_id: str) -> TurnVerdict | None:
        """One turn's verdict, or `None`.

        Present for the contract suite and for the re-derivation path that will
        read these rows; nothing on the reply path calls it. A verdict is
        written and forgotten until something rebuilds a position from it.
        """
        stmt = sa.select(TURN_VERDICT).where(TURN_VERDICT.c.tag_id == tag_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        if row is None:
            return None
        return TurnVerdict(
            tag_id=row.tag_id,
            conversation_id=row.conversation_id,
            grounded=row.grounded,
            unsupported=tuple(row.unsupported),
            created_at=row.created_at,
        )
