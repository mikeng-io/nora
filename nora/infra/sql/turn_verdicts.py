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

    async def latest_ungrounded(
        self, *, conversation_id: str
    ) -> TurnVerdict | None:
        """The newest UNGROUNDED verdict in one conversation.

        **`idx_turn_verdict_ungrounded` serves exactly this**, and until now
        served nothing. It is partial on `grounded = false`, which is precisely
        the predicate here — so the one read the trigger makes on every fold is
        also the only one of the stage's five store calls that had been an
        unindexed scan. That mattered beyond tidiness: this is the read most
        likely to hit its 5s timeout, `_should_rebuild` swallows that as R-27's
        «I could not check», and the verdict-dropping path this method exists
        to close begins with exactly that failure.

        This REPLACED a `latest` that answered the newest verdict of either
        kind. See `TurnVerdictStore.latest_ungrounded` for why that read is not
        merely unindexed but wrong, and why it is gone rather than deprecated.
        """
        stmt = (
            sa.select(TURN_VERDICT)
            .where(TURN_VERDICT.c.conversation_id == conversation_id,
                   TURN_VERDICT.c.grounded.is_(False))
            # `tag_id` breaks the tie, matching the double exactly. Two turns
            # in one millisecond are reachable, and without this the two
            # implementations answer DIFFERENT verdicts for the same rows —
            # Postgres picks whatever the scan reaches first, the double
            # picks by hash order. A contract suite cannot see that unless
            # both sides are deterministic.
            .order_by(TURN_VERDICT.c.created_at.desc(),
                      TURN_VERDICT.c.tag_id.desc())
            .limit(1)
        )
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
