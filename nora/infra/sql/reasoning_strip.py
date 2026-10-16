"""`SqlReasoningStripStore` — the durable half of the reasoning-strip
instrument (ADR 0013).

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlToolCallLogStore`/`SqlParticipationStateStore`: no config, clock or
metrics, so a fixture can build it from a bare `AsyncEngine`. `created_at`
arrives on the observation.

**One verb, and it writes.** There is no read method, by design. Nothing under
`nora/` acts on this signal; acting on it would be a behaviour change. A read
verb with no production caller is what
`tests/architecture/test_store_methods_have_production_callers.py` refuses. The
reader of this table is a person with `psql`; the query is in
`docs/runbook.md` beside the table.

**`record_observation`, not `record` or `append`.** Both short names are
already defined on other classes under `nora/` (`ReplyGuard.record`,
`CallLedger.record`, `SqlToolCallLogStore.append`), which would put this method
in that architecture test's `_AMBIGUOUS` set, outside the checked set.

**A plain INSERT, no upsert.** An observation is one invocation's own record
with nothing to conflict with; a second row for the same turn would mean the
watch was flushed twice, and `ReasoningStripWatch.flush` is idempotent so that
cannot happen silently.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.reasoning_strip import (
    ReasoningStripObservation,
    normalise_observation,
)
from nora.infra.sql.schema import REASONING_STRIP_LOG


class SqlReasoningStripStore:
    """Satisfies `nora.domain.reasoning_strip.ReasoningStripStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record_observation(
        self, observation: ReasoningStripObservation
    ) -> None:
        row = normalise_observation(observation)
        stmt = sa.insert(REASONING_STRIP_LOG).values(
            id=row.id,
            path=row.path,
            model=row.model,
            reasoning_effort=row.reasoning_effort,
            tag_id=row.tag_id,
            model_calls=row.model_calls,
            continuation_calls=row.continuation_calls,
            strip_calls=row.strip_calls,
            stripped_blocks=row.stripped_blocks,
            created_at=row.created_at,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
