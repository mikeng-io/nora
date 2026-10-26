"""One model pass per medium, off the ingest path — the SPAWNED half of media
custody.

**Custody stays inline; comprehension does not, and the asymmetry is the whole
argument.** `[image a3f2c1]` is a promise that `a3f2c1` can be produced, so the
copy runs before the audit write — spawning it would write the promise first
and keep it second, which is the A9 violation `nora/ingest/stages/media.py`
exists to end. **Comprehension has no such property.** An un-comprehended image
is a row that says Nora holds the bytes and claims nothing about them, so a
comprehension that lands late, or never, has not made anything untrue.
`memory_feed` is already spawned on precisely this reasoning and Honcho is
already never load-bearing.

**And inline would be untenable in the one case that matters.** Signal ingest
is serialized: the listener awaits each message's stages in turn, so an inline
video read — chunked, several model calls, tens of seconds — would stall every
message behind it in the room. What waits is a loopback HTTP fetch and a disk
write.

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
takes. It opens its own root instead, declared `generation` (R-76): every call
under it is a model call — a describer for a photo or a clip, a summarizer for
a voice note's transcript — which is the condition R-76 keeps a trace FOR, and
the volume is one root per attachment. DETACHING AND DROPPING ARE TWO
DECISIONS: the detach keeps the ingest trace short; the trace itself is kept.

**`perceive` is the same read, awaited.** The social stream's perception pass
(`nora.app.social_perception`) needs each item's answer before it raises the
read's stimulus, so it awaits one subject through the same slot, the same
dedupe and the same three states, and is answered the state. It feeds no
memory: a social item belongs to no conversation, and the memory index
projects it on its own.
"""

from __future__ import annotations

import asyncio
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
from nora.obs.tracing import TRACE_KIND, annotate, attached, span

log = logging.getLogger("nora.ingest.comprehension")

# What a comprehension job can be asked to read. Everything else — a PDF, an
# archive, a Discord lottie sticker declared `application/json` — is
# `nothing_found`: a final answer about those bytes rather than a failure, and
# never a model call that would produce prose about JSON.
COMPREHENSIBLE_TOPS = ("image", "video", "audio")

#: How many media reads may be in flight at once. Not a throughput knob — a
#: memory and quota one: each in-flight job holds a decrypted blob plus the
#: base64 copy the request carries, against ceilings of 8 MiB for a still and
#: 100 MiB for a clip, and a video job additionally runs up to `chunk_max`
#: sequential model calls while it holds them.
#:
#: Four, because the ordinary case is one message with a handful of
#: attachments and the pathological one is a burst nobody is waiting on. A
#: queued job costs latency on a path with no reader; an unqueued one costs
#: memory on the process serving the room.
DEFAULT_MAX_CONCURRENT = 4


@dataclass(frozen=True)
class Perceived:
    """One read's R-27 state and the reason beside it, the same pair
    `nora_media_comprehension_total` counts (`reason` is empty for `ok`)."""

    status: str
    reason: str


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
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        content_checks: bool = True,
    ) -> None:
        self._content_checks = content_checks
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
        # See `_run`: the bound is on the COUNT of in-flight reads, because
        # each one holds a decrypted blob and its base64 copy.
        self._slots = asyncio.Semaphore(max(1, max_concurrent))

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

    async def perceive(
        self, *, subject: Subject, platform: str, context: str = "",
    ) -> Perceived:
        """Read one subject now and answer its R-27 state (`ok`,
        `nothing_found`, `unavailable`) with its reason. `context` is what the describer is
        told it is reading, a sentence of system facts. Never raises. Bytes
        already in flight from a room answer `unavailable`: that read lands on
        the same row, and the caller tries again on a later day."""
        if subject.blob.sha256 in self._inflight:
            return Perceived("unavailable", "in_flight")
        self._inflight.add(subject.blob.sha256)
        try:
            async with self._slots:
                return await self._read(
                    subject=subject, platform=platform, conversation_id="",
                    person=None, timestamp=0, context=context,
                )
        except Exception:
            log.exception("media comprehension failed for %s", subject.blob.handle)
            return self._count(platform, subject.label, "unavailable", "error")
        finally:
            self._inflight.discard(subject.blob.sha256)

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
            # THE CONCURRENCY SLOT, held around the WHOLE job. `_inflight`
            # dedupes identical bytes, not volume, and `spawn` is a bare
            # `create_task` — without a bound, fifty photos in a minute would
            # be fifty concurrent describer calls, each holding a decrypted
            # blob plus its base64 copy against an 8 MiB still ceiling and a
            # 100 MiB video one.
            #
            # Around the whole job rather than around the model call alone: the
            # blob read and the ffmpeg chunking ARE the memory, and a semaphore
            # that let fifty jobs decrypt fifty videos before queueing at the
            # describer would bound the wrong thing.
            async with self._slots:
                await self._read(
                    subject=subject, platform=platform,
                    conversation_id=conversation_id, person=person,
                    timestamp=timestamp,
                )
        except Exception:
            log.exception("media comprehension failed for %s", subject.blob.handle)
            self._count(platform, subject.label, "unavailable", "error")
        finally:
            self._inflight.discard(subject.blob.sha256)

    async def _read(
        self, *, subject: Subject, platform: str, conversation_id: str,
        person: PersonRef | None, timestamp: int, context: str = "",
    ) -> Perceived:
        """One job, inside the concurrency slot and under its own trace root.
        Answers its R-27 state."""
        try:
            # `attached(None)` DETACHES first, and that is the whole of it:
            # `asyncio.create_task` copies the caller's contextvars, so without it
            # this span would be a CHILD of `ingest.media` and hold the ingest trace
            # open for as long as a chunked video read takes. The same one-liner
            # `nora/engagement/pipeline.py` uses for spawned work.
            #
            # DETACHING AND DROPPING ARE TWO DECISIONS. R-76 keeps the traces whose
            # work CAN contain a generation, and every branch below this line is a
            # model call — `MediaInterpreter.describe` for a photo or a clip,
            # `.summarize` for a voice note's transcript — so the span is declared
            # `generation`, not `plumbing`. `attached(None)` keeps it its OWN trace: it
            # does not hold `ingest.media` open.
            with attached(None), span(
                "comprehend",
                **{
                    TRACE_KIND: "generation",
                    "nora.platform": platform,
                    "nora.conversation_id": conversation_id,
                    "nora.media_label": subject.label,
                    "nora.media_handle": subject.blob.handle,
                },
            ):
                return await self._comprehend(
                    subject=subject, platform=platform,
                    conversation_id=conversation_id, person=person,
                    timestamp=timestamp, context=context,
                )
        except Exception:
            log.exception("media comprehension failed for %s", subject.blob.handle)
            return self._count(platform, subject.label, "unavailable", "error")

    async def _comprehend(
        self, *, subject: Subject, platform: str, conversation_id: str,
        person: PersonRef | None, timestamp: int, context: str = "",
    ) -> Perceived:
        blob, label = subject.blob, subject.label
        top = blob.media_type.partition("/")[0].lower()
        if top not in COMPREHENSIBLE_TOPS:
            # A REAL answer about these bytes, not a failure: nothing here will
            # ever read a PDF, and saying so is what stops the job retrying it
            # on every future sighting.
            return self._count(platform, label, "nothing_found", "unreadable")

        # `lookup`, not `get`: a transient index outage answers `None` from
        # `get` and is indistinguishable from «not read yet», so the job would
        # re-run the describer — on a video, a fresh ten-window read. The
        # availability axis is what makes «I could not check» a third answer.
        found = await self._store.lookup(sha256s=(blob.sha256,))
        if not found.available:
            return self._count(platform, label, "unavailable", "store")
        existing = found.objects.get(blob.sha256)
        if existing is not None and self._already_read(existing, label):
            # Already read — the same meme forwarded five times is one read.
            # NOT counted as `ok`: nothing was comprehended on this pass, and
            # counting it would make the ok rate say the describer is working
            # when it might not have run for days.
            return self._count(platform, label, "nothing_found", "already_read")

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
                return self._count(platform, label, "unavailable", "blob")
            # The context rides only when there is one, so a describer that
            # takes none (a room's media never has one) is called as before.
            extra = {"context": context} if context else {}
            result = await self._interpreter.describe(
                data=data, media_type=blob.media_type, label=label, **extra,
            )
            content = result.content
        self._metrics.observe(
            "nora_media_comprehension_seconds", time.perf_counter() - started,
        )

        if not result.available:
            status = self._count(platform, label, "unavailable", result.reason or "vision")
            if label in CONTENT_LABELS and content:
                if existing is not None and existing.content == content:
                    # ALREADY THERE, and this is the default config's own path: with
                    # `comprehension_text_model = ""` the text pass answers
                    # `no_text_describer` every time, and `_already_read` keys on
                    # title/description for a content label, so re-running would re-feed the
                    # SAME words to memory on every sighting. The words are already stored
                    # and already remembered; nothing here has anything to add.
                    return status
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
            return status

        # The model that WROTE THIS TEXT, off the result. `describe_model` is
        # the provenance of the text on the row, and recording the image model
        # against a clip the video describer read is a lie in the one column an
        # operator reads to find out which describer produced what.
        #
        # It comes back WITH the result rather than being derived from the
        # media type, because the answer is not a function of the modality: a
        # voice note's title and description are written by
        # `comprehension_text_model`, not by the audio describer, so deriving
        # it recorded `""` for the one modality whose text has a single named
        # author.
        model = result.model
        provenance = dict(result.provenance or {})
        # THE MODEL THAT ANSWERED, onto the span that was opened without one.
        # Same value that goes in `media_objects.describe_model`, so the trace
        # and the row cannot disagree about which describer produced the text.
        # The description itself does NOT go on the wire: it is model prose
        # ABOUT somebody's photo, and the row plus `nora.media_handle` already
        # re-derives it — the same reasoning `nora/ingest/relevance_gate.py`
        # gives for keeping a room's own words off its span.
        # Plain `annotate()`: this span is the R-76 root a model call can happen
        # under, not a GenAI-dialect generation span.
        # There is no child span for the describe call itself: neither
        # this module (`nora.ingest`) nor `nora.infra.vision.client` may
        # import `nora.agent` for one (the layered-import contract has
        # `nora.agent` above both), and threading a span handle through
        # `MediaInterpreter.describe`'s return would widen a port for a
        # value only the collector reads — the same argument this file makes for
        # keeping usage off `MediaDescription`.
        annotate(**{"nora.media_describe_model": model})
        stored = await self._store.comprehend(
            sha256=blob.sha256, title=result.title, description=result.text,
            content=content, model=model,
            # RETURNED with the result rather than read off the adapter — this
            # job spawns one task per subject, so a video and a photo on one
            # message run two `describe` calls concurrently on one shared
            # interpreter, and stashed state would race.
            provenance=provenance,
        )
        if not stored:
            # THE READ HAPPENED AND NOTHING KEPT IT. Counting `ok` here would be
            # «a tick that wrote nothing reporting ok»: under load the metric says the
            # pipeline is healthy while
            # the index is empty. And the feed is skipped with it — Honcho
            # would otherwise hold a description of bytes `media_objects` has
            # no row for, and a redelivery would feed the same words twice with
            # no row to dedup against.
            return self._count(platform, label, "unavailable", "store")
        await self._feed(
            platform=platform, conversation_id=conversation_id, person=person,
            timestamp=timestamp, blob=blob, label=label,
            title=result.title, description=result.text, content=content,
            # The caveat rides to memory too, and last. What a read did not see
            # is not a rendering detail — a recalled description of half a clip
            # is as wrong in a peer card as it is in a transcript.
            caveat=str(provenance.get("media_caveat", "")),
        )
        return self._count(platform, label, "ok", "")

    async def _read_transcript(self, subject: Subject) -> MediaDescription:
        """The audio lane. A voice note's `content` is the STT transcript
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
        title: str, description: str, content: str, caveat: str = "",
    ) -> None:
        """ALL THREE FIELDS to Honcho, under the sender's own peer, SCREENED.

        The marker already rides into memory in `messages.body`; the
        comprehension follows it in, keyed by the SAME handle the transcript
        renders, so a later recall can join the two.

        **The identifier screen runs HERE, and it has to.** Screening at render
        time covers the TRANSCRIPT but not Honcho, which has two consumers and
        only one of them renders. A voice note carrying a phone number would
        otherwise be written verbatim and DURABLY into the sender's peer, where
        it can resurface in a later peer card — a worse surface than the
        transcript, because nothing re-screens a card at render.

        A hit costs the DETAIL and never the marker, the shape the render join
        also uses: memory still records that a photo arrived, and claims
        nothing about it.

        Mapped senders only, like `memory_feed`: an unmapped sender is
        never fed to the semantic store, and a photo does not change that.
        Never load-bearing and never raised into: `MemoryStore` swallows its
        own failures and this is already off the listener path.
        """
        if person is None:
            return
        marker = f"[{label} {blob.handle}]"
        detail = "\n".join(
            sanitize_description(part)
            for part in (title, description, content, caveat)
            if part
        )
        kind = scan_identifiers(detail, content_checks=self._content_checks) if detail else None
        if kind is not None:
            log.warning(
                "a media comprehension would have carried a %s identifier into "
                "memory — feeding the marker alone", kind,
            )
            self._metrics.inc("nora_egress_blocks_total", kind=kind)
            detail = ""
        text = f"{marker}\n{detail}" if detail else marker
        try:
            await self._memory.ensure_peer(
                peer_id=person.honcho_peer_id, conversation_id=conversation_id,
            )
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

    def _count(self, platform: str, label: str, status: str, reason: str) -> Perceived:
        """Count one outcome, onto the metric and the span, and answer it."""
        self._metrics.inc(
            "nora_media_comprehension_total",
            platform=platform, label=label, status=status, reason=reason,
        )
        # THE SAME R-27 ANSWER ONTO THE SPAN. The counter says how many reads
        # ended which way across the deployment; the span says how THIS one
        # did, which is the question an operator holding a handle actually has.
        # A `comprehend` trace that carried only a name would make every
        # outcome — read, already-read, unreadable, blob gone — look identical
        # ("a span with only a name is worse than no span"). Safe on every
        # path: `annotate` is a no-op when no span is current, which is what
        # `_run`'s own error branch is.
        annotate(**{
            "nora.outcome": status,
            "nora.media_comprehension.reason": reason,
        })
        return Perceived(status, reason)
