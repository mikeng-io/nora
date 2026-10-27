"""The embedding worker.

One batch per tick of the nora process's memory drain loop
(`nora.app.runtime.memory_drain_loop`), only while `[memory.index] embed`.
The node is the queue: `embed_pick` finds live, embeddable nodes
with no vector, each is embedded and stored with the model's fingerprint, and
lexical search finds a node before its vector exists.

**Before the first batch**, once per process: the server must be serving the
configured model file (/props `model_path`), or nothing is stamped with a
fingerprint it did not make; then every vector of another fingerprint is
cleared (`clear_embeddings`), so a model change is that one statement and the
backfill through the ordinary pick.

**What a node is embedded from.** An attachment whose own message records an
image type, or a social item's medium whose bytes custody recorded as one
(`nora.domain.memory_index.embeds_bytes`: a photo, a sticker, a GIF), is
embedded from its bytes, read from the blob store; any other node,
and an image whose bytes Nora no longer holds, from its text as a document.
The store records which (`embed_input`), so only a change to THAT input
clears the vector. A social item whose text is its label alone (no caption,
no reading yet) is recorded `empty`, not embedded (`nothing_to_embed`): a
reading landing changes its text and frees it.

**No node escapes the bookkeeping, and none loops.** Every failure of one
node, an unexpected exception included, is recorded on it: its
`embed_attempts` rises and `embed_pick` backs it off exponentially, and after
`EMBED_MAX_ATTEMPTS` failures of the input itself (rejected, empty, error) it
is not picked again (`given_up`). An unavailable embedder (or blob store)
ends the tick at the first failure, so a dead server costs one call per
tick; that attempt backs the node off too, but never to the cap, so an
outage gives up on nothing. `embed_error` records a short class
(`unavailable:HTTP 503`, `rejected:HTTP 400`, `error:KeyError`, `empty`), and
nothing here logs or records a node's text.

**A text cut to fit** the server's micro-batch (`LlamaEmbedder.
embed_document`'s `truncated`) is stored like any other, its vector standing
for the head of the text, and counted on `nora_memory_embed_truncated_total
{kind}` once its vector is stored: there is no chunking, and that
counter is what says when there should be.

**The image throttle.** An image costs far more CPU than a text.
At most `images_per_hour` images are embedded in any
hour; once spent, the pick leaves attachments out until it frees, so texts
are never queued behind images.
"""

from __future__ import annotations

import collections
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.memory_index import (
    ATTACHMENT_KINDS,
    EmbedOutcome,
    embeds_bytes,
    nothing_to_embed,
)
from nora.domain.ports import BlobStore
from nora.infra.embedder import (
    EmbedderRejectedError,
    EmbedderUnavailableError,
    LlamaEmbedder,
)
from nora.infra.sql import memory_index
from nora.infra.sql.memory_index import EMBED_MAX_ATTEMPTS, Embeddable

__all__ = ["MemoryEmbedWorker"]

log = logging.getLogger("nora.infra.sql.memory_embed")

_HOUR = 3600.0


class MemoryEmbedWorker:
    """See the module docstring. `model` is the fingerprint stored with each
    vector (`nora.infra.embedder.fingerprint`) and `model_file` the GGUF the
    server must be serving; `blobs` is None when the deployment holds no
    media bytes, and then every node is embedded from its text."""

    def __init__(
        self, engine: AsyncEngine, embedder: LlamaEmbedder, blobs: BlobStore | None, *,
        model: str, model_file: str, batch: int, images_per_hour: int,
        metrics: Any = None, clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._blobs = blobs
        self._model = model
        self._model_file = model_file
        self._batch = batch
        self._images_per_hour = images_per_hour
        self._metrics = metrics
        self._clock = clock
        self._images: collections.deque[float] = collections.deque()
        self._ready = False

    def _image_budget(self, now: float) -> int:
        while self._images and self._images[0] <= now - _HOUR:
            self._images.popleft()
        return self._images_per_hour - len(self._images)

    async def _prepare(self) -> bool:
        """Once per process: the served model is the configured one, then
        every other model's vector is cleared. False (retried next tick)
        while the server cannot say, or says another file."""
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
            cleared = await memory_index.clear_embeddings(conn, keep_model=self._model)
        if cleared:
            log.info("memory embed: cleared %d vectors of other models", cleared)
        self._ready = True
        return True

    async def run_once(self) -> int:
        """One batch. Returns the nodes tried."""
        if not self._ready and not await self._prepare():
            return 0
        now = self._clock()
        media = self._blobs is None or self._image_budget(now) > 0
        async with self._engine.connect() as conn:
            picked = await memory_index.embed_pick(
                conn, limit=self._batch, now_ms=int(now * 1000), media=media,
            )
            types = await memory_index.media_types(
                conn, [(n.source_id, n.sha256 or "") for n in picked if n.kind == "media"],
                social=[
                    (n.source_id, n.sha256 or "") for n in picked if n.kind == "social_media"
                ],
            )
        tried = 0
        for item in picked:
            media_type = types.get(item.source_id, "") if item.kind in ATTACHMENT_KINDS else ""
            outcome = await self._one(item, media_type)
            if outcome is None:
                continue
            tried += 1
            self._count(outcome)
            if outcome == "unavailable":
                break
        return tried

    def _count(self, outcome: EmbedOutcome) -> None:
        if self._metrics is not None:
            self._metrics.inc("nora_memory_embed_total", outcome=outcome)

    async def _one(self, item: Embeddable, media_type: str) -> EmbedOutcome | None:
        """Embed one node and store or record the result. None: an image
        left for a later tick, the throttle having run out in this batch."""
        by_bytes = False
        truncated = False
        try:
            vector: list[float] | None = None
            if self._blobs is not None and item.sha256 and embeds_bytes(media_type):
                if self._image_budget(self._clock()) <= 0:
                    return None
                try:
                    data = await self._blobs.get(key=item.sha256)
                except Exception as exc:  # any store failure is «unavailable»
                    raise _BlobUnavailableError(type(exc).__name__) from exc
                if data is not None:
                    by_bytes = True
                    self._images.append(self._clock())
                    vector = await self._embedder.embed_image(data)
            if vector is None:
                # Not an image, or an image whose bytes are no longer held:
                # its text, as a document.
                if nothing_to_embed(item.kind, item.text):
                    await self._failed(item, "empty", by_bytes=False)
                    return "empty"
                embedded = await self._embedder.embed_document(item.text)
                vector = embedded.vector
                truncated = embedded.truncated
            async with self._engine.begin() as conn:
                stored = await memory_index.embed_store(
                    conn, item, vector=vector, model=self._model,
                    now_ms=int(self._clock() * 1000), by_bytes=by_bytes,
                )
            # Only a vector that was stored stands for a cut text: a node
            # that changed meanwhile is embedded again, and counted then.
            if truncated and stored and self._metrics is not None:
                self._metrics.inc("nora_memory_embed_truncated_total", kind=item.kind)
            return "ok"
        except (EmbedderUnavailableError, _BlobUnavailableError) as exc:
            prefix = "blob" if isinstance(exc, _BlobUnavailableError) else "unavailable"
            log.warning("memory embed: %s (%s)", prefix, exc)
            await self._failed(item, f"{prefix}:{exc}", by_bytes=by_bytes, counts=False)
            return "unavailable"
        except EmbedderRejectedError as exc:
            log.warning("memory embed: rejected (%s)", exc)
            await self._failed(item, f"rejected:{exc}", by_bytes=by_bytes)
            return "rejected"
        except Exception as exc:  # noqa: BLE001 — recorded on that node, never re-raised
            # Never re-raised: an exception that escaped would leave the node
            # unrecorded, first in every pick, and wedge the worker on it.
            log.warning("memory embed: error (%s)", type(exc).__name__)
            await self._failed(item, f"error:{type(exc).__name__}", by_bytes=by_bytes)
            return "error"

    async def _failed(
        self, item: Embeddable, error: str, *, by_bytes: bool, counts: bool = True,
    ) -> None:
        """Record the failure on the node. `counts` only for a failure of the
        input itself: an unavailable embedder or blob store backs the node
        off but never gives up on it (`memory_index.embed_failed`)."""
        async with self._engine.begin() as conn:
            attempts = await memory_index.embed_failed(
                conn, item, error=error, now_ms=int(self._clock() * 1000), by_bytes=by_bytes,
                counts=counts,
            )
        if counts and attempts >= EMBED_MAX_ATTEMPTS:
            log.warning(
                "memory embed: node %d given up after %d attempts (%s)",
                item.id, attempts, error,
            )
            self._count("given_up")


class _BlobUnavailableError(RuntimeError):
    """The blob store could not be read; the message is an exception class."""
