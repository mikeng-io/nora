"""The version-announcement ledger: claim a version for a room, then record how
it went (DEC-79, R-42).

Shaped like `nora.infra.sql.reminders`: the engine comes through `__init__`,
every write is one statement inside `async with self._engine.begin()`, and the
narrow state transition carries its predicate IN the statement rather than in
Python.

WHY `claim` IS AN INSERT AND NOT A SELECT-THEN-INSERT
-----------------------------------------------------
`claim` is `INSERT … ON CONFLICT DO NOTHING` and answers from `rowcount`: the row
either did not exist and is now ours, or existed and is not. Read-then-write
would leave a window, and the window is not theoretical here — a crash-looping
container restarts every few seconds, so two boots can overlap on a slow database
and the second is exactly the one that must not speak. Postgres decides it, in
one statement, on the primary key.

A row EXISTING is the bar, whatever state it holds. That makes at-most-once hold
across a crash MID-TURN — the case a state check would get wrong — and it is the
same decision R-37b made for reminders, for the same reason: nothing links a row
to a message that may already have been delivered, so a retry double-posts
anything that died after egress. An upgrade note nobody hears is recoverable
(the operator re-tags a version); one posted twice is not.

There is deliberately ONE implementation and therefore no port and no fake:
every test points this class at a real throwaway Postgres (the `pg_url` fixture),
which is the only double that could behave like `ON CONFLICT DO NOTHING` anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema import VERSION_ANNOUNCEMENTS

# The three states a claimed row can hold. `ANNOUNCING` is what `claim` writes;
# the announcer resolves it from the fire path's outcome, and a row left
# `ANNOUNCING` by a crash stays that way forever ON PURPOSE — it is a claimed
# version, and re-claiming it is the one thing R-42 forbids. The operator's read
# of "this one died mid-turn" is that state, which is why it is not overwritten
# by a sweep the way a stranded reminder is.
ANNOUNCING = "announcing"
DONE = "done"
FAILED = "failed"


class SqlAnnouncementLog:
    """The `version_announcements` table, as two verbs."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def claim(
        self, *, version: str, platform: str, conversation_id: str, now: datetime,
    ) -> bool:
        """Take this version for this room, or report that somebody already has.

        `True` means the row was written by THIS call and the caller owns the
        announcement. `False` means a row already exists — announced, failed, or
        stranded mid-turn by a crash — and the caller must stay silent.
        """
        stmt = (
            pg_insert(VERSION_ANNOUNCEMENTS)
            .values(
                version=version,
                platform=platform,
                conversation_id=conversation_id,
                announced_at=now,
                state=ANNOUNCING,
                outcome=None,
            )
            .on_conflict_do_nothing(
                index_elements=["version", "platform", "conversation_id"],
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def resolve(
        self, *, version: str, platform: str, conversation_id: str,
        state: str, outcome: str,
    ) -> None:
        """Record how the announcement turn ended.

        An UPDATE on the row `claim` wrote, and deliberately not an upsert: if
        the row is gone, something deleted a claimed announcement, and silently
        re-creating it would hide that. Nothing here can un-claim a version —
        `state` moves `announcing → done|failed` and never back, because the CHECK
        constraint has no fourth value and the claim is what R-42 rests on.
        """
        stmt = (
            sa.update(VERSION_ANNOUNCEMENTS)
            .where(
                VERSION_ANNOUNCEMENTS.c.version == version,
                VERSION_ANNOUNCEMENTS.c.platform == platform,
                VERSION_ANNOUNCEMENTS.c.conversation_id == conversation_id,
            )
            .values(state=state, outcome=outcome)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def announced(self, *, version: str) -> Sequence[tuple[str, str, str]]:
        """Every `(platform, conversation_id, state)` this version has been taken
        for — the operator's read, and what the tests assert the ledger on."""
        stmt = (
            sa.select(
                VERSION_ANNOUNCEMENTS.c.platform,
                VERSION_ANNOUNCEMENTS.c.conversation_id,
                VERSION_ANNOUNCEMENTS.c.state,
            )
            .where(VERSION_ANNOUNCEMENTS.c.version == version)
            .order_by(
                VERSION_ANNOUNCEMENTS.c.platform,
                VERSION_ANNOUNCEMENTS.c.conversation_id,
            )
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [(r.platform, r.conversation_id, r.state) for r in rows]
