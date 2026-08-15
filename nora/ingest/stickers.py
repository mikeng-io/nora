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

from nora.domain.media import MediaBlob, MediaRef, resolve_media_type, sniff_media_type
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

    `level` is a metric label, not a branch: `identity` / `content` / `none` —
    which rung of the ladder actually answered. It is what makes "the cache is
    working" an observation instead of a belief, and it is a SEPARATE
    vocabulary from `nora_media_fetch_total`'s boolean `cached` label; the two
    were once spelled the same way and that was the wrong call (whole-branch
    review, 2026-08-15) — enumerated here so a caller cannot confuse the two.
    """

    blob: MediaBlob | None
    description: str = ""
    level: str = "none"
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
            # NESTED try, deliberately: `except Exception` below must not catch
            # `asyncio.CancelledError` (a `BaseException` since 3.8) — a
            # cancelled winner has to stay cancelled, not degrade to an
            # "error" outcome it never actually reached. But a cancellation
            # DOES skip `future.set_result` on this line, and the outer
            # `finally` runs regardless — which is exactly how a cancelled
            # winner used to leave every waiter blocked on this future
            # forever ("LOSER HUNG"). The outer `finally`'s job is the safety
            # net for that one case; this inner block is the ordinary one.
            try:
                outcome = await self._read(platform=platform, ref=ref, source=source)
            except Exception:
                # Total, like every ingest collaborator: a sticker must never
                # cost the message. The waiters get the same honest answer.
                log.exception("sticker read raised; degrading to unavailable")
                outcome = StickerOutcome(blob=None, reason="error")
            future.set_result(outcome)
        finally:
            # Cleaned up on EVERY path — success, the caught-exception
            # degrade, and cancellation alike.
            del self._inflight[key]
            if not future.done():
                # Reached only when the inner `try` was cancelled before
                # `future.set_result` above ran. Resolving it here — rather
                # than leaving it pending — is what keeps `read` total for
                # every OTHER task already awaiting this same future; the
                # cancelled task itself still propagates `CancelledError`
                # past this `finally`, unaffected.
                future.set_result(StickerOutcome(blob=None, reason="cancelled"))
        return await future

    async def _read(
        self, *, platform: str, ref: MediaRef, source: MediaSource
    ) -> StickerOutcome:
        # LEVEL 1 — identity. Known at parse time, so a hit costs no RPC at all.
        known = await self._descriptions.resolve(platform=platform, token=ref.fetch_token)
        data: bytes | None = None
        if known is not None:
            blob, level = known, "identity"
        else:
            fetched = await source.fetch(ref=ref)
            if not fetched.available:
                return StickerOutcome(blob=None, reason=fetched.reason or "unavailable")
            # The type as DECLARED (or filename-guessed) — but Signal declares
            # none for a sticker at all (spec §1: no `contentType` on
            # `dataMessage.sticker`), so `sniff_media_type` reads the real
            # container off the fetched bytes and wins when it recognises one.
            # BEFORE `strip_exif`/hashing: the media type decides whether
            # stripping happens and is what lands in the `MediaBlob` and the
            # interpreter call — an unresolved `""` here is what made the
            # interpreter's `_INTERPRETABLE` gate refuse every real Signal
            # sticker (whole-branch review C1). Never invented when neither
            # source resolves one: the declared/guessed fallback stays `""`.
            declared = resolve_media_type(declared=ref.media_type, filename=ref.filename)
            media_type = sniff_media_type(fetched.data) or declared
            # Strip before hashing, so the key is the key of the PICTURE — the
            # same rule `stages.media._take` follows, for the same reason.
            data = strip_exif(fetched.data, media_type)
            try:
                sha256 = await self._blobs.put(data=data)
            except Exception:
                log.exception("blob store refused a sticker; recording without custody")
                return StickerOutcome(blob=None, reason="store_failed")
            blob, level = (
                MediaBlob(sha256=sha256, media_type=media_type, size=len(data)),
                "content",
            )

        # LEVEL 2 — content. A description is a pure function of the bytes, so
        # the same art under a second identity is one row and one call.
        existing = await self._descriptions.describe(sha256=blob.sha256)
        if existing is not None:
            return StickerOutcome(blob=blob, description=existing, level=level)

        # LEVEL 3 — the model. On an identity hit the bytes were never fetched,
        # so they come from Nora's own custody: still zero platform RPC.
        if data is None:
            try:
                data = await self._blobs.get(key=blob.sha256)
            except Exception:
                log.exception("could not open a held sticker blob")
                data = None
            if not data:
                # `blob=None`, not `blob=blob`: custody just PROVED the bytes
                # are absent (an identity hit whose blob store no longer has
                # them), so returning the blob here would claim
                # `[sticker a3f2c1]`'s promise — "a3f2c1 can be produced" —
                # for a hash that cannot be (whole-branch review I2).
                return StickerOutcome(blob=None, reason="blob_missing")

        # `nora_sticker_describe_seconds` (sticker spec §5.2): the interpreter
        # call ALONE, not the ladder around it — a cache hit never reaches
        # this line, so the ladder's near-zero lookups cannot dilute what this
        # says about the vision model's own latency.
        started = time.perf_counter()
        result = await self._interpreter.describe(data=data, media_type=blob.media_type)
        self._metrics.observe("nora_sticker_describe_seconds", time.perf_counter() - started)
        if not result.available or not result.text:
            # Never cached, AND `level="none"` — not the `level` this call
            # entered with. A fetch-then-fail (or an identity hit whose model
            # call then fails) reached this line because NOTHING answered it
            # yet; reporting the ladder rung that merely supplied the bytes
            # over-reports "the cache is working" on the one path an operator
            # is watching it for (whole-branch review I4).
            return StickerOutcome(blob=blob, level="none", reason=result.reason or "vision")
        await self._descriptions.remember(
            platform=platform, token=ref.fetch_token, blob=blob,
            description=result.text, model=self._model,
        )
        return StickerOutcome(blob=blob, description=result.text, level="none")
