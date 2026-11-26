"""The Cognitive Graph search embedding worker.

The service embeds Cognitive Graph search rows while `[memory.index] embed`.
Its node is the text-only queue: `graph_search_embed.pick` finds rows with no
vector whose retry backoff has elapsed, each text is embedded as a document,
and the vector is stored with the model's fingerprint only while the text is
unchanged.

Before the first batch, once per process: the server must be serving the
configured model file, or nothing is stamped with a fingerprint it did not
make; then every vector of another fingerprint is cleared, so a model change
is that one statement and the backfill through the ordinary pick.

Every failure of one row, an unexpected exception included, is recorded on
it: its `embed_attempts` rises and the pick backs it off exponentially. An
unavailable embedder ends the tick at the first failure and backs the row off
without bringing it to the give-up cap. `embed_error` records a short class,
never the row's text.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memory_index import EmbedOutcome, nothing_to_embed
from nora.infra.embedder import (
    EmbedderRejectedError,
    EmbedderUnavailableError,
    LlamaEmbedder,
)
from nora.infra.sql import graph_search_embed
from nora.infra.sql.embed_schedule import EMBED_MAX_ATTEMPTS
from nora.infra.sql.graph_search_embed import GraphEmbeddable

__all__ = ["MemoryEmbedWorker"]

log = logging.getLogger("nora.infra.sql.memory_embed")


class MemoryEmbedWorker:
    """Embed Cognitive Graph search text with the configured model fingerprint."""

    def __init__(
        self, engine: AsyncEngine, embedder: LlamaEmbedder, *,
        model: str, model_file: str, batch: int,
        metrics: Any = None, clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._model = model
        self._model_file = model_file
        self._batch = batch
        self._metrics = metrics
        self._clock = clock
        self._ready = False

    async def _prepare(self) -> bool:
        """Confirm the served model once, then clear vectors of every other one."""
        try:
            served = await self._embedder.served_model()
        except (EmbedderUnavailableError, EmbedderRejectedError) as exc:
            log.warning("memory embed: cannot read the served model (%s)", exc)
            return False
        if served != self._model_file:
            log.error(
                "memory embed: the embedder serves %s, not the configured %s; "
                "embedding nothing", served, self._model_file,
            )
            return False
        async with self._engine.begin() as conn:
            cleared = await graph_search_embed.clear_other_models(conn, keep_model=self._model)
        if cleared:
            log.info("memory embed: cleared %d vectors of other models", cleared)
        self._ready = True
        return True

    async def run_once(self) -> int:
        """One batch. Returns the rows tried."""
        if not self._ready and not await self._prepare():
            return 0
        now = self._clock()
        async with self._engine.connect() as conn:
            picked = await graph_search_embed.pick(conn, limit=self._batch, now_ms=int(now * 1000))
        tried = 0
        for item in picked:
            outcome = await self._one(item)
            tried += 1
            self._count(outcome)
            if outcome == "unavailable":
                break
        return tried

    def _count(self, outcome: EmbedOutcome) -> None:
        if self._metrics is not None:
            self._metrics.inc("nora_memory_embed_total", outcome=outcome)

    async def _one(self, item: GraphEmbeddable) -> EmbedOutcome:
        """Embed one row's text and store or record the result."""
        truncated = False
        try:
            if nothing_to_embed(item.kind, item.text):
                await self._failed(item, "empty")
                return "empty"
            embedded = await self._embedder.embed_document(item.text)
            vector = embedded.vector
            truncated = embedded.truncated
            async with self._engine.begin() as conn:
                stored = await graph_search_embed.store(
                    conn, item, vector=vector, model=self._model,
                    now_ms=int(self._clock() * 1000),
                )
            if truncated and stored and self._metrics is not None:
                self._metrics.inc("nora_memory_embed_truncated_total", kind=item.kind)
            return "ok"
        except EmbedderUnavailableError as exc:
            log.warning("memory embed: unavailable (%s)", exc)
            await self._failed(item, f"unavailable:{exc}", counts=False)
            return "unavailable"
        except EmbedderRejectedError as exc:
            log.warning("memory embed: rejected (%s)", exc)
            await self._failed(item, f"rejected:{exc}")
            return "rejected"
        except Exception as exc:  # noqa: BLE001 — recorded on that row, never re-raised
            log.warning("memory embed: error (%s)", type(exc).__name__)
            await self._failed(item, f"error:{type(exc).__name__}")
            return "error"

    async def _failed(
        self, item: GraphEmbeddable, error: str, *, counts: bool = True,
    ) -> None:
        """Record the failure on the row; outages back off without giving it up."""
        async with self._engine.begin() as conn:
            attempts = await graph_search_embed.failed(
                conn, item, error=error, now_ms=int(self._clock() * 1000), counts=counts,
            )
        if counts and attempts >= EMBED_MAX_ATTEMPTS:
            log.warning(
                "memory embed: row %s given up after %d attempts (%s)",
                item.id, attempts, error,
            )
            self._count("given_up")
