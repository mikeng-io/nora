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

**Bytes reach a prompt for STICKERS and VOICE NOTES ONLY, and nothing else.**
This stage still produces a type and a key for every attachment — Nora cannot
read an arbitrary image or video, and this does not change that. A sticker is
published pack art, identical for everyone who has the pack, not a photograph
of somebody's life, so `stickers` (when wired) hands its bytes to one vision
call and the description rides in the body beside the handle. A voice note is
conversation content, not an attachment description, so `transcriber` (when
wired) hands its bytes to local STT the same way — see `[speech.stt]` and
`nora/infra/speech.py`. See `nora/ingest/stickers.py` for the dedup ladder
that makes the sticker call cheap and the reasoning behind restricting vision
to stickers alone.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.domain.media import (
    MediaBlob,
    MediaObject,
    MediaRef,
    body_with_handles,
    label_for,
    placeholder_suffix,
    resolve_media_type,
    sanitize_description,
)
from nora.domain.models import NormalizedMessage
from nora.domain.ports import BlobStore, MediaObjectStore, MediaSource, Transcriber
from nora.ingest.exif import strip_exif
from nora.ingest.stickers import StickerReader
from nora.platform.registry import MediaSourceRegistry

log = logging.getLogger("nora.ingest.media")


async def run(
    *,
    message: NormalizedMessage,
    sources: MediaSourceRegistry | None,
    blobs: BlobStore | None,
    media_store: MediaObjectStore | None = None,
    metrics: Any,
    stickers: StickerReader | None = None,
    transcriber: Transcriber | None = None,
    language: str = "",
    max_description_chars: int = 200,
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

    `transcriber` is the `[speech.stt]` lane: when present AND a ref is a
    voice note, the custody stage transcribes the bytes INLINE (before the
    audit write) and the transcript rides in the body beside the handle —
    `[voice note #hex: 喂你今日...]`. A voice note is conversation CONTENT,
    not an attachment description; a song is an audio file and stays on the
    bare-handle path. A failed transcription is recorded with the bare handle
    (a note is still a note), never a dropped message and never a guessed
    transcript (R-27).
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
    descriptions: list[str] = []
    for ref in message.media:
        if ref.sticker and stickers is not None:
            # STICKERS ONLY (spec §2.2, §5.3). Everything else keeps the
            # pull-based path below: custody plus a bare placeholder, described
            # only if Nora chooses to call the `media_fetch` tool. A sticker is
            # published pack art; a photo is somebody's life — see this
            # module's docstring and `nora/ingest/stickers.py`.
            outcome = await stickers.read(
                platform=message.platform, ref=ref, source=source
            )
            blob, raw = outcome.blob, outcome.description
            _count(metrics, platform=message.platform,
                   status="stored" if blob else (outcome.reason or "unavailable"))
            # `nora_sticker_descriptions_total` — the dedup ladder's cache hit
            # rate as an OBSERVATION, not a belief (spec §5.2's justification
            # for putting a vision call inline on the ingest path at all).
            # `level` is `StickerOutcome.level`'s own vocabulary
            # (identity/content/none), NOT `nora_media_fetch_total`'s
            # true/false `cached` — a separate label name on purpose, after
            # the two were once spelled the same way (whole-branch review).
            metrics.inc(
                "nora_sticker_descriptions_total",
                platform=message.platform,
                status="ok" if raw else (outcome.reason or "unavailable"),
                level=outcome.level,
            )
        else:
            taken = await _take(ref=ref, source=source, blobs=blobs, metrics=metrics,
                                platform=message.platform)
            blob = taken[0] if taken is not None else None
            raw = ""
            # VOICE NOTES ONLY. A voice note is conversation CONTENT — the
            # custody stage transcribes it inline (before the audit write) so
            # Nora sees the words, not a bare handle. A song is an audio file
            # and stays on the pull-based path; this gate is the parser's
            # `voiceNote` flag, NOT the MIME top-level type (spec §4.2, and
            # `nora/domain/media.py` — voice note is a SEPARATE axis from
            # audio/*). The transcript rides the same `descriptions` slot a
            # sticker description would, so it inherits `sanitize_description`
            # and the R-18 `scan_identifiers` egress guard below — a
            # transcript is a member's words verbatim, with higher stakes than
            # model-written prose.
            if taken is not None and ref.voice_note and transcriber is not None:
                # `plaintext` is the SAME bytes `_take` just held to compute
                # `blob` — reusing them here (rather than a second
                # `blobs.get` round trip through the just-written key) is both
                # cheaper and safer: it cannot raise on a disk/decrypt error
                # the way re-reading the store could, so a voice note that
                # was JUST written always reaches the transcriber.
                blob, plaintext = taken
                stt_outcome = await transcriber.transcribe(
                    data=plaintext, media_type=blob.media_type, language=language,
                )
                if stt_outcome.available:
                    raw = stt_outcome.text
                    metrics.inc(
                        "nora_speech_transcriptions_total",
                        platform=message.platform, status="ok",
                    )
                else:
                    metrics.inc(
                        "nora_speech_transcriptions_total",
                        platform=message.platform,
                        status=stt_outcome.reason or "unavailable",
                    )
        # A gap is a real position, not a skipped one: handles are matched to
        # placeholders by INDEX, so a failed attachment must contribute an
        # empty handle rather than shifting every later one onto the wrong
        # placeholder.
        handles.append(blob.handle if blob else "")
        # A description rides only WITH a handle; `render_placeholder` enforces
        # that too, and the empty string here is what makes a failed read read
        # as `[sticker a3f2c1]` rather than as a colon with nothing after it.
        descriptions.append(
            sanitize_description(raw, max_chars=max_description_chars)
            if blob and raw else ""
        )
        if blob:
            held.append(blob)
            # DEC-138 §3 M4 (inbound write path): every byte Nora takes
            # custody of gains a `source='inbound'` row in the unified media
            # index — the index is "one row per byte Nora holds", not only
            # described/generated bytes. Stickers are EXCLUDED here because
            # `StickerReader.remember` already wrote their row (and their
            # `sticker_index` identity) through the same `media_objects`
            # upsert, in one transaction; writing again would be a second
            # writer racing the first. First-source-wins (the store's
            # `on_conflict_do_nothing`) keeps the row immutable afterwards;
            # provenance is never overwritten. A store failure costs the
            # index entry, never the message — same degrade-as-bonus
            # discipline as every other store in this module.
            if not (ref.sticker and stickers is not None):
                try:
                    if media_store is not None:
                        await media_store.save(obj=MediaObject(
                            sha256=blob.sha256, media_type=blob.media_type,
                            size=blob.size, source="inbound",
                            description="", describe_model=None,
                            provenance={
                                "platform": message.platform,
                                "conversation_id": message.conversation_id,
                                "sender_id": message.sender_id,
                            },
                            created_at_ms=message.timestamp,
                        ))
                except Exception:
                    log.exception("media ingest: media_objects save failed")

    labels = tuple(label_for(ref) for ref in message.media)
    rendered = placeholder_suffix(labels, tuple(handles), tuple(descriptions))
    # R-18's floor, applied to exactly the text THIS stage composed — the
    # placeholder run, not the sender's own words, which are the audit's
    # business and not this stage's to censor. The LABEL and HANDLE halves
    # cannot carry an identifier by construction (a closed label set plus
    # hex); the DESCRIPTION half is free text a vision model wrote about a
    # sticker's bytes, so this is no longer an assertion on an impossible
    # state — it is a real guard. A hit costs the descriptions, never the
    # handles: the sticker is unread, not lost, and the blob really is held.
    kind = scan_identifiers(rendered)
    if kind is not None:
        log.warning(
            "a media description would have carried a %s identifier — "
            "rendering without descriptions", kind,
        )
        metrics.inc("nora_egress_blocks_total", kind=kind)
        return replace(
            message,
            body=body_with_handles(message.body, labels, tuple(handles)),
            media_blobs=tuple(held),
        )

    return replace(
        message,
        body=body_with_handles(message.body, labels, tuple(handles), tuple(descriptions)),
        media_blobs=tuple(held),
    )


async def _take(
    *,
    ref: MediaRef,
    source: MediaSource,
    blobs: BlobStore,
    metrics: Any,
    platform: str,
) -> tuple[MediaBlob, bytes] | None:
    """One attachment, from the platform's store into Nora's. `None` on any
    failure, always counted with the reason. The plaintext bytes ride
    alongside the blob so a caller that needs them (voice-note transcription)
    is not forced into a second `blobs.get` round trip for what this
    function already holds."""
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
    blob = MediaBlob(
        sha256=key,
        media_type=media_type,
        # MEASURED from what is HELD — the stripped plaintext, the same bytes
        # `get` returns — not the platform's claim and not the pre-strip length.
        size=len(plaintext),
        filename=ref.filename,
    )
    return blob, plaintext


def _count(metrics: Any, *, platform: str, status: str, n: int = 1) -> None:
    metrics.inc("nora_media_custody_total", n, platform=platform, status=status)
