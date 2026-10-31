"""`SqlRecordQuery` — the Record read behind `record_query`.

One method: the messages of some rooms in an interval, optionally one
person's, optionally matching some text, each with its newest edit folded in
(`transcript_select`, the one select every transcript read shares), its
reactions and whether it was deleted.

**Which rooms is the caller's decision, made before this runs.** This store
reads exactly the `(platform, conversation_id)` pairs it is handed; the
lineage predicate (`nora.agent.lineage`) chose them for the turn's room. A
store that filtered afterwards would have read another room's words into the
process first.

**Lexical search is pg_trgm**: a message matches when its CURRENT text (its
newest edit, else its body) CONTAINS the words (`ilike`, the note tier's
operator and reason, `nora.infra.sql.notes`), or when pg_trgm's word
similarity finds them inside it at `WORD_SIMILARITY_MIN` or above. The second
half is the word-similarity operator: a CJK run is one token to full-text search, and
plain `similarity` scored the true in-sentence match 0.18, under its own
default. Containment ranks first, then word similarity, then the newest; a
hit that does not contain the words is marked `near`.
`idx_messages_body_trgm` (migration `0073_lineage`) serves both operators on
unedited messages. **A deleted message never matches text, and an earlier
version of an edited one is never searched**: a hit is a claim that the words
are there, and it would make that claim about words their author took back.

`__init__(self, engine)` and nothing else, the house shape for a store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.cognition_ground import RECORD_REF_TABLES
from nora.domain.record_query import RECORD_QUERY_MAX, RecordHit, RecordQuery, RecordRefHit
from nora.infra.sql.audit import (
    reaction_states,
    transcript_row,
    transcript_select,
    with_reactions,
)
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_EDIT, MESSAGE_REACTION, MESSAGES

__all__ = ["WORD_SIMILARITY_MIN", "SqlRecordQuery"]

#: pg_trgm's word similarity at which a message counts as mentioning the
#: words. 0.5 fits the real case, a three-character
#: Cantonese term inside a sentence (`word_similarity('大步行', '我今日去咗大步行')`
#: = 0.5), where pg_trgm's own default (0.6) misses it; an unrelated Chinese
#: string scored 0.
WORD_SIMILARITY_MIN = 0.5


class SqlRecordQuery:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def query(self, q: RecordQuery) -> tuple[tuple[RecordHit, ...], bool, frozenset[int]]:
        """The hits, oldest first; whether more matched than were returned;
        and the ids of hits that only NEAR-match `q.text` (no literal
        containment). No rooms, or a non-positive limit, reads nothing."""
        limit = max(0, min(q.limit, RECORD_QUERY_MAX))
        if not q.rooms or limit == 0 or q.end_ms <= q.start_ms:
            return (), False, frozenset()
        in_rooms = sa.or_(*(
            sa.and_(MESSAGES.c.platform == platform, MESSAGES.c.conversation_id == cid)
            for platform, cid in q.rooms
        ))
        stmt = (
            transcript_select()
            .add_columns(MESSAGES.c.platform, MESSAGES.c.conversation_id)
            .where(in_rooms, MESSAGES.c.ts >= q.start_ms, MESSAGES.c.ts < q.end_ms)
        )
        if q.person_id is not None:
            stmt = stmt.where(MESSAGES.c.person_id == q.person_id)
        needle = (q.text or "").strip()
        contains: Any = None
        if needle:
            # WHAT IS SAID NOW, and only by a message still standing. A text
            # match is a claim about the words: matched against an earlier
            # version of an edited message, or against a deleted one, a hit
            # would prove the words were there even with the body withheld.
            # So the current text is the newest
            # edit's, or the body when there is none, and a deleted message
            # never matches.
            edited = sa.exists().where(MESSAGE_EDIT.c.message_id == MESSAGES.c.id)
            newest_edit = (
                sa.select(MESSAGE_EDIT.c.body)
                .where(MESSAGE_EDIT.c.message_id == MESSAGES.c.id)
                .order_by(MESSAGE_EDIT.c.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            current = sa.func.coalesce(newest_edit, MESSAGES.c.body)
            deleted = sa.exists().where(MESSAGE_DELETE.c.message_id == MESSAGES.c.id)
            # Each half is written against `messages.body` where it can be, so
            # `idx_messages_body_trgm` serves the unedited majority.
            contains = sa.or_(
                sa.and_(MESSAGES.c.body.icontains(needle, autoescape=True), ~edited),
                newest_edit.icontains(needle, autoescape=True),
            )
            # `<%` is pg_trgm's word-similarity operator, true at or above
            # `pg_trgm.word_similarity_threshold` (set below for this
            # transaction): the indexable spelling of `word_similarity(...) >=`.
            near_enough = sa.or_(
                sa.and_(sa.literal(needle).op("<%")(MESSAGES.c.body), ~edited),
                sa.literal(needle).op("<%")(newest_edit),
            )
            similar = sa.func.word_similarity(sa.literal(needle), current)
            stmt = (
                stmt.add_columns(contains.label("contains"))
                .where(sa.or_(contains, near_enough), ~deleted)
                .order_by(contains.desc(), similar.desc(), MESSAGES.c.ts.desc(),
                          MESSAGES.c.id.desc())
            )
        else:
            stmt = stmt.order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
        stmt = stmt.limit(limit + 1)
        async with self._engine.begin() as conn:
            await conn.execute(sa.text(
                f"SET LOCAL pg_trgm.word_similarity_threshold = {WORD_SIMILARITY_MIN}"
            ))
            rows = (await conn.execute(stmt)).all()
            more = len(rows) > limit
            rows = rows[:limit]
            ids = [r.id for r in rows]
            reactions = await reaction_states(conn, ids)
            gone = await self._deleted(conn, ids)
        near = frozenset(r.id for r in rows if needle and not r.contains)
        hits = sorted(
            (
                RecordHit(
                    platform=r.platform, conversation_id=r.conversation_id,
                    row=with_reactions([transcript_row(r)], reactions)[0],
                    reactions=(
                        reactions[r.id].counts if r.id in reactions else ()
                    ),
                    deleted=r.id in gone,
                )
                for r in rows
            ),
            key=lambda h: (h.row.ts, h.row.id),
        )
        return tuple(hits), more, near

    async def by_refs(
        self, refs: Sequence[tuple[str, int]], rooms: Sequence[tuple[str, str]],
    ) -> tuple[RecordRefHit, ...]:
        """The messages the `(table, row id)` refs of message stimuli name
        (`messages`, `message_reaction`, `message_delete`), read for the
        Cognition session's grounding.

        Not `record_query`: no interval, no search, and NOT behind
        `[record_query] enabled`, which decides whether a MODEL is offered a
        tool, not whether code may read the rows that raised its own stimuli.
        The caller's rooms are the whole boundary, as for `query` (a row of a
        room not listed is never selected), and the withdrawal cascade's hidden
        rows are excluded by the same `transcript_select` every read shares. A
        ref that names nothing readable (hidden, another room, gone) simply has
        no hit; the caller counts it, it never guesses one."""
        if not refs or not rooms:
            return ()
        in_rooms = sa.or_(*(
            sa.and_(MESSAGES.c.platform == platform, MESSAGES.c.conversation_id == cid)
            for platform, cid in rooms
        ))
        wanted: dict[str, set[int]] = {t: set() for t in RECORD_REF_TABLES}
        for table, row_id in refs:
            if table in wanted:
                wanted[table].add(row_id)
        async with self._engine.connect() as conn:
            emoji: dict[int, tuple[int, str]] = {}
            if wanted["message_reaction"]:
                for r in (await conn.execute(
                    sa.select(
                        MESSAGE_REACTION.c.id, MESSAGE_REACTION.c.message_id,
                        MESSAGE_REACTION.c.emoji,
                    ).where(MESSAGE_REACTION.c.id.in_(wanted["message_reaction"]))
                )).all():
                    emoji[int(r.id)] = (int(r.message_id), str(r.emoji))
            deleted_of: dict[int, int] = {}
            if wanted["message_delete"]:
                for r in (await conn.execute(
                    sa.select(MESSAGE_DELETE.c.id, MESSAGE_DELETE.c.message_id)
                    .where(MESSAGE_DELETE.c.id.in_(wanted["message_delete"]))
                )).all():
                    deleted_of[int(r.id)] = int(r.message_id)
            message_ids = (
                wanted["messages"]
                | {m for m, _ in emoji.values()} | set(deleted_of.values())
            )
            if not message_ids:
                return ()
            rows = (await conn.execute(
                transcript_select()
                .add_columns(MESSAGES.c.platform, MESSAGES.c.conversation_id)
                .where(in_rooms, MESSAGES.c.id.in_(message_ids))
            )).all()
            ids = [r.id for r in rows]
            reactions = await reaction_states(conn, ids)
            gone = await self._deleted(conn, ids)
        hits = {
            int(r.id): RecordHit(
                platform=r.platform, conversation_id=r.conversation_id,
                row=with_reactions([transcript_row(r)], reactions)[0],
                reactions=(
                    reactions[r.id].counts if r.id in reactions else ()
                ),
                deleted=r.id in gone,
            )
            for r in rows
        }
        out: list[RecordRefHit] = []
        for mid in sorted(wanted["messages"]):
            if mid in hits:
                out.append(RecordRefHit("messages", mid, hits[mid]))
        for rid, (mid, mark) in sorted(emoji.items()):
            if mid in hits:
                out.append(RecordRefHit("message_reaction", rid, hits[mid], emoji=mark))
        for rid, mid in sorted(deleted_of.items()):
            if mid in hits:
                out.append(RecordRefHit("message_delete", rid, hits[mid]))
        return tuple(out)

    async def own_sends(
        self, rooms: Sequence[tuple[str, str]], *, start_ms: int, end_ms: int, limit: int,
    ) -> tuple[RecordHit, ...]:
        """Nora's OWN delivered sends in these rooms over `[start_ms, end_ms)`,
        oldest first, at most the `limit` newest (D1, DEC-245): what a session's
        agenda shows beside the messages that raised it, so it can tell what
        they are answering. Only rows `messages` marks hers and `sent` (a queued
        or failed send never reached the room); the same select as every
        transcript read, so a hidden row is absent and an unsent one is marked
        deleted. The caller's rooms are the whole boundary, as for `query`."""
        if not rooms or limit <= 0 or end_ms <= start_ms:
            return ()
        in_rooms = sa.or_(*(
            sa.and_(MESSAGES.c.platform == platform, MESSAGES.c.conversation_id == cid)
            for platform, cid in rooms
        ))
        stmt = (
            transcript_select()
            .add_columns(MESSAGES.c.platform, MESSAGES.c.conversation_id)
            .where(
                in_rooms, MESSAGES.c.ts >= start_ms, MESSAGES.c.ts < end_ms,
                MESSAGES.c.is_nora == 1, MESSAGES.c.delivery_status == "sent",
            )
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            ids = [r.id for r in rows]
            reactions = await reaction_states(conn, ids)
            gone = await self._deleted(conn, ids)
        return tuple(sorted(
            (
                RecordHit(
                    platform=r.platform, conversation_id=r.conversation_id,
                    row=with_reactions([transcript_row(r)], reactions)[0],
                    reactions=(
                        reactions[r.id].counts if r.id in reactions else ()
                    ),
                    deleted=r.id in gone,
                )
                for r in rows
            ),
            key=lambda h: (h.row.ts, h.row.id),
        ))

    @staticmethod
    async def _deleted(conn: Any, ids: list[int]) -> frozenset[int]:
        if not ids:
            return frozenset()
        rows = await conn.execute(
            sa.select(MESSAGE_DELETE.c.message_id)
            .where(MESSAGE_DELETE.c.message_id.in_(ids))
            .distinct()
        )
        return frozenset(r[0] for r in rows)
