"""`SqlTagDebounceStore` — R-9's per-conversation rate limiter
(`nora.tag.Debounce`), in `tag_debounce_state` instead of in a dict a
force-recreate empties (migration 0056).

**THREE VERBS, because the read happens once and the write happens per tag.**
The rule is `nora/infra/sql/dream_schedule.py`'s: a combined read-and-write
would put a write on the path that only wanted to know. Here the split is
sharper still — `Debounce.allow` is SYNCHRONOUS (see that module: an await
between the admission verdict and the enqueue would let two concurrent tags
both read the same queue depth), so the read cannot happen there at all. It
happens once, at wiring time, through `hydrate`.

**`prune_before` is the bound, and it needs no policy to pick a cutoff.** A row
older than `min_interval_seconds` can never change `allow()`'s answer again —
that is arithmetic, not a retention judgement — so hydration deletes those
instead of loading them, and the table holds at most the conversations tagged
inside one debounce window. This is why there is no sweep loop for it, unlike
the in-memory dict, which `nora.app.runtime.debounce_sweep_loop` bounds because
it grows with every conversation ever tagged.

**Epoch milliseconds, wall clock.** `Debounce`'s own clock moved from
`time.monotonic` to `time.time` in the same commit as this table: a monotonic
value has an arbitrary per-process epoch, so a stored one is not a time to the
process that reads it back.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .schema import TAG_DEBOUNCE_STATE


class SqlTagDebounceStore:
    """`__init__(self, engine)` and nothing else — `SqlStandingStore`'s
    affordance, for the same reason."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read_all(self) -> list[tuple[str, str, int]]:
        """`(platform, conversation_id, last_allowed_at_ms)` for every row.

        Read AFTER `prune_before`, so what comes back is only what can still
        change an answer. Returns plain tuples rather than a domain type: the
        row IS the key and the instant, and a two-field dataclass here would be
        a name for a pair that never travels beyond `Debounce.hydrate`.
        """
        stmt = sa.select(
            TAG_DEBOUNCE_STATE.c.platform,
            TAG_DEBOUNCE_STATE.c.conversation_id,
            TAG_DEBOUNCE_STATE.c.last_allowed_at,
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [(r.platform, r.conversation_id, int(r.last_allowed_at)) for r in rows]

    async def prune_before(self, *, before_ms: int) -> int:
        """Delete rows that can no longer change `allow()`; returns how many.

        Not a retention policy and not a TTL: the caller computes the cutoff
        from its OWN interval, so the rows this drops are precisely the ones
        whose window has already elapsed. Deleting them is therefore lossless
        by construction, which is the only reason this is allowed to delete at
        all.
        """
        stmt = sa.delete(TAG_DEBOUNCE_STATE).where(
            TAG_DEBOUNCE_STATE.c.last_allowed_at < before_ms
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def record_allowed(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None:
        """Stamp a tag that was ALLOWED. An UPSERT, because the second tag in a
        conversation is the ordinary case.

        A BLOCKED tag never reaches here: `Debounce.allow` returns False
        without touching `_last`, and R-9's own rule is that a blocked tag does
        not extend the window. A verb that wrote on every call would extend it,
        and a burst would then starve the conversation — the behaviour
        `test_a_blocked_tag_does_not_extend_the_window` pins.
        """
        stmt = pg_insert(TAG_DEBOUNCE_STATE).values(
            platform=platform, conversation_id=conversation_id,
            last_allowed_at=at_ms,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_update(
                index_elements=[
                    TAG_DEBOUNCE_STATE.c.platform,
                    TAG_DEBOUNCE_STATE.c.conversation_id,
                ],
                set_={
                    "last_allowed_at": stmt.excluded.last_allowed_at,
                    "updated_at": sa.func.now(),
                },
            ))
