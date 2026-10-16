"""Stage 2.5 — take custody of the bytes, BEFORE the record claims them.

A9 says SQLite is THE RECORD. A media placeholder's referent lives in another
process's tree, under that process's retention: prune signal-cli, or re-link
the device, and a row would still say an image exists while nothing in the
system could produce it. This stage makes the claim true — it copies the bytes
into a content-addressed store Nora owns and puts the hash in the same INSERT
as the body that names it.

**It runs INLINE, ahead of the audit write, unlike `memory_feed`'s spawned
pattern**, because the two differ in the one way that decides it:

* `memory_feed` sends a message to Honcho, which is explicitly never
  load-bearing. If it lands late, or never, the RECORD is unaffected.
* Custody is part OF the record. `[image a3f2c1]` is a promise that `a3f2c1`
  can be produced. Spawning the copy would write that promise first and keep it
  second — a window in which the audit row asserts custody that does not exist,
  an A9 violation in a smaller size. And a spawned failure could not degrade the
  body, because the body would already be written.

The cost of inline is a stalled ingest sequence while bytes move. It is bounded:
the fetch is one loopback HTTP call with a short timeout, and
`FilesystemBlobStore` does every filesystem call in a worker thread, so the
event loop keeps serving delivery, health and the metrics endpoint throughout.
What waits is the NEXT message on the same listener — the same ordering the
audit already imposes.

**Degrading honestly is most of this module.** Any attachment whose bytes do
not arrive leaves the body exactly as the parser wrote it — `[image]`, no
handle — and increments `nora_media_custody_total{status=...}` with the reason.
The message is still recorded, in full, with its envelope. Nothing is silent
and nothing is claimed.

**THE BODY CARRIES A LABEL AND A HANDLE. NOTHING ELSE.** A sticker's
description and a voice note's transcript are not baked in beside the handle:
that single string is read VERBATIM by two consumers with opposite needs —
`memory_feed` wants everything, the prefill wants a line. The comprehension
lives in `media_objects` and each consumer selects the fields it wants at
render time.

**Bytes reach a prompt for EVERY medium Nora holds, and they reach it off this
path.** The read is `nora/ingest/comprehension.py`'s, SPAWNED through the same
injected `spawn` `memory_feed` uses, and this stage only says which blobs are
worth reading. The egress screen runs at RENDER time: `scan_identifiers` runs
on every render, in the context assembler — stricter than running it here once,
for the reason `nora/ingest/stickers.py` gives (a screen applied before caching
is one a cache hit skips).

**A voice note is transcribed INLINE.** `transcriber` (when wired) hands its
bytes to local STT here, before the audit write, because a voice note's
transcript is conversation CONTENT rather than a description of an attachment
— see `[speech.stt]` and `nora/infra/speech.py`. It lands in
`media_objects.content`, joined into the transcript at render, not baked into
the body. The comprehension job adds a title and a description over that
transcript with a cheap text pass; it never replaces the words.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from nora.domain.media import (
    COVER_ROLE,
    MediaBlob,
    MediaObject,
    MediaRef,
    body_with_handles,
    ground_media_type,
    label_for,
    resolve_media_type,
)
from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.ports import BlobStore, MediaObjectStore, MediaSource, Transcriber
from nora.ingest.comprehension import MediaComprehension, Subject
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
    comprehension: MediaComprehension | None = None,
    person: PersonRef | None = None,
    language: str = "",
) -> NormalizedMessage:
    """Return `message` with every attachment Nora could copy in her custody.

    Total: never raises, never drops a message. A message with no attachments
    is returned unchanged without touching either collaborator, so the ordinary
    case costs one `if`.

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
    audit write). A voice note is conversation CONTENT, not an attachment
    description; a song is an audio file and stays on the bare-handle path. A
    failed transcription is recorded with the bare handle (a note is still a
    note), never a dropped message and never a guessed transcript (R-27). The
    transcript lands in `media_objects.content` through the comprehension job
    rather than in the body — the words still reach the prompt, by a join
    instead of by a bake.

    `comprehension` is the SPAWNED read. `None` is the inert-by-config state
    (`[media] comprehension = false`, or no vision wired): no MODEL reads any
    medium, and every attachment keeps its bare handle. A voice note's
    transcript is still written to `media_objects.content` below, because STT
    already ran on this awaited path and its words are conversation content
    that needs no model. What the off state does cost is memory: with no job,
    nothing feeds those words to Honcho. `person` rides along only so a
    comprehension can be fed to Honcho under the sender's own peer (D6, mapped
    senders only) — this stage never resolves identity and never reads it for
    anything else.
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
    subjects: list[Subject] = []
    for ref in message.media:
        if ref.sticker and stickers is not None:
            # STICKERS take the IDENTITY LADDER rather than the pull-based
            # path below, as a CUSTODY optimisation: a sticker's platform-side
            # identity is known at parse time, so a repeat sighting costs zero
            # platform RPC. The model pass is the spawned comprehension job's
            # — one mechanism for every medium.
            outcome = await stickers.read(
                platform=message.platform, ref=ref, source=source
            )
            blob, raw = outcome.blob, ""
            _count(metrics, platform=message.platform,
                   status="stored" if blob else (outcome.reason or "unavailable"))
            # `nora_sticker_descriptions_total` — the dedup ladder's hit rate
            # as an OBSERVATION, not a belief. `status` is about CUSTODY
            # (`ok` = the bytes are held), not a description; the
            # description's own outcome is `nora_media_comprehension_total`.
            # `level` is `StickerOutcome.level`'s own vocabulary
            # (identity/content/none), NOT `nora_media_fetch_total`'s
            # true/false `cached` — a separate label name on purpose.
            metrics.inc(
                "nora_sticker_descriptions_total",
                platform=message.platform,
                status="ok" if blob else (outcome.reason or "unavailable"),
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
            # `voiceNote` flag, NOT the MIME top-level type (voice note is a
            # SEPARATE axis from audio/*; see `nora/domain/media.py`). The
            # transcript rides to `media_objects.content` on the comprehension
            # subject below, NOT into the body; the sanitize and the R-18
            # `scan_identifiers` screen run at RENDER time, in the context
            # assembler, on every render — a transcript is a member's words
            # verbatim, with higher stakes than model-written prose, and a
            # screen applied once at store time is one a cache hit skips.
            if taken is not None and ref.voice_note and transcriber is not None:
                # `plaintext` is the SAME bytes `_take` just held to compute
                # `blob` — reusing them here (rather than a second
                # `blobs.get` round trip through the just-written key) is both
                # cheaper and safer: it cannot raise on a disk/decrypt error
                # the way re-reading the store could, so a voice note that
                # was JUST written always reaches the transcriber.
                blob, plaintext = taken
                # NO SPAN HERE, by decision (`docs/observability.md`, "what
                # counts as a generation"). The STT service is a local ASR on
                # Nora's own private network, not an LLM, and Langfuse is an
                # LLM-observability tool (R-76). Its outcome ships on
                # `nora_speech_transcriptions_total` with R-27's three states.
                # The WORDS still reach a trace: the summary pass over this
                # transcript is a real model call and runs under `comprehend`.
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
        # A shared item's still picture is RECORDED as one, not only rendered
        # as one (`[cover image …]`): `media_json` is all `media_fetch` has
        # when it serves the handle, and the ref's `on_demand` does not survive
        # into it. THE LABEL'S OWN PREDICATE, called rather than restated:
        # `on_demand` alone also covers a preview that is a video or a
        # sticker, which renders under its plain label, and a role the label
        # never claimed would make `media_fetch` call it a cover anyway.
        if blob and label_for(ref) == "cover image":
            blob = replace(blob, role=COVER_ROLE)
        # A gap is a real position, not a skipped one: handles are matched to
        # placeholders by INDEX, so a failed attachment must contribute an
        # empty handle rather than shifting every later one onto the wrong
        # placeholder.
        handles.append(blob.handle if blob else "")
        if blob:
            held.append(blob)
            # A comprehension subject rides only WITH a handle, for
            # `render_placeholder`'s reason: reading bytes Nora does not hold
            # is not something that can happen, and scheduling a read for one
            # would be a job that can only ever answer `unavailable`.
            #
            # And NOT for an `on_demand` ref: the thumbnail of a reel, post or
            # story somebody shared is held and handled like any photo, and
            # read only when `media_fetch` asks. Describing every shared
            # thumbnail at ingest would run the vision model with nobody
            # tagging Nora. Skipping the SUBJECT, not the custody, keeps both
            # halves true: the handle still names bytes Nora holds, and the row
            # stays unread, so `media_fetch` does a fresh look and the same
            # bytes sent later as a direct photo are still read then.
            if not ref.on_demand:
                subjects.append(Subject(
                    blob=blob, label=label_for(ref), transcript=raw,
                ))
            # Inbound write path (DEC-138): every byte Nora takes custody of
            # gains a `source='inbound'` row in the unified media index — the
            # index is "one row per byte Nora holds", not only
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

            # A VOICE NOTE'S WORDS ARE WRITTEN HERE, INLINE, AND NEVER WAIT ON
            # A MODEL. They were captured synchronously above: STT has already
            # run, so holding them in a `Subject` until a spawned task wrote a
            # TITLE for them would be wrong twice:
            #
            #   * with `[media] comprehension = false` the job does not exist,
            #     so the transcript would reach nothing at all.
            #   * with it on, somebody asking «佢講咩» two seconds later would
            #     see a bare handle, because the words were queued behind a
            #     convenience. That latency is acceptable for a PHOTO, whose
            #     content genuinely has to be read; a voice note's content is
            #     already in hand.
            #
            # The comprehension job adds the title and description over this
            # same row afterwards and passes the identical `content` through,
            # so the two writers cannot disagree about the words.
            if raw and ref.voice_note and media_store is not None:
                try:
                    await media_store.comprehend(
                        sha256=blob.sha256, title="", description="",
                        content=raw, model="",
                    )
                except Exception:
                    log.exception("media ingest: voice transcript write failed")

    labels = tuple(label_for(ref) for ref in message.media)

    # THE READ, SCHEDULED — never awaited. Custody is finished at this line:
    # every handle in `handles` names bytes Nora holds, and the body about to
    # be written is true whether or not a model ever looks at them. So the read
    # goes through the same injected `spawn` `memory_feed` uses, and the
    # listener moves on to the next message rather than waiting on a video.
    #
    # `scan_identifiers` does not run here: there is no free text in this body
    # to screen (the LABEL and HANDLE halves cannot carry an identifier by
    # construction: a closed label set plus hex). The description half reaches
    # a prompt through `nora.domain.media_join`, which screens it on EVERY
    # render on all eight paths, including a render served entirely from stored
    # rows. That is `stickers.py`'s rule ("a screen applied before caching is
    # one a cache hit skips").
    if comprehension is not None and subjects:
        comprehension.schedule(
            subjects=tuple(subjects), platform=message.platform,
            conversation_id=message.conversation_id, person=person,
            timestamp=message.timestamp,
        )

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
    # A WILDCARD subtype is ground against the bytes, here and nowhere else,
    # because here is where the bytes first exist. Instagram's wire carries no
    # mimetype, so its parser honestly records `image/*`; that classifies fine
    # everywhere (the label and the handle are unaffected) and then breaks in
    # the comprehension pass, which builds a `data:image/*;base64,…` URI no
    # decoder can act on. Inert for every platform that declares a real
    # subtype — see `ground_media_type` for both fail-closed rules.
    media_type = ground_media_type(declared=media_type, data=fetched.data)
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
