"""One model pass per medium, off the ingest path — the SPAWNED half of media
custody (issue #116).

**Custody stays inline; comprehension does not, and the asymmetry is the whole
argument.** `[image a3f2c1]` is a promise that `a3f2c1` can be produced, so the
copy runs before the audit write — spawning it would write the promise first
and keep it second, which is the exact A9 violation `nora/ingest/stages/
media.py` exists to end. **Comprehension has no such property.** An
un-comprehended image is exactly the state every image was in before this
module existed: the row says Nora holds the bytes and claims nothing about
them, so a comprehension that lands late, or never, has not made anything
untrue. `memory_feed` is already spawned on precisely this reasoning and Honcho
is already never load-bearing.

**And inline would be untenable in the one case that matters.** Signal ingest
is serialized: the listener awaits each message's stages in turn, so an inline
video read — chunked, several model calls, tens of seconds (issue #117) — would
stall every message behind it in the room. What waits today is a loopback HTTP
fetch and a disk write.

**What lands, lands ONCE.** The job keys on the sha256 and refuses a row that
already carries a comprehension: a description is a pure function of the bytes,
so the same meme forwarded five times is one read. Two sightings racing in one
process are collapsed by the in-flight map, for `StickerReader`'s reason — the
Discord listener is a separate task from the Signal one, and without it two
people sending the same new photo seconds apart costs two model calls and two
writers racing on one row.

**Three states on the way out, never two** (R-27). `ok` is a comprehension
written; `nothing_found` is a medium nothing here can read (a PDF, a lottie
sticker) — a real, final answer about those bytes; `unavailable` is every
wiring gap and every failure, including "no describer configured for this
modality" and "the blob is gone". A wiring gap counted as `nothing_found` would
say «there is nothing in this image», which is a claim, and this module is not
entitled to make it.

**The spawned work does not inherit the ingest span.** `create_task` copies the
context of the task that CALLS it, so a job scheduled here would otherwise
attach to the ingest root and hold that trace open for as long as a video read
takes. It opens its own root instead, declared plumbing (R-76): every call
under it is a model call, but they are describers rather than Nora's own
reasoning, and the volume is one root per attachment.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.domain.media import (
    CONTENT_LABELS,
    MediaBlob,
    MediaObject,
    sanitize_description,
)
from nora.domain.models import PersonRef
from nora.domain.ports import (
    BlobStore,
    MediaDescription,
    MediaInterpreter,
    MediaObjectStore,
    MemoryStore,
)
from nora.obs.tracing import TRACE_KIND, attached, span

log = logging.getLogger("nora.ingest.comprehension")

# What a comprehension job can be asked to read. Everything else — a PDF, an
# archive, a Discord lottie sticker declared `application/json` — is
# `nothing_found`: a final answer about those bytes rather than a failure, and
# never a model call that would produce prose about JSON.
COMPREHENSIBLE_TOPS = ("image", "video", "audio")


@dataclass(frozen=True)
class Subject:
    """One attachment to comprehend, as the custody stage saw it.

    `label` is `nora.domain.media.label_for`'s answer — a member of a closed
    set, derived from the platform's own flags and the blob's MIME type, never
    anything a room typed. It selects the authored prompt and it decides which
    lane runs: a voice note's content is ALREADY IN HAND (`transcript`), so it
    takes the text pass rather than a second, larger listen.

    `transcript` is empty for everything that is not a voice note, and may be
    empty for a voice note whose STT failed — in which case there is nothing to
    summarize and the job answers `unavailable`, never a guessed transcript.
    """

    blob: MediaBlob
    label: str
    transcript: str = ""


class MediaComprehension:
    """The spawned comprehension job. One instance per process, built by the
    composition root — never a module-level singleton (CLAUDE.md's test-seam
    policy), which would make two tests share an in-flight map.

    Every collaborator arrives through `__init__`; `schedule` takes only the
    facts of one message. `spawn` is the SAME injected scheduler `memory_feed`
    uses, so a deployment that does not want background work has one place to
    change and this job inherits its done-callback logging.
    """

    def __init__(
        self,
        *,
        blobs: BlobStore,
        store: MediaObjectStore,
        interpreter: MediaInterpreter,
        memory: MemoryStore,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], None],
        stickers_enabled: bool = True,
    ) -> None:
        self._blobs = blobs
        self._store = store
        self._interpreter = interpreter
        self._memory = memory
        self._metrics = metrics
        self._spawn = spawn
        # `[media] sticker_descriptions`. Off excludes stickers from the read
        # and nothing else — the identity ladder in `StickerReader` is custody
        # and stays on, because it is what makes a repeat sighting free.
        self._stickers_enabled = stickers_enabled
        self._inflight: set[str] = set()

    def schedule(
        self,
        *,
        subjects: tuple[Subject, ...],
        platform: str,
        conversation_id: str,
        person: PersonRef | None,
        timestamp: int,
    ) -> None:
        """Queue a read for every attachment worth reading. Returns at once.

        Called from the custody stage AFTER the bytes are held, so every
        subject here is one whose handle is already a true claim. Nothing is
        awaited: the caller is the listener's own path.
        """
        for subject in subjects:
            if subject.label == "sticker" and not self._stickers_enabled:
                continue
            if subject.blob.sha256 in self._inflight:
                continue
            self._inflight.add(subject.blob.sha256)
            self._spawn(self._run(
                subject=subject, platform=platform,
                conversation_id=conversation_id, person=person, timestamp=timestamp,
            ))

    async def _run(
        self, *, subject: Subject, platform: str, conversation_id: str,
        person: PersonRef | None, timestamp: int,
    ) -> None:
        """Total: never raises, always releases the in-flight slot.

        A raise here would reach only `spawn`'s done-callback, which is exactly
        the shape a failure must not take — the counter below is what an
        operator reads instead.
        """
        try:
            # `attached(None)` DETACHES first, and that is the whole of it:
            # `asyncio.create_task` copies the caller's contextvars (measured
            # 2026-08-28, `nora/obs/tracing.py`), so this span was a CHILD of
            # `ingest.media` — which made the `plumbing` declaration inert
            # under `ParentBased` and held the ingest trace open for as long as
            # a chunked video read takes. The same one-liner
            # `nora/engagement/pipeline.py` already uses for spawned work.
            with attached(None), span(
                "comprehend",
                **{TRACE_KIND: "plumbing",
                   "nora.platform": platform,
                   "nora.conversation_id": conversation_id,
                   "nora.media_label": subject.label,
                   "nora.media_handle": subject.blob.handle},
            ):
                await self._comprehend(
                    subject=subject, platform=platform,
                    conversation_id=conversation_id, person=person,
                    timestamp=timestamp,
                )
        except Exception:
            log.exception("media comprehension failed for %s", subject.blob.handle)
            self._count(platform, subject.label, "unavailable", "error")
        finally:
            self._inflight.discard(subject.blob.sha256)

    async def _comprehend(
        self, *, subject: Subject, platform: str, conversation_id: str,
        person: PersonRef | None, timestamp: int,
    ) -> None:
        blob, label = subject.blob, subject.label
        top = blob.media_type.partition("/")[0].lower()
        if top not in COMPREHENSIBLE_TOPS:
            # A REAL answer about these bytes, not a failure: nothing here will
            # ever read a PDF, and saying so is what stops the job retrying it
            # on every future sighting.
            self._count(platform, label, "nothing_found", "unreadable")
            return

        existing = await self._store.get(sha256=blob.sha256)
        if existing is not None and self._already_read(existing, label):
            # Already read — the same meme forwarded five times is one read.
            # NOT counted as `ok`: nothing was comprehended on this pass, and
            # counting it would make the ok rate say the describer is working
            # when it might not have run for days.
            self._count(platform, label, "nothing_found", "already_read")
            return

        started = time.perf_counter()
        if label in CONTENT_LABELS:
            result = await self._read_transcript(subject)
            content = subject.transcript
        else:
            data = await self._bytes(blob)
            if data is None:
                # Custody said the bytes were held and the store cannot produce
                # them — a rotated key, a pruned volume. `unavailable`, never
                # `nothing_found`: this says nothing about what the image was.
                self._count(platform, label, "unavailable", "blob")
                return
            result = await self._interpreter.describe(
                data=data, media_type=blob.media_type, label=label,
            )
            content = result.content
        self._metrics.observe(
            "nora_media_comprehension_seconds", time.perf_counter() - started,
        )

        if not result.available:
            self._count(platform, label, "unavailable", result.reason or "vision")
            if label in CONTENT_LABELS and content:
                # A voice note whose SUMMARY failed still has its words, and
                # those words are the thing the room actually needs. Store the
                # content with no title rather than throwing away a transcript
                # over a missing headline — the transcript is evidence, the
                # title is a convenience.
                await self._store.comprehend(
                    sha256=blob.sha256, title="", description="",
                    content=content, model="",
                )
                await self._feed(
                    platform=platform, conversation_id=conversation_id,
                    person=person, timestamp=timestamp, blob=blob, label=label,
                    title="", description="", content=content,
                )
            return

        # The model THIS MODALITY used, not the image one. `describe_model` is
        # the provenance of the text on the row, and recording `glm-5.3-flash`
        # against a clip that `k3-256k` described is a lie in the one column an
        # operator reads to find out which describer produced what.
        # `model_for` exists on the adapter for exactly this and had no caller.
        model = self._model_for(blob.media_type)
        await self._store.comprehend(
            sha256=blob.sha256, title=result.title, description=result.text,
            content=content, model=model,
            # RETURNED with the result rather than read off the adapter — this
            # job spawns one task per subject, so a video and a photo on one
            # message run two `describe` calls concurrently on one shared
            # interpreter, and stashed state raced.
            provenance=dict(result.provenance or {}),
        )
        await self._feed(
            platform=platform, conversation_id=conversation_id, person=person,
            timestamp=timestamp, blob=blob, label=label,
            title=result.title, description=result.text, content=content,
        )
        self._count(platform, label, "ok", "")

    async def _read_transcript(self, subject: Subject) -> MediaDescription:
        """The audio lane. A voice note's `content` is the SenseVoice transcript
        `nora/infra/speech.py` produced inline at ingest — no vision call, and
        no second listen: the title and the description come from a cheap text
        pass over words Nora already has."""
        if not subject.transcript:
            # STT failed or the note was silent. There is nothing to summarize
            # and nothing to claim (R-27) — never a guessed transcript.
            return MediaDescription(available=False, reason="empty")
        return await self._interpreter.summarize(text=subject.transcript)

    async def _bytes(self, blob: MediaBlob) -> bytes | None:
        """The held bytes, read back through custody's own promise.

        Deliberately a re-read rather than a payload carried across the spawn.
        Holding N attachment bodies alive until a background task drains them
        is how a burst of ten photos becomes a memory spike on the listener's
        own loop — and the handle in the body is a promise these bytes are
        retrievable, so exercising that promise is the honest thing for the
        job to do. A blob that cannot be produced is the one case worth
        knowing about, and this is where it surfaces.
        """
        try:
            return await self._blobs.get(key=blob.sha256) or None
        except Exception:
            log.exception("comprehension could not open blob %s", blob.handle)
            return None

    async def _feed(
        self, *, platform: str, conversation_id: str, person: PersonRef | None,
        timestamp: int, blob: MediaBlob, label: str,
        title: str, description: str, content: str,
    ) -> None:
        """ALL THREE FIELDS to Honcho, under the sender's own peer, SCREENED.

        This is the half of issue #116 the story is about. The marker already
        rode into memory in `messages.body`; what it pointed at was empty, so
        every reply around a photo was preserved and unreadable. The
        comprehension follows the marker in, keyed by the SAME handle the
        transcript renders, so a later recall can join the two.

        **The identifier screen runs HERE, and it has to.** Design §5's claim
        that moving `scan_identifiers` to render time is the stricter position
        is true of the TRANSCRIPT and false of Honcho: before this change the
        same text rode in `message.body`, which the custody stage screened
        before `memory_feed` forwarded it, and the fork has two consumers. Only
        one of them renders. A voice note carrying a phone number would
        otherwise be written verbatim and DURABLY into the sender's peer, where
        it can resurface in a later peer card — a worse surface than the
        transcript, because nothing re-screens a card at render.

        A hit costs the DETAIL and never the marker, which is the shape the
        custody stage used on master and the shape the render join uses now:
        memory still records that a photo arrived, and claims nothing about it.

        Mapped senders only (D6), like `memory_feed`: an unmapped sender is
        never fed to the semantic store, and a photo does not change that.
        Never load-bearing and never raised into: `MemoryStore` swallows its
        own failures and this is already off the listener path.
        """
        if person is None:
            return
        marker = f"[{label} {blob.handle}]"
        detail = "\n".join(
            sanitize_description(part) for part in (title, description, content)
            if part
        )
        kind = scan_identifiers(detail) if detail else None
        if kind is not None:
            log.warning(
                "a media comprehension would have carried a %s identifier into "
                "memory — feeding the marker alone", kind,
            )
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            detail = ""
        text = f"{marker}\n{detail}" if detail else marker
        try:
            await self._memory.ensure_peer(peer_id=person.honcho_peer_id)
            ok = await self._memory.feed(
                peer_id=person.honcho_peer_id, text=text,
                conversation_id=conversation_id,
                metadata={"ts": timestamp, "media": blob.handle},
            )
        except Exception:
            log.exception("comprehension feed failed; memory is never load-bearing")
            ok = False
        self._metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")

    @staticmethod
    def _already_read(existing: MediaObject, label: str) -> bool:
        """Whether this row still needs the pass THIS label would run.

        Not simply `existing.comprehended`. A voice note's `content` is written
        INLINE by the custody stage — the words are in hand synchronously and
        must not wait behind a model call nobody needs — so the row is already
        non-empty when the job reaches it, and the plain predicate would skip
        the cheap text pass that gives it a title, forever. For a content label
        the question is whether the TEXT PASS has run; for everything else it
        is whether anything has.
        """
        if label in CONTENT_LABELS:
            return bool(existing.title or existing.description)
        return existing.comprehended

    def _model_for(self, media_type: str) -> str:
        """The describer this modality actually used, if the interpreter can
        say. A fake, or a future adapter with one model, falls back to `model`
        — the same value the row carried before."""
        resolver = getattr(self._interpreter, "model_for", None)
        if callable(resolver):
            return str(resolver(media_type) or "")
        return str(getattr(self._interpreter, "model", "") or "")

    def _count(self, platform: str, label: str, status: str, reason: str) -> None:
        self._metrics.inc(
            "nora_media_comprehension_total",
            platform=platform, label=label, status=status, reason=reason,
        )
