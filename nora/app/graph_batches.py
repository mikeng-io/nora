"""Produce aged message batches; graph work has no consumer in phase 1."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memory_admission import GRAPH_BATCH_OUTCOMES, GraphBatchOutcome
from nora.infra.sql.graph_projection import form_batch

log = logging.getLogger(__name__)


class GraphBatchProducer:
    def __init__(self, engine: AsyncEngine, *, metrics: Any) -> None:
        self.engine = engine
        self.metrics = metrics

    def _record(self, outcome: GraphBatchOutcome) -> None:
        if outcome not in GRAPH_BATCH_OUTCOMES:
            raise ValueError("unknown graph batch outcome")
        self.metrics.inc("nora_memory_graph_batches_total", outcome=outcome)

    async def run(self) -> None:
        while True:
            try:
                async with self.engine.begin() as conn:
                    count = await form_batch(conn, now=datetime.now(UTC))
                if count:
                    self._record("ok")
            except Exception:
                log.exception("graph batch production failed")
                self._record("unavailable")
            await asyncio.sleep(1)
