"""The embedding worker (design/unified-memory/65-build-step-3-vectors.md V1,
25-memory-graph.md §7 «after any of the above: vectors»).

One batch per tick of the nora process's memory drain loop
(`nora.app.runtime.memory_drain_loop`), only while `[memory_index] embed`.
The node is the queue (65 §1.1): `embed_pick` finds live, embeddable nodes
with no vector, each is embedded and stored with the model's fingerprint, and
lexical search finds a node before its vector exists.

**What a node is embedded from.** An attachment whose own message records an
image type (`nora.domain.memory_index.embeds_bytes`: a photo, a sticker, a
GIF) is embedded from its bytes, read from the blob store; any other node,
and an image whose bytes Nora no longer holds, from its text as a document.

**Failure never loops.** A node that fails is counted, its `embed_attempts`
rises and `embed_pick` backs it off exponentially (`EMBED_BACKOFF_*`). An
unavailable embedder (or blob store) ends the tick at the first failure, so
a dead server costs one call per tick. `embed_error` records a short class
(`unavailable:HTTP 503`, `rejected:HTTP 400`, `empty`), and nothing here
logs or records a node's text.

**The image throttle.** An image costs about 3.4 s of norty's CPU, a text
about 0.4 s (65 §0). At most `images_per_hour` images are embedded in any
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

from nora.domain.memory_index import EmbedOutcome, embeds_bytes
from nora.domain.ports import BlobStore
from nora.infra.embedder import EmbedderRejectedError, EmbedderUnavailableError, LlamaEmbedder
from nora.infra.sql import memory_index
from nora.infra.sql.memory_index import Embeddable

__all__ = ["MemoryEmbedWorker"]

log = logging.getLogger("nora.infra.sql.memory_embed")

_HOUR = 3600.0


class MemoryEmbedWorker:
    """See the module docstring. `model` is the fingerprint stored with each
    vector (`nora.infra.embedder.fingerprint`); `blobs` is None when the
    deployment holds no media bytes, and then every node is embedded from
    its text."""

    def __init__(
        self, engine: AsyncEngine, embedder: LlamaEmbedder, blobs: BlobStore | None, *,
        model: str, batch: int, images_per_hour: int, metrics: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._embedder = embedder
        self._blobs = blobs
        self._model = model
        self._batch = batch
        self._images_per_hour = images_per_hour
        self._metrics = metrics
        self._clock = clock
        self._images: collections.deque[float] = collections.deque()
        # Until a pick finds no node of another model, picks look for them
        # too (`embed_pick`'s `other_models`): a scan, paid once per process.
        # ponytail: a model change needs a restart (config), so once per
        # process covers it; `nora memory rebuild --embeddings-only` is the
        # operator's lever when it does not.
        self._other_models = True

    def _image_budget(self, now: float) -> int:
        while self._images and self._images[0] <= now - _HOUR:
            self._images.popleft()
        return self._images_per_hour - len(self._images)

    async def run_once(self) -> int:
        """One batch. Returns the nodes tried."""
        now = self._clock()
        media = self._blobs is None or self._image_budget(now) > 0
        async with self._engine.connect() as conn:
            picked = await memory_index.embed_pick(
                conn, limit=self._batch, now_ms=int(now * 1000), model=self._model,
                other_models=self._other_models, media=media,
            )
            types = await memory_index.media_types(
                conn, [(n.source_id, n.sha256 or "") for n in picked if n.kind == "media"],
            )
        if len(picked) < self._batch:
            self._other_models = False
        tried = 0
        for item in picked:
            media_type = types.get(item.source_id, "") if item.kind == "media" else ""
            outcome = await self._one(item, media_type)
            if outcome is None:
                continue
            tried += 1
            if self._metrics is not None:
                self._metrics.inc("nora_memory_embed_total", outcome=outcome)
            if outcome == "unavailable":
                break
        return tried

    async def _one(self, item: Embeddable, media_type: str) -> EmbedOutcome | None:
        """Embed one node and store or count the result. None: an image
        left for a later tick, the throttle having run out in this batch."""
        by_bytes = False
        vector: list[float] | None = None
        try:
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
                    vector = await self._embedder.embed_image(data, media_type)
            if vector is None:
                # Not an image, or an image whose bytes are no longer held:
                # its text, as a document.
                if not item.text.strip():
                    await self._failed(item, "empty", by_bytes=False)
                    return "empty"
                vector = await self._embedder.embed_document(item.text)
        except (EmbedderUnavailableError, _BlobUnavailableError) as exc:
            prefix = "blob" if isinstance(exc, _BlobUnavailableError) else "unavailable"
            log.warning("memory embed: %s (%s)", prefix, exc)
            await self._failed(item, f"{prefix}:{exc}", by_bytes=by_bytes)
            return "unavailable"
        except EmbedderRejectedError as exc:
            log.warning("memory embed: rejected (%s)", exc)
            await self._failed(item, f"rejected:{exc}", by_bytes=by_bytes)
            return "rejected"
        async with self._engine.begin() as conn:
            await memory_index.embed_store(
                conn, item, vector=vector, model=self._model,
                now_ms=int(self._clock() * 1000), by_bytes=by_bytes,
            )
        return "ok"

    async def _failed(self, item: Embeddable, error: str, *, by_bytes: bool) -> None:
        async with self._engine.begin() as conn:
            await memory_index.embed_failed(
                conn, item, error=error, now_ms=int(self._clock() * 1000), by_bytes=by_bytes,
            )


class _BlobUnavailableError(RuntimeError):
    """The blob store could not be read; the message is an exception class."""
