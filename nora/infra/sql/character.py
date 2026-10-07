"""`SqlCharacterEvidence` — what wakes a Character session, and what it is
handed (design/self-growth/10-character.md §3).

Each entity kind counts its own evidence since its key's watermark, and reads
the newest of it when it wakes. A count is the dream loop's wake signal (`lag`
for a room, generalised); a read is what the session is handed:

- **a person** (C2b): their human messages across their unit's rooms, by
  Record id.
- **a unit** (C3a): new revisions of its rooms' cultures, by COUNT: a culture
  revision's id is a uuid with no order, and the chain is append-only, so the
  number of revisions a unit's rooms hold only grows and is the cursor.

Every read here is a SELECT. Nothing is folded, cached or written.

**Media is not joined.** A person's lines are read by this module's own SQL,
so a photo they posted reaches their session as its stored body, the bare
`[image a3f2c1]` handle, not as what it showed (issue #116's join lives above
`nora.infra`). The session writes what a person is like from what they SAY;
joining media is the upgrade if an account is seen to miss what someone only
ever shows.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import RoomMessage
from nora.infra.sql.audit import transcript_row, transcript_select
from nora.infra.sql.schema import CULTURE_REVISION, MESSAGES

__all__ = ["SqlCharacterEvidence"]


def _in_rooms(rooms: Sequence[tuple[str, str]]) -> Any:
    """`(platform, conversation_id)` pairs as one predicate; none matches
    nothing (an empty `IN` is not an error, and not everything)."""
    if not rooms:
        return sa.false()
    return sa.or_(*(
        sa.and_(MESSAGES.c.platform == p, MESSAGES.c.conversation_id == c)
        for p, c in rooms
    ))


class SqlCharacterEvidence:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # -- a person ------------------------------------------------------------

    def _person(
        self, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> list[Any]:
        return [
            _in_rooms(rooms),
            MESSAGES.c.person_id == person_id,
            MESSAGES.c.is_nora == 0,
            MESSAGES.c.hidden_at.is_(None),
            MESSAGES.c.id > after,
        ]

    async def person_count(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> int:
        """How many messages `person_id` wrote in `rooms` after Record id
        `after`. `messages.person_id` is the roster's canonical id, resolved
        from the platform id at ingest (never a display name)."""
        stmt = sa.select(sa.func.count()).select_from(MESSAGES).where(
            *self._person(rooms, person_id, after),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def person_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]:
        """The NEWEST `limit` of those messages, oldest first, and the cursor
        to advance to: the newest id among ALL of them, read or not.

        Newest, not oldest: an account is one evolving head, so a backlog is
        read for what the person is saying lately, never walked from its
        start (`AdaptorSchedule.seed_at_head`'s reason for a room). The
        older part of a backlog past `limit` is consumed unread."""
        if limit <= 0:
            return [], after
        where = self._person(rooms, person_id, after)
        stmt = (
            transcript_select().add_columns(MESSAGES.c.conversation_id)
            .where(*where).order_by(MESSAGES.c.id.desc()).limit(limit)
        )
        head = sa.select(sa.func.max(MESSAGES.c.id)).where(*where)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            cursor = (await conn.execute(head)).scalar_one_or_none()
        out = [RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in rows]
        out.reverse()
        return out, int(cursor or after)

    # -- a unit --------------------------------------------------------------

    async def revision_count(self, *, keys: Sequence[str]) -> int:
        """How many culture revisions the rooms `keys` hold, ever. The cursor
        of a unit key: append-only, so it only grows (DEC-183)."""
        if not keys:
            return 0
        stmt = sa.select(sa.func.count()).select_from(CULTURE_REVISION).where(
            CULTURE_REVISION.c.culture_key.in_(list(keys)),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def revisions_after(
        self, *, keys: Sequence[str], after: int, limit: int,
    ) -> tuple[list[str], int]:
        """The room keys of the newest `limit` revisions past the first `after`
        (in the order they were written), oldest first, and the cursor: how
        many revisions the rooms hold now."""
        total = await self.revision_count(keys=keys)
        if limit <= 0 or total <= after or not keys:
            return [], max(total, after)
        stmt = (
            sa.select(CULTURE_REVISION.c.culture_key)
            .where(CULTURE_REVISION.c.culture_key.in_(list(keys)))
            .order_by(CULTURE_REVISION.c.written_at, CULTURE_REVISION.c.id)
            .offset(max(after, total - limit))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [r.culture_key for r in rows], total
