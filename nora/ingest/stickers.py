"""The sticker dedup ladder and the one vision call at the bottom of it
(sticker spec §4).

Separated from `nora.ingest.stages.media` because the responsibilities really
are two: that module makes the body's media claims TRUE (fetch, strip, hash,
store, render), and this one decides whether any of that work is needed at all.
Folding the ladder into `_take` would put four cache branches inside the
function whose job is custody.

**Order matters and is the whole design.** Identity before content before
model, because each level is cheaper than the next and — crucially — the first
is checkable before any bytes move, on a path the ingest listener awaits.
Measured on real data: 17 sticker sightings across 10 distinct stickers, so 41%
are already repeats before any platform RPC or vision call, and that share only
grows as a group's sticker vocabulary saturates.

* **Level 1, identity** (`resolve`) — known at parse time. A hit means ZERO
  platform RPC and zero vision call.
* **Level 2, content** (`describe` by sha256) — a description is a pure
  function of the bytes, so the same art in a second pack or on the other
  platform is one row and one vision call.
* **Level 3, the model** — and the subtle case: on an identity hit the bytes
  were never fetched, so if the description is missing they come from Nora's
  OWN custody via `BlobStore.get`, still zero platform RPC.

**A failure is NEVER cached.** If the interpreter comes back unavailable, no
`remember` call is made — a remembered failure would serve itself as an answer
on every later sighting.

**Single-flight** sits outside all of it. Signal ingest is serialized, but the
Discord listener is a separate task, so two people sending the same new sticker
seconds apart is a real race, not a theoretical one; without it that costs two
fetches and two model calls for one answer — and two concurrent `remember`
calls racing on the same row.

**`read` is total.** It runs on the ingest path a sticker must never cost the
message over, so any exception from a collaborator degrades to an honest
`unavailable` outcome rather than propagating, and the in-flight entry is
always cleaned up — on the success path and the exception path alike, or a
leaked future would wedge every later sighting of that sticker forever.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from nora.domain.media import MediaBlob, MediaRef, resolve_media_type
from nora.domain.ports import (
    BlobStore,
    MediaDescriptionStore,
    MediaInterpreter,
    MediaSource,
)
from nora.ingest.exif import strip_exif

log = logging.getLogger("nora.ingest.stickers")


@dataclass(frozen=True)
class StickerOutcome:
    """What one sticker cost and what it yielded.

    `description` is RAW. The caller screens it on render, never this module at
    store time — a screen applied before caching is one a cache hit skips.

    `cached` is a metric label, not a branch: `identity` / `content` / `none`.
    It is what makes "the cache is working" an observation instead of a belief.
    """

    blob: MediaBlob | None
    description: str = ""
    cached: str = "none"
    reason: str = ""


class StickerReader:
    """Owns the ladder and the in-flight map. One instance per process, built
    by the composition root and handed to the pipeline — never a module-level
    singleton, which CLAUDE.md's test-seam policy forbids and which would make
    two tests share a cache.
    """

    def __init__(
        self,
        *,
        blobs: BlobStore,
        descriptions: MediaDescriptionStore,
        interpreter: MediaInterpreter,
        model: str,
        metrics: Any,
    ) -> None:
        self._blobs = blobs
        self._descriptions = descriptions
        self._interpreter = interpreter
        self._model = model
        self._metrics = metrics
        # Per-instance, not module-level — the test-seam policy's whole point:
        # a module-level singleton would let one test's in-flight sticker leak
        # into the next test's reader.
        self._inflight: dict[tuple[str, str], asyncio.Future[StickerOutcome]] = {}

    async def read(
        self, *, platform: str, ref: MediaRef, source: MediaSource
    ) -> StickerOutcome:
        """Total: never raises, never leaks the in-flight entry it registers."""
        key = (platform, ref.fetch_token)
        existing = self._inflight.get(key)
        if existing is not None:
            # A second sighting of a sticker already being read. Awaiting the
            # winner is not merely an optimisation: two concurrent `remember`
            # calls for one identity would race on the same row.
            return await existing
        future: asyncio.Future[StickerOutcome] = (
            asyncio.get_running_loop().create_future()
        )
        self._inflight[key] = future
        try:
            outcome = await self._read(platform=platform, ref=ref, source=source)
        except Exception:
            # Total, like every ingest collaborator: a sticker must never cost
            # the message. The waiters get the same honest answer.
            log.exception("sticker read raised; degrading to unavailable")
            outcome = StickerOutcome(blob=None, reason="error")
        finally:
            # Cleaned up on BOTH paths, and before `future.set_result` — no
            # `await` sits between them, so no other task can observe a state
            # where the future is resolved but still claims to be in flight,
            # or vice versa.
            del self._inflight[key]
        future.set_result(outcome)
        return outcome

    async def _read(
        self, *, platform: str, ref: MediaRef, source: MediaSource
    ) -> StickerOutcome:
        # LEVEL 1 — identity. Known at parse time, so a hit costs no RPC at all.
        known = await self._descriptions.resolve(platform=platform, token=ref.fetch_token)
        data: bytes | None = None
        if known is not None:
            blob, cached = known, "identity"
        else:
            fetched = await source.fetch(ref=ref)
            if not fetched.available:
                return StickerOutcome(blob=None, reason=fetched.reason or "unavailable")
            media_type = resolve_media_type(declared=ref.media_type, filename=ref.filename)
            # Strip before hashing, so the key is the key of the PICTURE — the
            # same rule `stages.media._take` follows, for the same reason.
            data = strip_exif(fetched.data, media_type)
            try:
                sha256 = await self._blobs.put(data=data)
            except Exception:
                log.exception("blob store refused a sticker; recording without custody")
                return StickerOutcome(blob=None, reason="store_failed")
            blob, cached = (
                MediaBlob(sha256=sha256, media_type=media_type, size=len(data)),
                "content",
            )

        # LEVEL 2 — content. A description is a pure function of the bytes, so
        # the same art under a second identity is one row and one call.
        existing = await self._descriptions.describe(sha256=blob.sha256)
        if existing is not None:
            return StickerOutcome(blob=blob, description=existing, cached=cached)

        # LEVEL 3 — the model. On an identity hit the bytes were never fetched,
        # so they come from Nora's own custody: still zero platform RPC.
        if data is None:
            try:
                data = await self._blobs.get(key=blob.sha256)
            except Exception:
                log.exception("could not open a held sticker blob")
                data = None
            if not data:
                return StickerOutcome(blob=blob, cached=cached, reason="blob_missing")

        # `nora_sticker_describe_seconds` (sticker spec §5.2): the interpreter
        # call ALONE, not the ladder around it — a cache hit never reaches
        # this line, so the ladder's near-zero lookups cannot dilute what this
        # says about the vision model's own latency.
        started = time.perf_counter()
        result = await self._interpreter.describe(data=data, media_type=blob.media_type)
        self._metrics.observe("nora_sticker_describe_seconds", time.perf_counter() - started)
        if not result.available or not result.text:
            # Never cached. A failure that got remembered would make every
            # later sighting serve the failure as if it were an answer.
            return StickerOutcome(
                blob=blob, cached=cached, reason=result.reason or "vision"
            )
        await self._descriptions.remember(
            platform=platform, token=ref.fetch_token, blob=blob,
            description=result.text, model=self._model,
        )
        return StickerOutcome(blob=blob, description=result.text, cached="none")
