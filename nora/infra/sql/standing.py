"""`SqlStandingStore` — the debrief's per-conversation position, read before
every fold and revised in place after it
(`design/dreamer-agent-loop/26-standing.md` §4).

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlTagTraceStore`/`SqlNoteStore`/`SqlFactStore`'s own contract-suite
affordance: no config, no clock, no metrics. `updated_at` arrives on the
`Standing` itself, so a fixture in another session can build this class from a
bare `AsyncEngine` and never has to guess what else it needs.

**The write is an UPSERT, not an insert-or-update pair.** R-10 makes two tags
for one conversation impossible to run concurrently, but the fire path reaches
the same conversation through the same slot, and a read-then-insert would race
into a duplicate-key error on exactly the turn that was already slow enough for
the window to matter. `ON CONFLICT` makes the race unrepresentable rather than
unlikely.

**`fold_count` is written, never incremented in SQL.** The caller holds the
prior it just read and computes `prior.fold_count + 1` there, because the
re-derivation path also has to be able to RESET it to zero, and a store that
only knows how to increment cannot express that. Arithmetic in the sink, not in
the statement.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.standing import Standing
from nora.infra.sql.schema import STANDING


class SqlStandingStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Standing | None:
        stmt = sa.select(STANDING).where(
            STANDING.c.platform == platform,
            STANDING.c.conversation_id == conversation_id,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        if row is None:
            return None
        return Standing(
            platform=row.platform,
            conversation_id=row.conversation_id,
            body=row.body,
            fold_count=row.fold_count,
            updated_at=row.updated_at,
            consumed_verdict_tag=row.consumed_verdict_tag,
        )

    async def write(self, *, standing: Standing) -> None:
        stmt = pg_insert(STANDING).values(
            platform=standing.platform,
            conversation_id=standing.conversation_id,
            body=standing.body,
            fold_count=standing.fold_count,
            updated_at=standing.updated_at,
            consumed_verdict_tag=standing.consumed_verdict_tag,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[STANDING.c.platform, STANDING.c.conversation_id],
            set_={
                "body": stmt.excluded.body,
                "fold_count": stmt.excluded.fold_count,
                "updated_at": stmt.excluded.updated_at,
                # WRITTEN ON EVERY UPSERT, never left out of the `set_`. The
                # caller carries the prior's watermark forward on a revise, so
                # the value handed here is always the intended one — but an
                # omitted key would make the column keep its old value by
                # accident rather than by decision, and "correct because the
                # writer happens to agree with the previous row" is exactly
                # what stops being true the first time it does not.
                "consumed_verdict_tag": stmt.excluded.consumed_verdict_tag,
            },
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
