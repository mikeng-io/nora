"""`SourceHealthLog` over SQLAlchemy Core: the append-only `source_health`
table.

`record` never raises: it runs beside a presence poll or an adapter health
sample, and a health row that cannot be written must never stop the work it
describes. A failure is logged and answered False; the caller counts it.

`metrics` is injected, never imported (`nora.infra` sits below `nora.obs`,
`SqlRecordStore.__init__` has the argument).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.ports import HealthRow, SourceHealthEvent
from nora.infra.sql.item_registration import SqlItemRegistration, epoch_ms
from nora.infra.sql.schema import SOURCE_HEALTH

log = logging.getLogger("nora.infra.sql.source_health")


class SqlSourceHealthLog:
    """Satisfies `nora.domain.ports.SourceHealthLog`."""

    def __init__(
        self, engine: AsyncEngine, *, clock: Callable[[], float] = time.time,
        registration: SqlItemRegistration | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._registration = registration or engine.get_execution_options().get("item_registration")

    async def record(
        self, *, source: str, scope: str, event: SourceHealthEvent, outcome: str,
        at_ms: int, detail: str = "",
    ) -> bool:
        stmt = sa.insert(SOURCE_HEALTH).values(
            source=source, scope=scope, event=event, outcome=outcome,
            detail=detail, at_ms=at_ms,
            recorded_at=datetime.fromtimestamp(self._clock(), tz=UTC),
        )
        try:
            registration = self._registration
            units = registration.scope.health_units(source, scope) if registration else ()
            async with self._engine.begin() as conn:
                for unit in units or (None,):
                    values = await registration.planes(
                        conn, SOURCE_HEALTH, unit=unit, occurred_at=epoch_ms(at_ms),
                    ) if registration else {}
                    await conn.execute(stmt.values(**values))
        except Exception:
            log.exception("source health row not written (%s %s %s)", source, event, outcome)
            return False
        return True

    async def transitions(
        self, *, source: str, since_ms: int,
    ) -> tuple[HealthRow, ...] | None:
        h = SOURCE_HEALTH
        stmt = (
            sa.select(h.c.source, h.c.scope, h.c.event, h.c.outcome, h.c.at_ms, h.c.detail)
            .where(h.c.source == source, h.c.event == "transition", h.c.at_ms >= since_ms)
            .order_by(h.c.at_ms, h.c.id)
        )
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
        except Exception:
            log.exception("source health transitions not read (%s)", source)
            return None
        return tuple(
            HealthRow(source=r.source, scope=r.scope, event=r.event, outcome=r.outcome,
                      at_ms=r.at_ms, detail=r.detail)
            for r in rows
        )
