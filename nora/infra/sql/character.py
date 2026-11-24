"""Closed response episodes and addressed messages for Cognition Lessons.

Evidence bodies include stored attachment handles such as `[image a3f2c1]`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import Episode, RoomMessage
from nora.domain.lessons import NAMES_HER
from nora.infra.sql.audit import transcript_row, transcript_select, turn_id_for_message
from nora.infra.sql.identity_views import project_messages
from nora.infra.sql.mobility import current_build_id
from nora.infra.sql.schema import (
    CONSOLIDATION_WATERMARK,
    MESSAGES,
    ROLLUP_RESPONSE,
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
        out = project_messages(rows)
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

    async def episodes(
        self, *, unit: str, after: int, limit: int,
    ) -> tuple[list[Episode], int]:
        """The newest closed episodes of the current build, oldest first.

        Each has her send's text and the replies quoting it in its window.
        The cursor is the newest eligible send id, including episodes beyond
        the limit.
        """
        where = [
            ROLLUP_RESPONSE.c.build_id == current_build_id(),
            ROLLUP_RESPONSE.c.unit == unit,
            ROLLUP_RESPONSE.c.outcome.notin_(("open", "unknown")),
            ROLLUP_RESPONSE.c.message_id > after,
        ]
        head = sa.select(sa.func.max(ROLLUP_RESPONSE.c.message_id)).where(*where)
        send = MESSAGES.alias("send")
        stmt = (
            sa.select(
                ROLLUP_RESPONSE.c.message_id, ROLLUP_RESPONSE.c.platform,
                ROLLUP_RESPONSE.c.conversation_id, ROLLUP_RESPONSE.c.sent_ts,
                ROLLUP_RESPONSE.c.valid_to_ms, ROLLUP_RESPONSE.c.outcome,
                ROLLUP_RESPONSE.c.reactions, ROLLUP_RESPONSE.c.edits,
                ROLLUP_RESPONSE.c.follows, send.c.body,
                turn_id_for_message(send).label("turn_id"),
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
                    follows=int(r.follows), turn_id=r.turn_id,
                    replies=tuple(transcript_row(x) for x in replies),
                ))
        return out, int(cursor or after)
