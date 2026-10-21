"""The spawned comprehension job — one model pass per medium into
title/description/content, off the ingest path.

Every test here asserts on what the job WROTE and on what it COUNTED, not on a
returned value: the job returns nothing by construction (the caller is the
listener's own path and never awaits it), so a test that only checked a return
would be checking `None`.

`_CollectingSpawn` is what makes that observable without a race. The job's
contract is "never awaited by the caller"; the alternative to collecting the
coroutine is a real `create_task` plus a sleep, which is a race written into an
assertion.
"""

from __future__ import annotations

from dataclasses import replace

from nora.domain.media import MediaBlob, MediaObject
from nora.domain.models import PersonRef
from nora.ingest.comprehension import MediaComprehension, Subject
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, span
from nora.testing import (
    FakeBlobStore,
    FakeMediaInterpreter,
    FakeMediaObjectStore,
    FakeMemoryStore,
)

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")

SHA = "a" * 64
BYTES = b"\x89PNG some pixels"


class _CollectingSpawn:
    """`spawn`, collected instead of backgrounded — see the module docstring."""

    def __init__(self) -> None:
        self.coros: list = []

    def __call__(self, coro) -> None:
        self.coros.append(coro)

    async def drain(self) -> None:
        for coro in self.coros:
            await coro
        self.coros.clear()

    def discard(self) -> None:
        """Close what a test only meant to COUNT. A coroutine that is created
        and never awaited warns at GC with no traceback, in whichever test
        happens to be running when the collector fires — which is exactly the
        kind of noise that trains a reader to ignore warnings."""
        for coro in self.coros:
            coro.close()
        self.coros.clear()


def _blobs() -> FakeBlobStore:
    blobs = FakeBlobStore()
    blobs.blobs[SHA] = BYTES
    return blobs


def _job(
    *, interpreter=None, store=None, blobs=None, memory=None, metrics=None,
    spawn=None, stickers_enabled: bool = True,
) -> tuple[MediaComprehension, _CollectingSpawn]:
    spawn = spawn or _CollectingSpawn()
    job = MediaComprehension(
        blobs=blobs if blobs is not None else _blobs(),
        store=store if store is not None else FakeMediaObjectStore(),
        interpreter=interpreter if interpreter is not None else FakeMediaInterpreter(),
        memory=memory if memory is not None else FakeMemoryStore(),
        metrics=metrics if metrics is not None else Metrics(),
        spawn=spawn,
        stickers_enabled=stickers_enabled,
    )
    return job, spawn


def _subject(*, label: str = "image", media_type: str = "image/png",
             transcript: str = "") -> Subject:
    return Subject(
        blob=MediaBlob(sha256=SHA, media_type=media_type, size=len(BYTES)),
        label=label, transcript=transcript,
    )


def _schedule(job, subject, *, person=None, platform: str = "signal") -> None:
    job.schedule(
        subjects=(subject,), platform=platform, conversation_id="g1",
        person=person, timestamp=1_700_000_000_000,
    )


# --- what one read produces -------------------------------------------------


async def test_one_pass_writes_all_three_fields_and_the_model_that_wrote_them():
    """Reading the bytes is the expensive part; the
    title and the legible content come back from the SAME call, and all three
    land on the row so each consumer can select what it wants at render."""
    store = FakeMediaObjectStore()
    interpreter = FakeMediaInterpreter(
        "a bowl of wonton noodles", title="a plate of noodles",
        content="生記粥麵",
    )
    job, spawn = _job(interpreter=interpreter, store=store)

    _schedule(job, _subject())
    await spawn.drain()

    assert store.comprehended == [
        (SHA, "a plate of noodles", "a bowl of wonton noodles", "生記粥麵",
         "fake-vision"),
    ]


async def test_the_label_reaches_the_interpreter_so_it_can_pick_its_question():
    """The prompt is selected by LABEL — the platform's own sticker flag plus
    the blob's MIME type, a system-controlled fact. A sticker is asked what it
    depicts and explicitly not what its sender meant."""
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter)

    _schedule(job, _subject(label="sticker"))
    await spawn.drain()

    assert interpreter.labels == ["sticker"]


async def test_the_bytes_come_back_from_custody_rather_than_across_the_spawn():
    """The handle in the body is a promise the bytes are retrievable, so the
    job exercising that promise is honest — and it keeps a burst of ten photos
    from holding ten payloads alive on the listener's own loop until a
    background task drains them."""
    blobs = _blobs()
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter, blobs=blobs)

    _schedule(job, _subject())
    await spawn.drain()

    assert interpreter.calls == [("image/png", len(BYTES))], (
        "the interpreter saw the bytes the blob store produced, not a payload "
        "carried across the spawn"
    )


# --- R-27's three states ----------------------------------------------------


async def test_a_medium_nothing_can_read_is_nothing_found_and_never_a_model_call():
    """A PDF, an archive, a Discord lottie declared `application/json`. This is
    a real, final answer ABOUT those bytes — not a failure — and saying so is
    what stops the job retrying it on every future sighting. Handing JSON to a
    vision model is not a degraded read, it is a wrong one."""
    metrics = Metrics()
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter, metrics=metrics)

    _schedule(job, _subject(label="document", media_type="application/pdf"))
    await spawn.drain()

    assert interpreter.calls == []
    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="document",
        status="nothing_found", reason="unreadable",
    ) == 1


async def test_a_blob_that_cannot_be_opened_is_unavailable_not_nothing_found():
    """Custody said the bytes were held and the store cannot produce them — a
    rotated key, a pruned volume. That says NOTHING about what the image was,
    so it can never be «there was nothing there» (R-27)."""
    metrics = Metrics()
    job, spawn = _job(blobs=FakeBlobStore(), metrics=metrics)

    _schedule(job, _subject())
    await spawn.drain()

    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="unavailable", reason="blob",
    ) == 1
    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="nothing_found", reason="blob",
    ) == 0


async def test_an_already_read_sha256_is_not_read_again():
    """A description is a pure function of the bytes, so the same meme
    forwarded five times is ONE read. Counted `nothing_found`/`already_read`
    rather than `ok`: nothing was comprehended on this pass, and counting a
    cache hit as ok would make the ok rate say the describer is working when it
    might not have run for days."""
    metrics = Metrics()
    store = FakeMediaObjectStore([MediaObject(
        sha256=SHA, media_type="image/png", size=4, source="inbound",
        title="a cat", description="a cartoon cat", content="",
        created_at_ms=1,
    )])
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter, store=store, metrics=metrics)

    _schedule(job, _subject())
    await spawn.drain()

    assert interpreter.calls == []
    assert store.comprehended == []
    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="nothing_found", reason="already_read",
    ) == 1


async def test_an_indexed_but_unread_row_is_still_read():
    """The positive control for the test above. Custody
    writes a row with all three fields EMPTY for every byte it holds, so a job
    that treated «a row exists» as «already read» would never read a single
    photo — and every row in the index would stay an index entry forever."""
    store = FakeMediaObjectStore([MediaObject(
        sha256=SHA, media_type="image/png", size=4, source="inbound",
        description="", created_at_ms=1,
    )])
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter, store=store)

    _schedule(job, _subject())
    await spawn.drain()

    assert len(interpreter.calls) == 1
    assert len(store.comprehended) == 1


async def test_two_sightings_in_flight_collapse_to_one_read():
    """The Discord listener is a separate task from the Signal one, so two
    people sending the same new photo seconds apart is a real race — two model
    calls and two writers on one row without this."""
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter)

    _schedule(job, _subject())
    _schedule(job, _subject())          # same sha256, first still in flight

    assert len(spawn.coros) == 1
    await spawn.drain()
    assert len(interpreter.calls) == 1


# --- the audio lane ---------------------------------------------------------


async def test_a_voice_note_summarizes_its_transcript_and_never_listens_again():
    """The audio lane. The words are already in hand — the STT
    service transcribed them inline at ingest — so the title and description come from
    a cheap TEXT pass, and `content` is the transcript itself, stored whole."""
    store = FakeMediaObjectStore()
    interpreter = FakeMediaInterpreter("asking about tonight", title="a voice note")
    job, spawn = _job(interpreter=interpreter, store=store)

    _schedule(job, _subject(
        label="voice note", media_type="audio/aac",
        transcript="喂你今日得唔得閒啊",
    ))
    await spawn.drain()

    assert interpreter.calls == [], "no second listen"
    assert interpreter.summarized == ["喂你今日得唔得閒啊"]
    # The TEXT model, not the audio describer: a voice note's headline is
    # written entirely by `comprehension_text_model`, so the column is derived
    # from that model, not from the media type (which would record `""` for
    # the one modality with a single named author).
    assert store.comprehended == [
        (SHA, "a voice note", "asking about tonight", "喂你今日得唔得閒啊",
         "fake-text"),
    ]


async def test_a_voice_note_keeps_its_words_even_when_the_summary_fails():
    """The transcript is EVIDENCE; the title is a convenience. Throwing away
    what somebody said because a headline could not be written would be the
    worse of the two losses by a wide margin."""
    store = FakeMediaObjectStore()
    interpreter = FakeMediaInterpreter(available=False, reason="timeout")
    job, spawn = _job(interpreter=interpreter, store=store)

    _schedule(job, _subject(
        label="voice note", media_type="audio/aac",
        transcript="喂你今日得唔得閒啊",
    ))
    await spawn.drain()

    assert store.comprehended == [(SHA, "", "", "喂你今日得唔得閒啊", "")]


async def test_a_voice_note_with_no_transcript_claims_nothing():
    """STT failed, or the note was silent. There is nothing to summarize and
    nothing to claim — never a guessed transcript (R-27)."""
    metrics = Metrics()
    store = FakeMediaObjectStore()
    job, spawn = _job(store=store, metrics=metrics)

    _schedule(job, _subject(label="voice note", media_type="audio/aac"))
    await spawn.drain()

    assert store.comprehended == []
    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="voice note",
        status="unavailable", reason="empty",
    ) == 1


# --- what reaches Honcho ----------------------------------------------------


async def test_all_three_fields_reach_honcho_under_the_senders_peer():
    """The marker rides into memory in `messages.body`, and what it points at
    must not be empty, or every reply around a photo is preserved and
    unreadable. The handle is carried with it so a later recall can join the
    two."""
    memory = FakeMemoryStore()
    interpreter = FakeMediaInterpreter(
        "a bowl of wonton noodles", title="a plate of noodles", content="生記粥麵")
    job, spawn = _job(interpreter=interpreter, memory=memory)

    _schedule(job, _subject(), person=MILES)
    await spawn.drain()

    assert len(memory.fed) == 1
    text = memory.fed[0][1]
    assert SHA[:6] in text
    assert "a plate of noodles" in text
    assert "a bowl of wonton noodles" in text
    assert "生記粥麵" in text


async def test_an_unmapped_sender_is_never_fed():
    """A message from somebody `identity.toml` does not name is
    never fed to the semantic store, and a photo does not change that. The
    comprehension is still WRITTEN — the index is not identity-scoped."""
    memory = FakeMemoryStore()
    store = FakeMediaObjectStore()
    job, spawn = _job(memory=memory, store=store)

    _schedule(job, _subject(), person=None)
    await spawn.drain()

    assert memory.fed == []
    assert len(store.comprehended) == 1, "the index write is not identity-scoped"


# --- the inert states -------------------------------------------------------


async def test_stickers_are_excluded_when_the_operator_switched_them_off():
    """`[media] sticker_descriptions = false`. It excludes stickers from the
    READ and nothing else — the identity ladder in `StickerReader` is custody,
    and is what makes a repeat sighting cost zero platform RPC.

    The positive control is the second schedule: the same job reads an image,
    so this cannot pass by scheduling nothing at all."""
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(interpreter=interpreter, stickers_enabled=False)

    _schedule(job, _subject(label="sticker"))
    assert spawn.coros == []

    _schedule(job, Subject(
        blob=MediaBlob(sha256="b" * 64, media_type="image/png", size=4),
        label="image",
    ))
    assert len(spawn.coros) == 1
    spawn.discard()


async def test_a_collaborator_that_raises_costs_the_read_and_nothing_else():
    """The job runs in a spawned task, where an exception would surface only as
    a done-callback log line. It is counted `unavailable`/`error` instead, and
    the in-flight slot is released so the next sighting is not wedged."""

    class _Explodes(FakeMediaInterpreter):
        async def describe(self, *, data, media_type, label=""):
            raise RuntimeError("gateway on fire")

    metrics = Metrics()
    job, spawn = _job(interpreter=_Explodes(), metrics=metrics)

    _schedule(job, _subject())
    await spawn.drain()

    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="unavailable", reason="error",
    ) == 1

    # Not wedged: the same sha256 schedules again rather than being swallowed
    # by a leaked in-flight entry.
    _schedule(job, _subject())
    assert len(spawn.coros) == 1
    spawn.discard()


# --- what the screen catches on the way to memory ---------------------------


async def test_an_identifier_in_a_comprehension_never_reaches_memory():
    """Memory is screened too. The text rides to Honcho screened by
    `scan_identifiers`; a render-time screen is the stricter position for the
    TRANSCRIPT and no position at all for memory, which does not render.

    A comprehension written unscreened into a peer is worse than one written
    into a transcript, because nothing re-screens a peer card at render — a
    voice note containing a phone number would be durable and resurfacing.
    A hit costs the DETAIL and keeps the marker, exactly as the render join
    does: memory still records that media arrived and claims nothing about it.
    """
    memory = FakeMemoryStore()
    metrics = Metrics()
    interpreter = FakeMediaInterpreter(
        "a card reading +85212345678", title="a business card")
    job, spawn = _job(interpreter=interpreter, memory=memory, metrics=metrics)

    _schedule(job, _subject(), person=MILES)
    await spawn.drain()

    assert len(memory.fed) == 1
    text = memory.fed[0][1]
    assert "85212345678" not in text
    assert "business card" not in text, "the whole detail goes, not just the digits"
    assert SHA[:6] in text, "the marker stays — media DID arrive"
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_clean_comprehension_still_reaches_memory_whole():
    """The positive control for the screen: it must not be eating everything.
    All three fields, under the marker."""
    memory = FakeMemoryStore()
    interpreter = FakeMediaInterpreter(
        "a bowl of wonton noodles", title="a plate of noodles", content="生記粥麵")
    job, spawn = _job(interpreter=interpreter, memory=memory)

    _schedule(job, _subject(), person=MILES)
    await spawn.drain()

    text = memory.fed[0][1]
    assert "a plate of noodles" in text
    assert "a bowl of wonton noodles" in text
    assert "生記粥麵" in text


async def test_a_screened_comprehension_is_still_stored():
    """The screen is an EGRESS screen. The row keeps what the model read — that
    is the record, and `media_fetch` applies its own screen on every serve —
    while the copy that leaves for memory is the one that loses the detail."""
    store = FakeMediaObjectStore()
    interpreter = FakeMediaInterpreter("a card reading +85212345678")
    job, spawn = _job(interpreter=interpreter, store=store)

    _schedule(job, _subject(), person=MILES)
    await spawn.drain()

    assert len(store.comprehended) == 1
    assert "85212345678" in store.comprehended[0][2]


# --- the model recorded against a row ---------------------------------------


async def test_the_row_records_the_describer_this_modality_actually_used():
    """`describe_model` is the provenance of the text on the row. Recording the
    IMAGE model against a clip that the video describer read is a lie in the
    one column an operator reads to find out which describer produced what;
    `model_for` on the adapter is what supplies it."""

    class _PerModality(FakeMediaInterpreter):
        async def describe(self, *, data, media_type, label=""):
            got = await super().describe(
                data=data, media_type=media_type, label=label)
            return replace(got, model="k3-256k")

    store = FakeMediaObjectStore()
    job, spawn = _job(interpreter=_PerModality(), store=store)

    _schedule(job, _subject(label="video", media_type="video/mp4"))
    await spawn.drain()

    assert store.comprehended[0][4] == "k3-256k"


async def test_a_videos_chunk_record_travels_with_its_result():
    """Returned, never stashed — see `MediaDescription.provenance`. Asserted at
    the STORE, which is where a lost or mis-attached record would actually be
    read back from."""

    class _WithProvenance(FakeMediaInterpreter):
        async def describe(self, *, data, media_type, label=""):
            got = await super().describe(
                data=data, media_type=media_type, label=label)
            return replace(got, provenance={"video_capped": True})

    store = FakeMediaObjectStore()
    job, spawn = _job(interpreter=_WithProvenance(), store=store)

    _schedule(job, _subject(label="video", media_type="video/mp4"))
    await spawn.drain()

    assert store.objs[SHA].provenance == {"video_capped": True}


# --- the trace root ---------------------------------------------------------


async def test_the_read_is_its_own_trace_and_reaches_the_collector(
    exported_spans,
):
    """DETACHING AND DROPPING ARE TWO DECISIONS.

    `asyncio.create_task` COPIES the caller's contextvars
    (`nora/obs/tracing.py`), so a span opened inside a spawned job is a CHILD of
    whatever was current when `schedule` was called — here `ingest.media`, which
    would hold the ingest trace open for as long as a chunked video read takes.
    `attached(None)` detaches first.

    The span declares `TRACE_KIND: "generation"`, not `plumbing`, so the
    sampler keeps it. Every branch under this span is a model call, so
    R-76 keeps it. The fixture is built with production's own sampler, and:

      * it is EXPORTED — a root declaring `generation` is kept; and
      * its TRACE ID DIFFERS from `ingest.media`'s — which is what detachment
        means and is a question only the exported ids can answer.

    Both assertions are needed and neither implies the other: re-attaching to
    the ingest span would still export it (as a child, one trace), and going
    back to `plumbing` would still detach it (as a dropped root).
    """
    job, spawn = _job()

    with span("ingest.media"):
        _schedule(job, _subject())
        await spawn.drain()

    spans = {s.name: s for s in exported_spans.get_finished_spans()}
    # CONTROL — the exporter was capturing at all. Without it every assertion
    # below is also true of a run in which nothing was traced.
    assert "ingest.media" in spans, "the exporter captured nothing"

    assert "comprehend" in spans, (
        "the comprehension span never reached the collector — it is either "
        "declaring `plumbing` again (the sampler drops such a root) or no "
        "longer opened at all"
    )
    assert (
        spans["comprehend"].context.trace_id != spans["ingest.media"].context.trace_id
    ), (
        "the comprehension span is in the SAME trace as the ingest span, so it "
        "is a CHILD of it: `attached(None)` is missing and the ingest trace is "
        "held open for as long as a video read takes"
    )
    assert spans["comprehend"].parent is None, "the comprehension span is not a root"
    # The R-76 root declaration: the span's `TRACE_KIND` is `generation`. This
    # span is not itself a model-invoke span (there is no child one for the
    # vision call either; see `nora.agent.telemetry`'s module docstring), only
    # the root a model call CAN happen under.
    assert spans["comprehend"].attributes[TRACE_KIND] == "generation"


async def test_the_caveat_reaches_memory_with_the_reading():
    """What a read did NOT see is not a rendering detail. A recalled
    description of half a clip is as wrong in a peer card as in a transcript —
    and worse, because nothing re-screens or re-qualifies a card at render."""

    class _Capped(FakeMediaInterpreter):
        async def describe(self, *, data, media_type, label=""):
            got = await super().describe(
                data=data, media_type=media_type, label=label)
            return replace(got, provenance={
                "media_caveat": "(I only watched the first 4 of them.)"})

    memory = FakeMemoryStore()
    job, spawn = _job(interpreter=_Capped(), memory=memory)

    _schedule(job, _subject(label="video", media_type="video/mp4"), person=MILES)
    await spawn.drain()

    assert len(memory.fed) == 1
    assert "I only watched the first 4" in memory.fed[0][1]


async def test_a_whole_read_carries_no_caveat_into_memory():
    """The control: a caveat on every reading is a caveat that means nothing,
    and it would also teach her to hedge about clips she saw in full."""
    memory = FakeMemoryStore()
    job, spawn = _job(memory=memory)

    _schedule(job, _subject(), person=MILES)
    await spawn.drain()

    assert "watched" not in memory.fed[0][1]


async def test_a_write_that_did_not_land_is_not_counted_ok():
    """`SqlMediaObjectStore.comprehend` answers False on any database failure
    and the job must not count `ok` on it — the «a tick that wrote nothing
    reporting ok» shape docs/maintainers/conventions-rationale.md names.

    The FEED goes with it: Honcho would otherwise hold a description of bytes
    `media_objects` has no row for, and a redelivery would feed the same words
    again with no row to dedup against."""
    class _WriteFails(FakeMediaObjectStore):
        """The index READS fine and the write does not — which is the shape
        that matters and the shape a blanket `fail=True` cannot produce: with
        the lookup failing too, the job returns at the already-read gate and
        the write is never reached, so the assertion below would pass against a
        build that ignores the return value."""

        async def comprehend(self, **kw):
            return False

    metrics = Metrics()
    memory = FakeMemoryStore()
    store = _WriteFails()
    job, spawn = _job(store=store, memory=memory, metrics=metrics)

    _schedule(job, _subject(), person=MILES)
    await spawn.drain()

    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="unavailable", reason="store") == 1

    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="ok", reason="") == 0
    assert memory.fed == [], "nothing reached memory that nothing stored"


async def test_a_forwarded_voice_note_does_not_re_feed_the_same_words():
    """The shipped default's own path. With `comprehension_text_model = ""`
    the text pass answers `no_text_describer` every time, and `_already_read`
    keys on title/description for a content label — so a forwarded voice note
    must not re-run the pass and feed the SAME words to memory on every
    sighting.

    The words are already stored and already remembered; a second sighting has
    nothing to add."""
    store = FakeMediaObjectStore()
    memory = FakeMemoryStore()
    interpreter = FakeMediaInterpreter(available=False, reason="no_text_describer")
    job, spawn = _job(interpreter=interpreter, store=store, memory=memory)

    subject = _subject(
        label="voice note", media_type="audio/aac",
        transcript="喂你今日得唔得閒啊")
    _schedule(job, subject, person=MILES)
    await spawn.drain()
    _schedule(job, subject, person=MILES)          # the same bytes, forwarded
    await spawn.drain()

    assert len(memory.fed) == 1, "the same words reached memory twice"
    assert len(store.comprehended) == 1


async def test_a_voice_note_whose_words_changed_is_still_written():
    """The control for the guard above — and the case that keeps it from being
    «never write a voice note twice». It is the CONTENT that decides: a row
    holding different words is a row with something to add."""
    store = FakeMediaObjectStore()
    memory = FakeMemoryStore()
    interpreter = FakeMediaInterpreter(available=False, reason="no_text_describer")
    job, spawn = _job(interpreter=interpreter, store=store, memory=memory)

    _schedule(job, _subject(
        label="voice note", media_type="audio/aac", transcript="first"),
        person=MILES)
    await spawn.drain()
    _schedule(job, _subject(
        label="voice note", media_type="audio/aac", transcript="second"),
        person=MILES)
    await spawn.drain()

    assert [c[3] for c in store.comprehended] == ["first", "second"]


async def test_an_unreadable_index_does_not_look_like_an_unread_blob():
    """The already-read gate must tell a missing row from an unreachable
    database: `get` answers `None` for both, so a transient outage would read
    as «not yet comprehended» and the job would re-run the describer. On a
    video that is a fresh ten-window read, paid for by an index that was merely
    briefly down."""
    metrics = Metrics()
    interpreter = FakeMediaInterpreter()
    job, spawn = _job(
        interpreter=interpreter, store=FakeMediaObjectStore(fail=True),
        metrics=metrics)

    _schedule(job, _subject())
    await spawn.drain()

    assert interpreter.calls == [], "the describer ran against an unknown row"
    assert metrics.get(
        "nora_media_comprehension_total", platform="signal", label="image",
        status="unavailable", reason="store") == 1


async def test_concurrent_reads_are_bounded():
    """`_inflight` dedupes identical BYTES, not volume. `spawn` is a bare
    `create_task`, so fifty photos in a minute would be fifty concurrent
    describer calls, each holding a decrypted blob plus its base64 copy against
    an 8 MiB still ceiling and a 100 MiB video one.

    Re-reading the bytes in the job moves the memory spike off the listener's
    loop; it does not bound it. This is the bound.

    Measured on the PEAK, not on the total: what matters is how many are inside
    the describer at once, which is what holds the memory."""
    import asyncio

    peak = 0
    live = 0

    class _Slow(FakeMediaInterpreter):
        async def describe(self, *, data, media_type, label=""):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0)
            try:
                return await super().describe(
                    data=data, media_type=media_type, label=label)
            finally:
                live -= 1

    blobs = FakeBlobStore()
    subjects = []
    for i in range(12):
        sha = f"{i:06x}" + "0" * 58
        blobs.blobs[sha] = BYTES
        subjects.append(Subject(
            blob=MediaBlob(sha256=sha, media_type="image/png", size=4),
            label="image",
        ))

    spawn = _CollectingSpawn()
    job = MediaComprehension(
        blobs=blobs, store=FakeMediaObjectStore(),
        interpreter=_Slow(), memory=FakeMemoryStore(), metrics=Metrics(),
        spawn=spawn, max_concurrent=3,
    )
    job.schedule(
        subjects=tuple(subjects), platform="signal", conversation_id="g1",
        person=None, timestamp=1,
    )

    # Concurrently, which is how `BackgroundTasks.spawn` runs them.
    await asyncio.gather(*(asyncio.create_task(c) for c in spawn.coros))
    spawn.coros.clear()

    assert peak <= 3, f"{peak} reads were in flight at once against a bound of 3"
    assert peak > 1, "the harness did not actually overlap them"
