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

from nora.domain.ports import SourceHealthEvent
from nora.infra.sql.schema import SOURCE_HEALTH

log = logging.getLogger("nora.infra.sql.source_health")


class SqlSourceHealthLog:
    """Satisfies `nora.domain.ports.SourceHealthLog`."""

    def __init__(
        self, engine: AsyncEngine, *, clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._clock = clock

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
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except Exception:
            log.exception("source health row not written (%s %s %s)", source, event, outcome)
            return False
        return True
