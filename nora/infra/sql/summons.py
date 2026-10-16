"""`SqlSummonsLedger` — the public summons ledger over SQLAlchemy Core
(`nora.domain.summons.SummonsLedger`).

The one property the table exists for is that a summons is claimed ONCE:
`claim` is `INSERT ... ON CONFLICT (message_id) DO NOTHING RETURNING id`, keyed
on the summons card's DM message id, so of any number of concurrent claims on
one card exactly one gets an id. The comment the card resolves to is bound
later (`bind_comment`), and `comment_id` stays UNIQUE, so two cards that
resolve to one comment cannot both be answered.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.summons import OPEN_STATES, SUMMONS_MODES, SUMMONS_STATES, SummonsRow
from nora.infra.sql.schema import INSTAGRAM_SUMMONS

#: What the sweep writes into `bridge_code` for a row a dead process left
#: `claimed` (swept to `failed`) or `flushing` (swept to `ambiguous`): nobody knows
#: whether its comment went out, so it is never retried.
SWEPT_AT_BOOT = "swept_at_boot"


def _row(r: Row[Any]) -> SummonsRow:
    return SummonsRow(
        id=r.id, message_id=r.message_id, comment_id=r.comment_id,
        media_id=r.media_id, shortcode=r.shortcode,
        author_igid=r.author_igid, person_id=r.person_id, mention_at=r.mention_at,
        seen_at=r.seen_at, state=r.state, mode=r.mode, reply_text=r.reply_text,
        reply_comment_id=r.reply_comment_id, bridge_code=r.bridge_code,
        trace_id=r.trace_id, updated_at=r.updated_at,
    )


class SqlSummonsLedger:
    """Satisfies `nora.domain.summons.SummonsLedger`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def claim(
        self, *, message_id: str, media_id: str, shortcode: str,
        author_igid: str, mention_at: datetime | None, mode: str, now: datetime,
    ) -> UUID | None:
        if mode not in SUMMONS_MODES:
            raise ValueError(f"unknown summons mode {mode!r}")
        stmt = (
            pg_insert(INSTAGRAM_SUMMONS)
            .values(
                id=uuid.uuid4(), message_id=message_id, media_id=media_id,
                shortcode=shortcode, author_igid=author_igid, mention_at=mention_at,
                seen_at=now, updated_at=now, state="claimed", mode=mode,
            )
            .on_conflict_do_nothing(index_elements=["message_id"])
            .returning(INSTAGRAM_SUMMONS.c.id)
        )
        async with self._engine.begin() as conn:
            got = (await conn.execute(stmt)).scalar_one_or_none()
        return got

    async def bind_comment(
        self, summons_id: UUID, comment_id: str, *, now: datetime,
    ) -> bool:
        """Record the comment a `claimed` row resolved to, once. `False` when
        the row is not an unbound `claimed` row, or when ANOTHER row already
        holds that comment: the UNIQUE constraint decides, in the one
        statement that writes, so two concurrent binds of one comment cannot
        both succeed."""
        stmt = (
            sa.update(INSTAGRAM_SUMMONS)
            .where(
                INSTAGRAM_SUMMONS.c.id == summons_id,
                INSTAGRAM_SUMMONS.c.state == "claimed",
                INSTAGRAM_SUMMONS.c.comment_id.is_(None),
            )
            .values(comment_id=comment_id, updated_at=now)
        )
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt)
        except sa.exc.IntegrityError:
            return False
        return bool(result.rowcount)

    async def finish(
        self, summons_id: UUID, state: str, *, now: datetime,
        person_id: str | None = None, reply_text: str | None = None,
        reply_comment_id: str | None = None, bridge_code: str | None = None,
        trace_id: str | None = None,
    ) -> bool:
        if state not in SUMMONS_STATES:
            raise ValueError(f"unknown summons state {state!r}")
        values: dict[str, Any] = {"state": state, "updated_at": now}
        for key, value in (
            ("person_id", person_id), ("reply_text", reply_text),
            ("reply_comment_id", reply_comment_id), ("bridge_code", bridge_code),
            ("trace_id", trace_id),
        ):
            if value is not None:
                values[key] = value
        # A final row is final: the WHERE is the state machine, enforced in
        # the one statement that writes, so no read-then-write race exists.
        stmt = (
            sa.update(INSTAGRAM_SUMMONS)
            .where(
                INSTAGRAM_SUMMONS.c.id == summons_id,
                INSTAGRAM_SUMMONS.c.state.in_(OPEN_STATES),
            )
            .values(**values)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def take_for_flush(self, summons_id: UUID, *, now: datetime) -> bool:
        stmt = (
            sa.update(INSTAGRAM_SUMMONS)
            .where(
                INSTAGRAM_SUMMONS.c.id == summons_id,
                INSTAGRAM_SUMMONS.c.state == "staged",
            )
            .values(state="flushing", updated_at=now)
            .returning(INSTAGRAM_SUMMONS.c.id)
        )
        async with self._engine.begin() as conn:
            got = (await conn.execute(stmt)).scalar_one_or_none()
        return got is not None

    async def awaiting_flush(self) -> SummonsRow | None:
        stmt = (
            sa.select(INSTAGRAM_SUMMONS)
            .where(
                INSTAGRAM_SUMMONS.c.state == "staged",
                INSTAGRAM_SUMMONS.c.mode == "live",
            )
            .order_by(INSTAGRAM_SUMMONS.c.seen_at.asc(), INSTAGRAM_SUMMONS.c.id.asc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row(row)

    async def get(self, summons_id: UUID) -> SummonsRow | None:
        stmt = sa.select(INSTAGRAM_SUMMONS).where(INSTAGRAM_SUMMONS.c.id == summons_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row(row)

    async def sweep_stale_claims(
        self, *, now: datetime, older_than: timedelta,
    ) -> dict[str, int]:
        """`{"failed": n, "ambiguous": m}`: how many `claimed` and `flushing`
        rows older than `older_than` (by `updated_at`) it closed."""
        cutoff = now - older_than
        swept = {"failed": 0, "ambiguous": 0}
        async with self._engine.begin() as conn:
            for state, to in (("claimed", "failed"), ("flushing", "ambiguous")):
                result = await conn.execute(
                    sa.update(INSTAGRAM_SUMMONS)
                    .where(
                        INSTAGRAM_SUMMONS.c.state == state,
                        INSTAGRAM_SUMMONS.c.updated_at < cutoff,
                    )
                    .values(state=to, bridge_code=SWEPT_AT_BOOT, updated_at=now)
                )
                swept[to] += int(result.rowcount or 0)
        return swept
