"""`SqlSummonsLedger` — the public summons ledger over SQLAlchemy Core
(`nora.domain.summons.SummonsLedger`, design/instagram-adoption/19).

The one property the table exists for is that a summons is claimed ONCE:
`claim` is `INSERT ... ON CONFLICT (comment_id) DO NOTHING RETURNING id`, so of
any number of concurrent claims on one comment exactly one gets an id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.summons import SUMMONS_MODES, SUMMONS_STATES, SummonsRow
from nora.infra.sql.schema import INSTAGRAM_SUMMONS

#: What the boot sweep writes into `bridge_code` for a row a dead process left
#: `claimed`: nobody knows whether its comment went out, so it is never retried.
SWEPT_AT_BOOT = "swept_at_boot"


def _row(r: Row[Any]) -> SummonsRow:
    return SummonsRow(
        id=r.id, comment_id=r.comment_id, media_id=r.media_id, shortcode=r.shortcode,
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
        self, *, comment_id: str, media_id: str, shortcode: str,
        author_igid: str, mention_at: datetime | None, mode: str, now: datetime,
    ) -> UUID | None:
        if mode not in SUMMONS_MODES:
            raise ValueError(f"unknown summons mode {mode!r}")
        stmt = (
            pg_insert(INSTAGRAM_SUMMONS)
            .values(
                id=uuid.uuid4(), comment_id=comment_id, media_id=media_id,
                shortcode=shortcode, author_igid=author_igid, mention_at=mention_at,
                seen_at=now, updated_at=now, state="claimed", mode=mode,
            )
            .on_conflict_do_nothing(index_elements=["comment_id"])
            .returning(INSTAGRAM_SUMMONS.c.id)
        )
        async with self._engine.begin() as conn:
            got = (await conn.execute(stmt)).scalar_one_or_none()
        return got

    async def finish(
        self, summons_id: UUID, state: str, *, now: datetime,
        person_id: str | None = None, reply_text: str | None = None,
        reply_comment_id: str | None = None, bridge_code: str | None = None,
        trace_id: str | None = None,
    ) -> None:
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
        stmt = (
            sa.update(INSTAGRAM_SUMMONS)
            .where(INSTAGRAM_SUMMONS.c.id == summons_id)
            .values(**values)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def seen(self, comment_ids: tuple[str, ...]) -> frozenset[str]:
        if not comment_ids:
            return frozenset()
        stmt = sa.select(INSTAGRAM_SUMMONS.c.comment_id).where(
            INSTAGRAM_SUMMONS.c.comment_id.in_(comment_ids),
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).scalars().all()
        return frozenset(rows)

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

    async def sweep_stale_claims(self, *, now: datetime) -> int:
        stmt = (
            sa.update(INSTAGRAM_SUMMONS)
            .where(INSTAGRAM_SUMMONS.c.state == "claimed")
            .values(state="failed", bridge_code=SWEPT_AT_BOOT, updated_at=now)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)
