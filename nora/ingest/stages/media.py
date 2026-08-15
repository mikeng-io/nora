"""Stage 2.5 — take custody of the bytes, BEFORE the record claims them.

A9 says SQLite is THE RECORD. For media it held a placeholder whose referent
lived in another process's tree, under another process's retention: prune
signal-cli, or re-link the device, and the row still said an image existed
while nothing in the system could produce it. This stage is what makes the
claim true — it copies the bytes into a content-addressed store Nora owns and
puts the hash in the same INSERT as the body that names it.

**It runs INLINE, ahead of the audit write, and that is a deliberate departure
from `memory_feed`'s pattern.** The instruction to study `memory_feed`'s
injected `spawn` was right to point here, and the two cases differ in the one
way that decides it:

* `memory_feed` sends a message to Honcho, which is explicitly never
  load-bearing. If it lands late, or never, the RECORD is unaffected.
* Custody is part OF the record. `[image a3f2c1]` is a promise that `a3f2c1`
  can be produced. Spawning the copy would write that promise first and keep it
  second — a window in which the audit row asserts custody that does not exist,
  which is the exact A9 violation this change exists to end, reintroduced in a
  smaller size. And a spawned failure could not degrade the body, because the
  body would already be written.

The cost of inline is a stalled ingest sequence while bytes move, and it is
paid down rather than ignored: the fetch is one loopback HTTP call with a
short timeout, and `FilesystemBlobStore` does every filesystem call in a worker
thread, so the event loop keeps serving delivery, health and the metrics
endpoint throughout. What waits is the NEXT message on the same listener —
which is the same ordering the audit already imposes.

**Degrading honestly is most of this module.** Any attachment whose bytes do
not arrive leaves the body exactly as the parser wrote it — `[image]`, no
handle — and increments `nora_media_custody_total{status=...}` with the reason.
The message is still recorded, in full, with its envelope. Nothing is silent
and nothing is claimed.

**Bytes never reach a prompt.** This stage produces a type and a key. Nora
cannot read an image and this does not change that; a retrieval tool, if one is
ever wanted, is a separate decision with a separate model behind it.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.domain.media import (
    MediaBlob,
    MediaRef,
    body_with_handles,
    label_for,
    placeholder_suffix,
    resolve_media_type,
)
from nora.domain.models import NormalizedMessage
from nora.domain.ports import BlobStore, MediaSource
from nora.ingest.exif import strip_exif
from nora.platform.registry import MediaSourceRegistry

log = logging.getLogger("nora.ingest.media")


async def run(
    *,
    message: NormalizedMessage,
    sources: MediaSourceRegistry | None,
    blobs: BlobStore | None,
    metrics: Any,
) -> NormalizedMessage:
    """Return `message` with every attachment Nora could copy in her custody.

    Total: never raises, never drops a message. A message with no attachments
    is returned unchanged without touching either collaborator, so the ordinary
    case — every message this deployment has ever seen — costs one `if`.

    `sources`/`blobs` are `None` when media custody is not wired at the
    composition root at all — COUNTED as `status="not_configured"` rather than
    passed over in silence: a message that carried an attachment while custody
    was switched off is precisely the loss an operator needs to see, and it is
    invisible in every other series.

    When custody IS wired but `sources` brought no fetcher for THIS message's
    platform, that is a different operator problem — `status="not_supported"`
    — and is resolved once, here, rather than re-derived per attachment.
    """
    if not message.media:
        return message
    for ref in message.media:
        metrics.inc(
            "nora_media_attachments_total",
            platform=message.platform,
            label=label_for(ref),
        )
    source = sources.for_platform(message.platform) if sources is not None else None
    if source is None or blobs is None:
        # `not_configured` when custody is off entirely; `not_supported` when
        # custody is on but THIS platform brought no fetcher. Two different
        # things to go fix, so two labels.
        status = "not_configured" if sources is None or blobs is None else "not_supported"
        _count(metrics, platform=message.platform, status=status, n=len(message.media))
        return message

    held: list[MediaBlob] = []
    handles: list[str] = []
    for ref in message.media:
        blob = await _take(ref=ref, source=source, blobs=blobs, metrics=metrics,
                           platform=message.platform)
        # A gap is a real position, not a skipped one: handles are matched to
        # placeholders by INDEX, so a failed attachment must contribute an
        # empty handle rather than shifting every later one onto the wrong
        # placeholder.
        handles.append(blob.handle if blob else "")
        if blob:
            held.append(blob)

    labels = tuple(label_for(ref) for ref in message.media)
    rendered = placeholder_suffix(labels, tuple(handles))
    # R-18's floor, applied to exactly the text THIS stage composed — the
    # placeholder run, not the sender's own words, which are the audit's
    # business and not this stage's to censor. Nothing composed here can carry
    # an identifier by construction (the labels are a closed set, a handle is
    # hex), so this asserts that property rather than filtering for it; a hit
    # means the construction broke. It costs the handles, never the message.
    kind = scan_identifiers(rendered)
    if kind is not None:
        log.error(
            "media placeholders would have carried a %s identifier — rendering "
            "without handles; this should be impossible, see nora/domain/media.py",
            kind,
        )
        metrics.inc("nora_egress_blocks_total", kind=kind)
        return replace(message, media_blobs=tuple(held))

    return replace(
        message,
        body=body_with_handles(message.body, labels, tuple(handles)),
        media_blobs=tuple(held),
    )


async def _take(
    *,
    ref: MediaRef,
    source: MediaSource,
    blobs: BlobStore,
    metrics: Any,
    platform: str,
) -> MediaBlob | None:
    """One attachment, from the platform's store into Nora's. None on any
    failure, always counted with the reason."""
    fetched = await source.fetch(ref=ref)
    if not fetched.available:
        _count(metrics, platform=platform, status=fetched.reason or "unavailable")
        return None
    # The type as RESOLVED (declared, else inferred from the filename), decided
    # BEFORE the hash because it decides whether to strip: the stored row and
    # the rendered label can never disagree about what this is, and the strip is
    # driven off the registered top-level type, not a byte sniff.
    media_type = resolve_media_type(declared=ref.media_type, filename=ref.filename)
    # Strip EXIF/embedded metadata from images BEFORE hashing (DEC-47). The
    # content key is the sha256 of THESE bytes, so stripping first is what makes
    # the key the key of the PICTURE — the same photo with different GPS dedups
    # to one blob — and removes the GPS/device-id PII from what Nora holds.
    # Non-images pass through unchanged; see nora/ingest/exif.py.
    plaintext = strip_exif(fetched.data, media_type)
    try:
        key = await blobs.put(data=plaintext)
    except Exception:
        # The blob store is the disk. A full or read-only volume must cost the
        # attachment and not the message — the row still records that something
        # arrived, and this counter is what says custody is broken.
        log.exception("blob store refused %d bytes; recording without custody", len(plaintext))
        _count(metrics, platform=platform, status="store_failed")
        return None
    _count(metrics, platform=platform, status="stored")
    return MediaBlob(
        sha256=key,
        media_type=media_type,
        # MEASURED from what is HELD — the stripped plaintext, the same bytes
        # `get` returns — not the platform's claim and not the pre-strip length.
        size=len(plaintext),
        filename=ref.filename,
    )


def _count(metrics: Any, *, platform: str, status: str, n: int = 1) -> None:
    metrics.inc("nora_media_custody_total", n, platform=platform, status=status)
