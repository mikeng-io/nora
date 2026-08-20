"""`RoomPortraitReader` plus Dreamer Plan 3's write side, over SQLAlchemy Core
— the portrait sibling of `nora.infra.sql.language`/`nora.infra.sql.exchange`,
much simpler than either: `room_portrait` is a single UPSERT table, one row
per scope, with no evidence layer and no watermark of its own (see
`nora.language.portrait_synthesizer`'s module docstring for why).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.models import RoomPortrait
from nora.infra.sql.schema import ROOM_PORTRAIT

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class SqlRoomPortraitStore:
    """Satisfies `nora.domain.ports.RoomPortraitReader` and
    `nora.language.portrait_synthesizer.RoomPortraitWriter`, consumed by
    `nora.language.portrait_synthesizer.PortraitSynthesizer`."""

    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        self._engine = engine
        self._metrics = metrics

    async def upsert_portrait(
        self, *, scope_id: str, narrative: str,
        lexeme_basis: tuple[str, ...], exchange_basis: tuple[str, ...],
    ) -> None:
        """ONE `INSERT ... ON CONFLICT DO UPDATE` — replaces the whole row,
        never accumulates: a scope's portrait is entirely re-derived on every
        synthesis (see module docstring), so the previous narrative carries
        no information the new one should be merged with."""
        stmt = pg.insert(ROOM_PORTRAIT).values(
            scope_id=scope_id,
            narrative=narrative,
            lexeme_basis=list(lexeme_basis),
            exchange_basis=list(exchange_basis),
            synthesized_at=_now_ms(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ROOM_PORTRAIT.c.scope_id],
            set_={
                "narrative": stmt.excluded.narrative,
                "lexeme_basis": stmt.excluded.lexeme_basis,
                "exchange_basis": stmt.excluded.exchange_basis,
                "synthesized_at": stmt.excluded.synthesized_at,
            },
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def room_portrait(self, *, scope_id: str) -> RoomPortrait:
        """Satisfies `RoomPortraitReader.room_portrait`: NEVER raises — same
        broad `except Exception` as `SqlLanguageStore.room_language`/
        `SqlExchangeStore.room_exchange`, and for the identical reason (this
        read runs beside its siblings in `TranscriptInjection`'s
        `asyncio.gather`)."""
        try:
            stmt = sa.select(ROOM_PORTRAIT.c.narrative).where(
                ROOM_PORTRAIT.c.scope_id == scope_id
            )
            async with self._engine.connect() as conn:
                row = (await conn.execute(stmt)).one_or_none()
            if row is None:
                return RoomPortrait(scope_id=scope_id)
            return RoomPortrait(
                scope_id=scope_id, narrative=row.narrative, generated_ms=_now_ms(),
            )
        except Exception:
            log.exception("room_portrait unavailable for scope %s", scope_id)
            return RoomPortrait(scope_id=scope_id)
