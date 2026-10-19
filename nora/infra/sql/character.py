"""`SqlCharacterEvidence` — what wakes a Character session, and what it is
handed.

Each entity kind counts its own evidence since its key's watermark, and reads
the newest of it when it wakes. A count is the dream loop's wake signal (`lag`
for a room, generalised); a read is what the session is handed:

- **a person**: their human messages across their unit's rooms, by
  Record id.
- **Nora**: `response` episodes of the current Rollup build for the
  unit that are neither `open` (it can still change) nor `unknown` (an outage,
  not silence, R-27, so nothing to read), by her send's Record id: her send's
  text, the replies that quoted it inside the window, and the episode's counts
  and outcome.
- **a unit**: new revisions of its rooms' cultures, by COUNT: a culture
  revision's id is a uuid with no order, and the chain is append-only, so the
  number of revisions a unit's rooms hold only grows and is the cursor.

Evidence reads are SELECTs. Historical and social progress reuse the existing
consolidation watermarks; their explicit cursor methods write no Persona claim.

**Media is not joined.** A person's lines are read by this module's own SQL,
so a photo they posted reaches their session as its stored body, the bare
`[image a3f2c1]` handle, not as what it showed (the media join lives above
`nora.infra`). The session writes what a person is like from what they SAY.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import EPISODE_EVIDENCE_OUTCOMES, Episode, RoomMessage
from nora.domain.lessons import NAMES_HER
from nora.domain.social_stream import from_us, to_us
from nora.infra.sql.audit import transcript_row, transcript_select
from nora.infra.sql.mobility import current_build_id
from nora.infra.sql.schema import (
    CONSOLIDATION_WATERMARK,
    CULTURE_REVISION,
    MEMORY_EDGE,
    MESSAGES,
    PERSONA,
    ROLLUP_RESPONSE,
    ROLLUP_TIE,
)

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

    async def message_head(self) -> int:
        """The newest message id in the Record (0 when empty): a cheap primary
        key read, so a count that found nothing can say how far it looked."""
        async with self._engine.connect() as conn:
            return int((await conn.execute(
                sa.select(sa.func.max(MESSAGES.c.id)),
            )).scalar_one_or_none() or 0)

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

    async def history_state(self, *, key: str) -> tuple[int, int]:
        """Freeze a pass's Record highwater once, independently of recent updates.

        Two existing watermark rows hold boundary and progress. Their explicit
        names let the CLI restart an incorrectly completed shadow pass without
        rewriting Persona history or the normal runtime watermark.
        """
        t = CONSOLIDATION_WATERMARK
        async with self._engine.begin() as conn:
            head = sa.select(sa.func.coalesce(sa.func.max(MESSAGES.c.id), 0))
            await conn.execute(
                pg.insert(CONSOLIDATION_WATERMARK)
                .values(
                    consolidator_name="persona-person:history-boundary",
                    scope_id=key,
                    last_message_id=head.scalar_subquery(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing()
            )
            rows = (
                await conn.execute(
                    sa.select(t.c.consolidator_name, t.c.last_message_id).where(
                        t.c.scope_id == key,
                        t.c.consolidator_name.in_(
                            ["persona-person:history-boundary", "persona-person:history"]
                        ),
                    )
                )
            ).all()
        values = {str(r.consolidator_name): int(r.last_message_id) for r in rows}
        return int(values["persona-person:history-boundary"]), int(
            values.get("persona-person:history", 0)
        )

    async def advance_history(self, *, key: str, cursor: int) -> None:
        t = CONSOLIDATION_WATERMARK
        async with self._engine.begin() as conn:
            await conn.execute(
                pg.insert(CONSOLIDATION_WATERMARK)
                .values(
                    consolidator_name="persona-person:history",
                    scope_id=key,
                    last_message_id=cursor,
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=[t.c.consolidator_name, t.c.scope_id],
                    set_={
                        "last_message_id": sa.func.greatest(t.c.last_message_id, cursor),
                        "updated_at": sa.func.now(),
                    },
                )
            )

    async def historical_counts(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str,
        cursor: int, through: int,
    ) -> tuple[int, int]:
        """Receipt counts from the same eligible own/mention union as pages."""
        eligible = sa.select(MESSAGES.c.id).where(
            _in_rooms(rooms), MESSAGES.c.is_nora == 0, MESSAGES.c.hidden_at.is_(None),
            MESSAGES.c.id <= through,
            sa.or_(MESSAGES.c.person_id == person_id,
                sa.cast(MESSAGES.c.mention_persons_json, pg.JSONB).contains([person_id])),
        ).subquery()
        async with self._engine.connect() as conn:
            row = (await conn.execute(sa.select(
                sa.func.count(), sa.func.count().filter(eligible.c.id <= cursor)
            ).select_from(eligible))).one()
        return int(row[0]), int(row[1])

    async def historical_page(
        self,
        *,
        rooms: Sequence[tuple[str, str]],
        person_id: str,
        after: int,
        through: int,
        limit: int,
    ) -> list[RoomMessage]:
        """One oldest-first UNION, then one limit, never two truncated streams.

        Read ingestion's resolved mention list directly: history also works
        while graph projection is off. All eligibility/disclosure rules are
        identical for own messages and mentions.
        """
        stmt = (
            transcript_select()
            .add_columns(MESSAGES.c.conversation_id)
            .where(
                _in_rooms(rooms),
                MESSAGES.c.is_nora == 0,
                MESSAGES.c.hidden_at.is_(None),
                MESSAGES.c.id > after,
                MESSAGES.c.id <= through,
                sa.or_(
                    MESSAGES.c.person_id == person_id,
                    sa.cast(MESSAGES.c.mention_persons_json, pg.JSONB).contains([person_id]),
                ),
            )
            .order_by(MESSAGES.c.id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in rows]

    # -- a room --------------------------------------------------------------

    def _room(self, rooms: Sequence[tuple[str, str]], after: int) -> list[Any]:
        """Every visible message in `rooms` past `after`, whoever said it —
        except HERS (the echo rule: her own words can never support a claim,
        so a collective's session is never handed them)."""
        return [
            _in_rooms(rooms),
            MESSAGES.c.is_nora == 0,
            MESSAGES.c.hidden_at.is_(None),
            MESSAGES.c.id > after,
        ]

    async def room_count(
        self, *, rooms: Sequence[tuple[str, str]], after: int,
    ) -> int:
        """How many such messages: a room Persona's wake count."""
        stmt = sa.select(sa.func.count()).select_from(MESSAGES).where(
            *self._room(rooms, after),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def room_since(
        self, *, rooms: Sequence[tuple[str, str]], after: int, limit: int,
    ) -> tuple[list[RoomMessage], int]:
        """The NEWEST `limit` of those messages, oldest first, and the cursor:
        the newest id among ALL of them, read or not (`person_since`'s shape)."""
        if limit <= 0:
            return [], after
        where = self._room(rooms, after)
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

    _MENTIONS_JOIN = MEMORY_EDGE.join(
        MESSAGES, MESSAGES.c.id == sa.cast(MEMORY_EDGE.c.src_id, sa.BigInteger)
    )

    def _mentions(
        self, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
    ) -> list[Any]:
        return [
            _in_rooms(rooms),
            MEMORY_EDGE.c.src_kind == "message",
            MEMORY_EDGE.c.rel == "mentions",
            MEMORY_EDGE.c.dst_kind == "person",
            MEMORY_EDGE.c.dst_id == person_id,
            MESSAGES.c.person_id != person_id,
            MESSAGES.c.is_nora == 0,
            MESSAGES.c.hidden_at.is_(None),
            MESSAGES.c.id > after,
        ]

    async def mentions_since(
        self, *, rooms: Sequence[tuple[str, str]], person_id: str, after: int,
        limit: int,
    ) -> tuple[list[RoomMessage], int]:
        """The NEWEST `limit` of the messages in `rooms` that mention
        `person_id` past Record id `after` (the index's `mentions` edges,
        written from `messages.mention_persons_json` at projection; the author
        and Nora never count as mentioners of themselves here), oldest first,
        and the cursor: the newest id among ALL of them, read or not (same
        shape as `person_since`)."""
        if limit <= 0:
            return [], after
        where = self._mentions(rooms, person_id, after)
        stmt = (
            transcript_select().add_columns(MESSAGES.c.conversation_id)
            .join(MEMORY_EDGE,
                  MESSAGES.c.id == sa.cast(MEMORY_EDGE.c.src_id, sa.BigInteger))
            .where(*where)
            .order_by(MESSAGES.c.id.desc()).limit(limit)
        )
        head = (
            sa.select(sa.func.max(MESSAGES.c.id)).select_from(self._MENTIONS_JOIN)
            .where(*where)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            cursor = (await conn.execute(head)).scalar_one_or_none()
        out = [RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in rows]
        out.reverse()
        return out, int(cursor or after)

    async def windowed(
        self, *, rooms: Sequence[tuple[str, str]], since_ms: int, until_ms: int,
        limit: int,
    ) -> list[RoomMessage]:
        """Every visible message in `rooms` with `ts` in `(since_ms, until_ms]`,
        oldest first, at most the NEWEST `limit`: what the Persona verdict
        judge reads a hypothesis's prediction window by (45 §3 — «the Record
        rows inside it»). Hers included: the observable may be an exchange, not
        one person's lines. Past `limit` the window's OLDEST rows are dropped —
        a ponytail ceiling: windows are days, and a window too large to read is
        judged on what was said lately."""
        if limit <= 0 or until_ms <= since_ms:
            return []
        stmt = (
            transcript_select().add_columns(MESSAGES.c.conversation_id)
            .where(
                _in_rooms(rooms), MESSAGES.c.hidden_at.is_(None),
                MESSAGES.c.ts > since_ms, MESSAGES.c.ts <= until_ms,
            )
            .order_by(MESSAGES.c.id.desc()).limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        out = [RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in rows]
        out.reverse()
        return out

    async def rows_by_id(self, *, ids: Sequence[int]) -> list[RoomMessage]:
        """The Record rows `ids` names that are not hidden, each with its
        room: what a Persona session checks a citation an older account
        carried by (its time, speaker and room, 45 §4)."""
        if not ids:
            return []
        stmt = (transcript_select().add_columns(MESSAGES.c.conversation_id)
                .where(MESSAGES.c.id.in_(list(ids))).order_by(MESSAGES.c.id))
        async with self._engine.connect() as conn:
            found = (await conn.execute(stmt)).all()
        return [RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in found]

    async def tie_totals(
        self, *, unit: str, person_id: str,
    ) -> list[tuple[str, int, int, int, int, int, int]]:
        """One person's Rollup ties in `unit`'s current build, summed over every
        week: `(other person, replies out, replies in, mentions out, mentions
        in, reactions out, reactions in)`, most replies first. Never
        `colocation_ms`: a Persona session holds no presence (R-31)."""
        t = ROLLUP_TIE.c
        mine = t.person_a == person_id
        other = sa.case((mine, t.person_b), else_=t.person_a)

        def out(ab: Any, ba: Any) -> Any:
            return sa.func.sum(sa.case((mine, ab), else_=ba))

        stmt = (
            sa.select(
                other.label("other"),
                out(t.replies_ab, t.replies_ba).label("ro"),
                out(t.replies_ba, t.replies_ab).label("ri"),
                out(t.mentions_ab, t.mentions_ba).label("mo"),
                out(t.mentions_ba, t.mentions_ab).label("mi"),
                out(t.reactions_ab, t.reactions_ba).label("xo"),
                out(t.reactions_ba, t.reactions_ab).label("xi"),
            )
            .where(t.build_id == current_build_id(), t.unit == unit,
                   sa.or_(t.person_a == person_id, t.person_b == person_id))
            .group_by(other)
        )
        async with self._engine.connect() as conn:
            found = (await conn.execute(stmt)).all()
        rows = [(r.other, int(r.ro), int(r.ri), int(r.mo), int(r.mi), int(r.xo), int(r.xi))
                for r in found]
        return sorted(rows, key=lambda r: (-(r[1] + r[2]), r[0]))

    # -- corrections (Lessons L4) ---------------------------------------------

    async def addressed_since(
        self, *, rooms: Sequence[tuple[str, str]], since_ms: int, until_ms: int,
        limit: int, self_ids: frozenset[str] = frozenset(),
    ) -> list[RoomMessage]:
        """Human messages in `rooms` sent in `(since_ms, until_ms]` that address
        Nora: they quote one of her messages in the same room, @-mention one of
        `self_ids` (`mentions_json`: Signal stores a mention as U+FFFC in the
        body, so the body alone never shows it), or name her («nora» as a word,
        any case). Oldest first, at most the newest `limit`. Whether one is a
        CORRECTION is the caller's (`nora.domain.lessons.is_correction`)."""
        if limit <= 0 or until_ms <= since_ms:
            return []
        hers = MESSAGES.alias("hers")
        quotes_her = sa.exists(
            sa.select(hers.c.id).where(
                hers.c.is_nora == 1,
                hers.c.platform == MESSAGES.c.platform,
                hers.c.conversation_id == MESSAGES.c.conversation_id,
                hers.c.ts == MESSAGES.c.quoted_ts,
            )
        )
        stmt = (
            transcript_select().add_columns(MESSAGES.c.conversation_id)
            .where(
                _in_rooms(rooms), MESSAGES.c.is_nora == 0,
                MESSAGES.c.hidden_at.is_(None),
                MESSAGES.c.ts > since_ms, MESSAGES.c.ts <= until_ms,
                sa.or_(
                    sa.and_(MESSAGES.c.quoted_ts.isnot(None), quotes_her),
                    MESSAGES.c.body.op("~*")(NAMES_HER),
                    *(MESSAGES.c.mentions_json.contains(f'"{i}"') for i in sorted(self_ids)),
                ),
            )
            .order_by(MESSAGES.c.id.desc()).limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        out = [RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in rows]
        out.reverse()
        return out

    async def last_run_ms(self, *, adaptor: str, key: str) -> int | None:
        """When `adaptor` last ran for `key` (its watermark row's `updated_at`,
        which every run that is not `unavailable` sets), in epoch ms, or None
        when it never has."""
        stmt = sa.select(CONSOLIDATION_WATERMARK.c.updated_at).where(
            CONSOLIDATION_WATERMARK.c.consolidator_name == adaptor,
            CONSOLIDATION_WATERMARK.c.scope_id == key,
        )
        async with self._engine.connect() as conn:
            at = (await conn.execute(stmt)).scalar_one_or_none()
        return None if at is None else int(at.timestamp() * 1000)

    # -- a person's social items -----------------------------------------------

    async def social_cursor(self, *, adaptor: str, key: str) -> tuple[int, int]:
        """How far `adaptor`'s sessions for `key` have been handed the
        person's social items, as `(cursor, read_at)` in microseconds since
        the epoch: the `recorded_at` of the last item handed, and when the
        read that settled last began (`(0, 0)`: none yet). Kept on its own
        `consolidation_watermark` row (`<adaptor>:social`), beside the key's
        message watermark, because an item has no Record id: `last_message_id`
        holds the cursor (BIGINT, 0115) and `updated_at` the read's time."""
        t = CONSOLIDATION_WATERMARK
        stmt = sa.select(t.c.last_message_id, t.c.updated_at).where(
            t.c.consolidator_name == f"{adaptor}:social", t.c.scope_id == key,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        if row is None:
            return 0, 0
        return int(row.last_message_id), to_us(row.updated_at)

    async def advance_social_cursor(
        self, *, adaptor: str, key: str, cursor: int, read_at: int,
    ) -> None:
        """Move the cursor to `cursor` and the read's time to `read_at`,
        neither ever back."""
        t = CONSOLIDATION_WATERMARK
        at = from_us(read_at)
        stmt = (
            pg.insert(CONSOLIDATION_WATERMARK)
            .values(consolidator_name=f"{adaptor}:social", scope_id=key,
                    last_message_id=cursor, updated_at=at)
            .on_conflict_do_update(
                index_elements=[t.c.consolidator_name, t.c.scope_id],
                set_={"last_message_id": sa.func.greatest(t.c.last_message_id, cursor),
                      "updated_at": sa.func.greatest(t.c.updated_at, at)},
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

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

    async def persona_revision_count(self, *, keys: Sequence[str]) -> int:
        """Monotone account revisions, not timestamp-offset paging of line UUIDs."""
        async with self._engine.connect() as conn:
            return int((await conn.execute(sa.select(sa.func.coalesce(
                sa.func.sum(PERSONA.c.revision), 0)).where(
                    PERSONA.c.key.in_(list(keys))))).scalar_one())

    async def persona_revisions_after(
        self, *, keys: Sequence[str], after: int, limit: int,
    ) -> tuple[list[str], int]:
        # The consumer reads ALL current room contexts in one session; this
        # cursor measures the snapshot, not individual line evidence.
        total = await self.persona_revision_count(keys=keys)
        return (list(keys), total) if total > after and limit > 0 else ([], after)

    # -- Nora ----------------------------------------------------------------

    def _closed(self, unit: str, after: int, *, evidence_only: bool = True) -> list[Any]:
        """Settled episodes of the current build for `unit` past `after`.
        `evidence_only` (Character · Nora) narrows them to `Episode.is_evidence`;
        off (Lessons), every CLOSED episode is read,
        `unanswered` included, because the Lessons session is handed each send's
        text and judges whether an unanswered one asked something."""
        closed = [
            ROLLUP_RESPONSE.c.build_id == current_build_id(),
            ROLLUP_RESPONSE.c.unit == unit,
            ROLLUP_RESPONSE.c.outcome.notin_(("open", "unknown")),
            ROLLUP_RESPONSE.c.message_id > after,
        ]
        if not evidence_only:
            return closed
        return [
            *closed,
            # EVIDENCE ONLY, `Episode.is_evidence` in SQL (DEC-290 (4)): people
            # answered, or silence was proved against an expected answer.
            # `unanswered` (none expected) is neither, and `open`/`unknown`
            # are not yet / an outage (R-27). Counting them would wake
            # Nora's session on episodes it cannot cite.
            sa.or_(
                ROLLUP_RESPONSE.c.outcome.in_(sorted(EPISODE_EVIDENCE_OUTCOMES)),
                ROLLUP_RESPONSE.c.replies > 0,
                ROLLUP_RESPONSE.c.reactions > 0,
                ROLLUP_RESPONSE.c.edits > 0,
            ),
            # ...and not one whose only responses are unquoted follows: the
            # session is not shown those messages (`Episode.is_evidence`).
            sa.not_(sa.and_(
                ROLLUP_RESPONSE.c.follows > 0,
                ROLLUP_RESPONSE.c.replies == 0,
                ROLLUP_RESPONSE.c.reactions == 0,
                ROLLUP_RESPONSE.c.edits == 0,
            )),
        ]

    async def episode_count(
        self, *, unit: str, after: int, evidence_only: bool = True,
    ) -> int:
        """`response` episodes of the current build for `unit` whose send is
        past Record id `after` that ARE EVIDENCE (`Episode.is_evidence`). An
        `open` one is never counted (its outcome can still change), nor an
        `unknown` one (no proof the platform was up), nor, with `evidence_only`,
        an `unanswered` one (none was expected, so nothing happened)."""
        stmt = sa.select(sa.func.count()).select_from(ROLLUP_RESPONSE).where(
            *self._closed(unit, after, evidence_only=evidence_only),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def episodes(
        self, *, unit: str, after: int, limit: int, evidence_only: bool = True,
    ) -> tuple[list[Episode], int]:
        """The newest `limit` of those evidence episodes, oldest first, each with her
        send's text and the replies that quoted it inside its window, and the
        cursor: the newest send id among all of them."""
        where = self._closed(unit, after, evidence_only=evidence_only)
        head = sa.select(sa.func.max(ROLLUP_RESPONSE.c.message_id)).where(*where)
        send = MESSAGES.alias("send")
        stmt = (
            sa.select(
                ROLLUP_RESPONSE.c.message_id, ROLLUP_RESPONSE.c.platform,
                ROLLUP_RESPONSE.c.conversation_id, ROLLUP_RESPONSE.c.sent_ts,
                ROLLUP_RESPONSE.c.valid_to_ms, ROLLUP_RESPONSE.c.outcome,
                ROLLUP_RESPONSE.c.reactions, ROLLUP_RESPONSE.c.edits,
                ROLLUP_RESPONSE.c.follows, send.c.body,
            )
            .select_from(ROLLUP_RESPONSE.outerjoin(
                send, send.c.id == ROLLUP_RESPONSE.c.message_id,
            ))
            .where(*where)
            .order_by(ROLLUP_RESPONSE.c.message_id.desc())
            .limit(max(limit, 0))
        )
        async with self._engine.connect() as conn:
            cursor = (await conn.execute(head)).scalar_one_or_none()
            rows = (await conn.execute(stmt)).all() if limit > 0 else []
            out: list[Episode] = []
            for r in reversed(rows):
                replies = (await conn.execute(
                    transcript_select().where(
                        MESSAGES.c.platform == r.platform,
                        MESSAGES.c.conversation_id == r.conversation_id,
                        MESSAGES.c.quoted_ts == r.sent_ts,
                        MESSAGES.c.is_nora == 0,
                        MESSAGES.c.ts >= r.sent_ts,
                        MESSAGES.c.ts <= r.valid_to_ms,
                    ).order_by(MESSAGES.c.id)
                )).all()
                out.append(Episode(
                    send_id=int(r.message_id), room=r.conversation_id,
                    text=r.body or "", outcome=r.outcome,
                    reactions=int(r.reactions), edits=int(r.edits),
                    follows=int(r.follows),
                    replies=tuple(transcript_row(x) for x in replies),
                ))
        return out, int(cursor or after)
