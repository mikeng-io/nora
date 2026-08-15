"""The sticker dedup ladder, one test per rung (sticker spec §4). These are the
tests that would go green against a reader that always called the vision model,
so each one asserts on the CALL LOGS of the collaborators rather than only on
the returned description — "no RPC happened" is the property that matters, and
a description alone cannot prove it.

**On the single-flight test specifically.** `asyncio.gather` alone does NOT
force two `reader.read()` calls to race: every fake collaborator in this module
is synchronous code wrapped in `async def`, with no genuine suspension point
anywhere in the ladder. Verified separately (a throwaway probe script mirroring
this exact shape) that when neither coroutine truly `await`s anything
unresolved, the first Task runs to FULL completion — in-flight entry
registered, used, and cleaned up — before the event loop ever schedules the
second Task. Two plain `reader.read()` calls under `asyncio.gather` would
therefore call the interpreter twice regardless of whether single-flight is
implemented at all, and the test would prove nothing.

`_SlowMediaInterpreter` below fixes that by giving the interpreter one genuine
suspension point (`await asyncio.sleep(...)`), standing in for the real
interpreter's HTTP round trip — the same `_EchoRunner(delay=...)` pattern
`tests/unit/test_engagement_pipeline.py` already uses to force two pipeline
runs to overlap. With that suspension in place, the first `read()` reaches the
interpreter and yields control mid-flight; the second `read()`, issued while
the first is still suspended there, genuinely observes the first one's
in-flight future rather than an already-cleaned-up map.
"""

from __future__ import annotations

import asyncio
import hashlib

from nora.domain.media import MediaBlob, MediaRef
from nora.ingest.stickers import StickerReader
from nora.testing import (
    FakeBlobStore,
    FakeMediaDescriptionStore,
    FakeMediaInterpreter,
    FakeMediaSource,
)

TOKEN = "sticker:8c6ce85a:15"  # noqa: S105
BYTES = b"\x89PNG\r\n\x1a\n sticker-ish"
REF = MediaRef(platform_id="", media_type="image/png", sticker=True, fetch_token=TOKEN)


def _source(payload: bytes | None = BYTES) -> FakeMediaSource:
    src = FakeMediaSource({} if payload is None else {TOKEN: payload})
    src.platform = "signal"
    return src


def _reader(store, interpreter, blobs=None) -> StickerReader:
    return StickerReader(
        blobs=blobs or FakeBlobStore(), descriptions=store,
        interpreter=interpreter, model="fake-vision",
    )


class _SlowMediaInterpreter(FakeMediaInterpreter):
    """`FakeMediaInterpreter` with one genuine suspension point inserted
    before it does its (synchronous) work — see the module docstring for why
    the plain fake cannot exercise a real race on its own."""

    def __init__(self, *args, delay: float = 0.02, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.delay = delay

    async def describe(self, *, data: bytes, media_type: str):
        await asyncio.sleep(self.delay)
        return await super().describe(data=data, media_type=media_type)


async def test_an_identity_hit_makes_no_platform_call_and_no_vision_call():
    """The 41% case, and the whole reason the identity level exists: it is the
    only level checkable BEFORE the bytes move, on a path the listener awaits."""
    store = FakeMediaDescriptionStore()
    blob = MediaBlob(sha256="a" * 64, media_type="image/png", size=len(BYTES))
    store.index[("signal", TOKEN)] = blob
    store.descriptions["a" * 64] = "a cartoon cat"
    source, interpreter = _source(), FakeMediaInterpreter()

    out = await _reader(store, interpreter).read(
        platform="signal", ref=REF, source=source)

    assert out.blob == blob
    assert out.description == "a cartoon cat"
    assert out.cached == "identity"
    assert source.fetched == [], "no platform RPC on a repeat sticker"
    assert interpreter.calls == [], "no vision call on a repeat sticker"


async def test_a_content_hit_fetches_but_does_not_describe():
    """The same art in a second pack, or on the other platform. One RPC is
    unavoidable — the hash is only knowable from the bytes — but the expensive
    half is not paid twice."""
    store = FakeMediaDescriptionStore()
    blobs = FakeBlobStore()
    sha = hashlib.sha256(BYTES).hexdigest()
    store.descriptions[sha] = "a cartoon cat"
    source, interpreter = _source(), FakeMediaInterpreter()

    out = await _reader(store, interpreter, blobs).read(
        platform="signal", ref=REF, source=source)

    assert out.cached == "content"
    assert out.description == "a cartoon cat"
    assert source.fetched == [TOKEN]
    assert interpreter.calls == []


async def test_a_cold_sticker_pays_once_and_is_remembered():
    store = FakeMediaDescriptionStore()
    source, interpreter = _source(), FakeMediaInterpreter("a cartoon cat")

    out = await _reader(store, interpreter).read(
        platform="signal", ref=REF, source=source)

    assert out.cached == "none"
    assert out.description == "a cartoon cat"
    assert len(interpreter.calls) == 1
    assert store.remembered == [("signal", TOKEN, out.blob.sha256)]


async def test_two_simultaneous_sightings_make_one_vision_call():
    """Signal ingest is serialized but the Discord listener is a separate
    task, so this race is real rather than theoretical — see the module
    docstring for why `_SlowMediaInterpreter` is what makes it REAL in this
    test rather than an accident of `asyncio.gather` scheduling."""
    store = FakeMediaDescriptionStore()
    source, interpreter = _source(), _SlowMediaInterpreter()
    reader = _reader(store, interpreter)

    first = asyncio.create_task(
        reader.read(platform="signal", ref=REF, source=source))
    # One tick is enough for `first` to register itself in the in-flight map
    # and reach the interpreter's real suspension point — see the module
    # docstring's probe. Bounded implicitly: `first` is awaited below, so a
    # `first` that never reaches that point fails the test rather than
    # hanging it.
    await asyncio.sleep(0)
    second = await reader.read(platform="signal", ref=REF, source=source)
    first_outcome = await first

    assert len(interpreter.calls) == 1
    assert {first_outcome.description, second.description} == {"a cartoon cat"}


async def test_an_unfetchable_sticker_yields_no_blob_and_a_reason():
    store = FakeMediaDescriptionStore()
    interpreter = FakeMediaInterpreter()

    out = await _reader(store, interpreter).read(
        platform="signal", ref=REF, source=_source(payload=None))

    assert out.blob is None
    assert out.description == ""
    assert out.reason == "not_found"
    assert interpreter.calls == []


async def test_a_failed_interpretation_still_yields_the_blob():
    """Custody succeeded even though the read did not. The body must keep its
    handle — `[sticker a3f2c1]` — because the bytes really are held."""
    store = FakeMediaDescriptionStore()
    interpreter = FakeMediaInterpreter(available=False, reason="timeout")

    out = await _reader(store, interpreter).read(
        platform="signal", ref=REF, source=_source())

    assert out.blob is not None
    assert out.description == ""
    assert out.reason == "timeout"
    assert store.remembered == [], "a failure is never cached as a description"


async def test_a_store_outage_costs_a_re_description_not_the_sticker():
    store = FakeMediaDescriptionStore(unavailable=True)
    interpreter = FakeMediaInterpreter()

    out = await _reader(store, interpreter).read(
        platform="signal", ref=REF, source=_source())

    assert out.description == "a cartoon cat"
    assert out.blob is not None


async def test_a_reader_raising_mid_ladder_degrades_to_unavailable_and_cleans_up():
    """`read` is called from the ingest path; a sticker must never cost the
    message. A collaborator that raises must still produce an honest outcome,
    AND the in-flight entry must not be leaked — a leaked future would wedge
    every later sighting of that sticker forever."""

    class _Explodes(FakeMediaDescriptionStore):
        async def resolve(self, *, platform: str, token: str):
            raise RuntimeError("db on fire")

    store = _Explodes()
    interpreter = FakeMediaInterpreter()
    reader = _reader(store, interpreter)

    out = await reader.read(platform="signal", ref=REF, source=_source())
    assert out.blob is None
    assert out.reason == "error"

    # No leaked in-flight entry: a second sighting after the failure must run
    # its own fresh attempt rather than hanging forever on a future nobody
    # will ever resolve.
    out2 = await asyncio.wait_for(
        reader.read(platform="signal", ref=REF, source=_source()), timeout=2.0)
    assert out2.blob is None
    assert out2.reason == "error"
