"""The spawned comprehension job (issue #116) — one model pass per medium into
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

from nora.domain.media import MediaBlob, MediaObject
from nora.domain.models import PersonRef
from nora.ingest.comprehension import MediaComprehension, Subject
from nora.obs.metrics import Metrics
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
    """The heart of issue #116. Reading the bytes is the expensive part; the
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
    """The positive control for the test above, and the bug it guards. Custody
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
    """Issue #116's audio decision. The words are already in hand — SenseVoice
    transcribed them inline at ingest — so the title and description come from
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
    assert store.comprehended == [
        (SHA, "a voice note", "asking about tonight", "喂你今日得唔得閒啊",
         "fake-vision"),
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
    """Issue #116's story, at the seam it is about: the marker already rode
    into memory in `messages.body`, and what it pointed at was empty — so every
    reply around a photo was preserved and unreadable. The handle is carried
    with it so a later recall can join the two."""
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
    """D6, unchanged: a message from somebody `identity.toml` does not name is
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
