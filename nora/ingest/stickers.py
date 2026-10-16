"""The sticker dedup ladder — CUSTODY, and the identity that makes a repeat
sighting free.

Separated from `nora.ingest.stages.media` because the responsibilities really
are two: that module makes the body's media claims TRUE (fetch, strip, hash,
store, render), and this one decides whether any of that work is needed at all.
Folding the ladder into `_take` would put four cache branches inside the
function whose job is custody.

**There is no model rung: this module is only custody.** Describing a sticker
is the spawned comprehension job's, one job for every medium
(`nora/ingest/comprehension.py`). What lives here is what that job cannot do:
turn a repeat sighting into zero platform RPC, before any bytes move, on a path
the ingest listener awaits.

**Order matters.** Repeats are common and grow as a group's sticker vocabulary
saturates, so the cheapest check runs first.

* **Level 1, identity** (`resolve`) — known at parse time. A hit means ZERO
  platform RPC: the blob Nora already holds IS the answer.
* **Level 2, custody** — a miss fetches once, stores once, and records the
  identity so the next sighting is a level-1 hit. Content-level dedup (the same
  art in a second pack) still happens, one layer up: the comprehension job
  keys on the sha256 and a row that is already comprehended is never read again.

**Nothing here is cached on failure.** A fetch that does not land records no
identity — a remembered identity pointing at a blob Nora does not hold would
serve itself as a custody claim on every later sighting.

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
from dataclasses import dataclass
from typing import Any

from nora.domain.media import MediaBlob, MediaRef, resolve_media_type, sniff_media_type
from nora.domain.ports import (
    BlobStore,
    MediaDescriptionStore,
    MediaSource,
)

log = logging.getLogger("nora.ingest.stickers")


@dataclass(frozen=True)
class StickerOutcome:
    """What one sticker cost and what it yielded.

    `level` is a metric label, not a branch: `identity` / `content` / `none` —
    which rung of the ladder actually answered. It is what makes "the cache is
    working" an observation instead of a belief, and it is a SEPARATE
    vocabulary from `nora_media_fetch_total`'s boolean `cached` label —
    enumerated here so a caller cannot confuse the two.

    It carries no `description`: the comprehension is the spawned job's, keyed
    on this blob's sha256.
    """

    blob: MediaBlob | None
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
        metrics: Any,
    ) -> None:
        self._blobs = blobs
        self._descriptions = descriptions
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
            # `finally` runs regardless — otherwise a cancelled winner would
            # leave every waiter blocked on this future forever. The outer
            # `finally` is the safety net for that one case; this inner block
            # is the ordinary one.
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
            # none for a sticker at all (no `contentType` on
            # `dataMessage.sticker`), so `sniff_media_type` reads the real
            # container off the fetched bytes and wins when it recognises one.
            # Resolved BEFORE hashing: it is what lands in the `MediaBlob`
            # and the interpreter call — an unresolved `""` here would make the
            # interpreter's `_INTERPRETABLE` gate refuse every real Signal
            # sticker. Never invented when
            # neither source resolves one: the declared/guessed fallback
            # stays `""`.
            declared = resolve_media_type(declared=ref.media_type, filename=ref.filename)
            media_type = sniff_media_type(fetched.data) or declared
            # STICKERS SKIP THE STRIP — deliberately; do not "restore consistency"
            # with `stages.media._take`'s strip without re-reading both reasons
            # below.
            #
            # 1. DEC-47 exists to remove GPS/device-id PII from photographs a
            #    PERSON took. Sticker pack art is published, platform-
            #    distributed, and byte-identical for everyone who has the
            #    pack — there is no personal metadata in it to strip, so
            #    re-encoding here is pure loss for no PII benefit.
            # 2. Re-encoding makes the content hash depend on the PILLOW
            #    VERSION, not just the bytes — and `strip_exif` does not pass
            #    `save_all`, so an animated image is silently flattened to
            #    its first frame before it is ever hashed. Both failure
            #    modes land on `media_descriptions`/`sticker_index`, which
            #    are DURABLE caches keyed on this exact hash: a Pillow
            #    upgrade would silently change every sticker's sha256 and
            #    orphan every existing row, and flattening would hide an
            #    animated sticker's later frames from the model.
            #    Hashing the platform's own bytes is stable by construction.
            data = fetched.data
            try:
                sha256 = await self._blobs.put(data=data)
            except Exception:
                log.exception("blob store refused a sticker; recording without custody")
                return StickerOutcome(blob=None, reason="store_failed")
            blob, level = (
                MediaBlob(sha256=sha256, media_type=media_type, size=len(data)),
                "content",
            )

        # LEVEL 2 — the identity write. On an identity hit there is nothing
        # left to record: the row already points at this blob and `resolve`
        # just read it. On a MISS the bytes have just landed in custody, so
        # the identity is written now — that write is what makes every LATER
        # sighting of this sticker a level-1 hit and costs no platform RPC.
        #
        # `description=""` is deliberate and load-bearing: it means "index
        # this identity, describe nothing". `SqlMediaDescriptionStore.remember`
        # keeps whatever description is already on the row rather than
        # clobbering it, so a sticker seen again after the comprehension job
        # has read it does not erase what that job wrote. The description
        # itself is the job's business — `nora/ingest/comprehension.py` —
        # and it is keyed on this same sha256.
        if level == "content":
            await self._descriptions.remember(
                platform=platform, token=ref.fetch_token, blob=blob,
                description="", model="",
            )
        elif data is None:
            # An identity hit. Nothing was fetched, so PROVE custody before
            # returning the blob: `[sticker a3f2c1]` promises `a3f2c1` can be
            # produced, and an identity row outliving its bytes (a rotated key,
            # a pruned volume) would make that promise a lie.
            # One `get` on a hit is the price of the claim.
            try:
                held = await self._blobs.get(key=blob.sha256)
            except Exception:
                log.exception("could not open a held sticker blob")
                held = None
            if not held:
                return StickerOutcome(blob=None, reason="blob_missing")
        return StickerOutcome(blob=blob, level=level)
