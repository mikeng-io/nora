"""`SqlRecordQuery` — the Record read behind `record_query`
(design/cognition-loop/15-map.md §4, migration step 2).

One method: the messages of some rooms in an interval, optionally one
person's, optionally matching some text, each with its newest edit folded in
(`transcript_select`, the one select every transcript read shares), its
reactions and whether it was deleted.

**Which rooms is the caller's decision, made before this runs.** This store
reads exactly the `(platform, conversation_id)` pairs it is handed; the
lineage predicate (`nora.agent.lineage`) chose them for the turn's room. A
store that filtered afterwards would have read another room's words into the
process first.

**Lexical search is pg_trgm** (design/dreamer-agent-loop/24-embedding-seam.md
§2, measured on Cantonese): a message matches when its CURRENT text (its
newest edit, else its body) CONTAINS the words (`ilike`, the note tier's
operator and reason, `nora.infra.sql.notes`), or when pg_trgm's word
similarity finds them inside it at `WORD_SIMILARITY_MIN` or above. The second
half is 24 §2's operator: a CJK run is one token to full-text search, and
plain `similarity` scored the true in-sentence match 0.18, under its own
default. Containment ranks first, then word similarity, then the newest; a
hit that does not contain the words is marked `near`.
`idx_messages_body_trgm` (migration `0071_lineage`) serves both operators on
unedited messages. **A deleted message never matches text, and an earlier
version of an edited one is never searched**: a hit is a claim that the words
are there, and it would make that claim about words their author took back.

`__init__(self, engine)` and nothing else, the house shape for a store.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.record_query import RECORD_QUERY_MAX, RecordHit, RecordQuery
from nora.infra.sql.audit import transcript_row, transcript_select
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_EDIT, MESSAGE_REACTION, MESSAGES

__all__ = ["WORD_SIMILARITY_MIN", "SqlRecordQuery"]

#: pg_trgm's word similarity at which a message counts as mentioning the
#: words. 0.5 is what 24 §2 measured for the real case, a three-character
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
            # would prove the words were there even with the body withheld
            # (review of the first cut). So the current text is the newest
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
            reactions = await self._reactions(conn, ids)
            gone = await self._deleted(conn, ids)
        near = frozenset(r.id for r in rows if needle and not r.contains)
        hits = sorted(
            (
                RecordHit(
                    platform=r.platform, conversation_id=r.conversation_id,
                    row=transcript_row(r), reactions=reactions.get(r.id, ()),
                    deleted=r.id in gone,
                )
                for r in rows
            ),
            key=lambda h: (h.row.ts, h.row.id),
        )
        return tuple(hits), more, near

    @staticmethod
    async def _reactions(
        conn: Any, ids: list[int],
    ) -> dict[int, tuple[tuple[str, int], ...]]:
        """Each message's reactions standing now: a person's newest row per
        emoji, counted unless it took the reaction back (`removed`)."""
        if not ids:
            return {}
        latest = (
            sa.select(
                MESSAGE_REACTION.c.message_id, MESSAGE_REACTION.c.emoji,
                MESSAGE_REACTION.c.removed,
            )
            .where(MESSAGE_REACTION.c.message_id.in_(ids))
            .distinct(
                MESSAGE_REACTION.c.message_id, MESSAGE_REACTION.c.sender_id,
                MESSAGE_REACTION.c.emoji,
            )
            .order_by(
                MESSAGE_REACTION.c.message_id, MESSAGE_REACTION.c.sender_id,
                MESSAGE_REACTION.c.emoji, MESSAGE_REACTION.c.id.desc(),
            )
            .subquery()
        )
        stmt = (
            sa.select(latest.c.message_id, latest.c.emoji, sa.func.count())
            .where(latest.c.removed == 0)
            .group_by(latest.c.message_id, latest.c.emoji)
            .order_by(latest.c.message_id, latest.c.emoji)
        )
        out: dict[int, list[tuple[str, int]]] = defaultdict(list)
        for message_id, emoji, count in (await conn.execute(stmt)).all():
            out[message_id].append((emoji, int(count)))
        return {k: tuple(v) for k, v in out.items()}

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
