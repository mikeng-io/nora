"""The sticker dedup ladder, one test per rung. These are the
tests that would go green against a reader that always went back to the
platform, so each one asserts on the CALL LOGS of the collaborators rather than
only on the returned blob — "no RPC happened" is the property that matters, and
a blob alone cannot prove it.

**There is no MODEL rung.** This ladder is custody: identity resolves to a
blob before any bytes move, a miss fetches once and records the identity so
the next sighting does not. The description is the spawned comprehension
job's (`tests/unit/test_media_comprehension.py`), keyed on the same sha256 —
one mechanism for every medium instead of one for stickers and none for
photos.

**On the single-flight test specifically.** `asyncio.gather` alone does NOT
force two `reader.read()` calls to race: every fake collaborator in this module
is synchronous code wrapped in `async def`, with no genuine suspension point
anywhere in the ladder. When neither coroutine truly `await`s anything
unresolved, the first Task runs to FULL completion — in-flight entry
registered, used, and cleaned up — before the event loop ever schedules the
second Task. Two plain `reader.read()` calls under `asyncio.gather` would
therefore call the platform twice regardless of whether single-flight is
implemented at all, and the test would prove nothing.

`_SlowMediaSource` below fixes that by giving the FETCH one genuine suspension
point (`await asyncio.sleep(...)`), standing in for the real platform RPC — the
same `_EchoRunner(delay=...)` pattern `tests/unit/test_engagement_pipeline.py`
already uses to force two pipeline runs to overlap. With that suspension in
place, the first `read()` reaches the fetch and yields control mid-flight; the
second `read()`, issued while the first is still suspended there, genuinely
observes the first one's in-flight future rather than an already-cleaned-up
map.
"""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from nora.domain.media import MediaBlob, MediaRef
from nora.ingest.stickers import StickerReader
from nora.obs.metrics import Metrics
from nora.platform.signal.parser import parse_event
from nora.testing import (
    FakeBlobStore,
    FakeMediaDescriptionStore,
    FakeMediaSource,
)

TOKEN = "sticker:8c6ce85a:15"  # noqa: S105
BYTES = b"\x89PNG\r\n\x1a\n sticker-ish"
REF = MediaRef(platform_id="", media_type="image/png", sticker=True, fetch_token=TOKEN)


def _source(payload: bytes | None = BYTES) -> FakeMediaSource:
    src = FakeMediaSource({} if payload is None else {TOKEN: payload})
    src.platform = "signal"
    return src


def _reader(store, blobs=None, metrics=None) -> StickerReader:
    return StickerReader(
        blobs=blobs or FakeBlobStore(), descriptions=store,
        metrics=metrics if metrics is not None else Metrics(),
    )


class _SlowMediaSource(FakeMediaSource):
    """`FakeMediaSource` with one genuine suspension point inserted before it
    answers — see the module docstring for why the plain fake cannot exercise
    a real race on its own. It stands in for the platform RPC, which is what
    single-flight now exists to spend only once."""

    def __init__(self, *args, delay: float = 0.02, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.delay = delay
        self.platform = "signal"

    async def fetch(self, *, ref):
        await asyncio.sleep(self.delay)
        return await super().fetch(ref=ref)


async def test_an_identity_hit_makes_no_platform_call_at_all():
    """The common case, and the whole reason the identity level exists: it is the
    only level checkable BEFORE the bytes move, on a path the listener awaits."""
    store = FakeMediaDescriptionStore()
    blob = MediaBlob(sha256="a" * 64, media_type="image/png", size=len(BYTES))
    store.index[("signal", TOKEN)] = blob
    blobs = FakeBlobStore()
    blobs.blobs["a" * 64] = BYTES
    source = _source()

    out = await _reader(store, blobs).read(
        platform="signal", ref=REF, source=source)

    assert out.blob == blob
    assert out.level == "identity"
    assert source.fetched == [], "no platform RPC on a repeat sticker"
    assert store.remembered == [], "a hit re-records nothing"


async def test_a_cold_sticker_fetches_once_and_records_its_identity():
    """The identity write is what makes the NEXT sighting free. Without it the
    ladder would fetch every time."""
    store = FakeMediaDescriptionStore()
    source = _source()

    out = await _reader(store).read(platform="signal", ref=REF, source=source)

    assert out.level == "content"
    assert out.blob is not None
    assert out.blob.sha256 == hashlib.sha256(BYTES).hexdigest()
    assert source.fetched == [TOKEN]
    assert store.remembered == [("signal", TOKEN, out.blob.sha256)]


async def test_the_identity_write_never_carries_a_description():
    """`description=""` means "index this identity, describe nothing" — the
    comprehension is the spawned job's and is keyed on the same sha256. If the
    ladder wrote a description here it would clobber whatever that job had
    already learned about these bytes, every time the sticker was seen again.

    Positive control: the identity row IS written (the test above asserts it),
    so a reader that wrote nothing at all would not pass this by doing less."""
    store = FakeMediaDescriptionStore()

    out = await _reader(store).read(platform="signal", ref=REF, source=_source())

    assert out.blob is not None
    assert store.descriptions[out.blob.sha256] == ""


async def test_two_simultaneous_sightings_make_one_platform_call():
    """Signal ingest is serialized but the Discord listener is a separate
    task, so this race is real rather than theoretical — see the module
    docstring for why `_SlowMediaSource` is what makes it REAL in this
    test rather than an accident of `asyncio.gather` scheduling."""
    store = FakeMediaDescriptionStore()
    source = _SlowMediaSource({TOKEN: BYTES})
    reader = _reader(store)

    first = asyncio.create_task(
        reader.read(platform="signal", ref=REF, source=source))
    # One tick is enough for `first` to register itself in the in-flight map
    # and reach the fetch's real suspension point — see the module docstring.
    # Bounded implicitly: `first` is awaited below, so a `first` that
    # never reaches that point fails the test rather than hanging it.
    await asyncio.sleep(0)
    second = await reader.read(platform="signal", ref=REF, source=source)
    first_outcome = await first

    assert source.fetched == [TOKEN]
    assert first_outcome.blob is not None
    assert second.blob == first_outcome.blob


async def test_an_unfetchable_sticker_yields_no_blob_and_a_reason():
    store = FakeMediaDescriptionStore()

    out = await _reader(store).read(
        platform="signal", ref=REF, source=_source(payload=None))

    assert out.blob is None
    assert out.reason == "not_found"
    assert store.remembered == [], "a failed fetch records no identity"


async def test_a_store_outage_costs_the_dedup_not_the_sticker():
    """The identity store is a cache in front of custody. Losing it costs one
    re-fetch on the next sighting, never the sticker."""
    store = FakeMediaDescriptionStore(unavailable=True)

    out = await _reader(store).read(platform="signal", ref=REF, source=_source())

    assert out.blob is not None
    assert out.level == "content"


async def test_a_reader_raising_mid_ladder_degrades_to_unavailable_and_cleans_up():
    """`read` is called from the ingest path; a sticker must never cost the
    message. A collaborator that raises must still produce an honest outcome,
    AND the in-flight entry must not be leaked — a leaked future would wedge
    every later sighting of that sticker forever."""

    class _Explodes(FakeMediaDescriptionStore):
        async def resolve(self, *, platform: str, token: str):
            raise RuntimeError("db on fire")

    store = _Explodes()
    reader = _reader(store)

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


async def test_an_identity_hit_whose_blob_is_gone_claims_no_custody():
    """`blob_missing` must not return `blob=blob` after `BlobStore.get` has
    just PROVEN the bytes absent: the custody stage would append
    `blob.handle`, render `[sticker a3f2c1]`, and count a stored hit, all for a
    hash just shown NOT to be producible. This is the identity-hit-then-gone-blob
    path."""
    store = FakeMediaDescriptionStore()
    blob = MediaBlob(sha256="b" * 64, media_type="image/png", size=123)
    store.index[("signal", TOKEN)] = blob
    # No entry in the blob store for that sha256 — the identity row outlived
    # its bytes (a rotated key, a pruned volume), which is the exact
    # combination that reaches `blob_missing`.
    source = _source()

    out = await _reader(store, FakeBlobStore()).read(
        platform="signal", ref=REF, source=source)

    assert out.blob is None, "custody was just disproven; must not claim it"
    assert out.reason == "blob_missing"
    assert source.fetched == [], "an identity hit still makes no platform call"


async def test_a_real_signal_sticker_envelope_survives_the_ladder():
    """The real Signal parser emits a sticker ref with NO media type at all
    (the envelope declares none), while every other test here hand-builds a
    `MediaRef` with `media_type="image/png"`. A ref with `media_type=""` would
    reach `StickerInterpreter`'s `_INTERPRETABLE` gate and be refused, so every
    real Signal sticker would fail with `unsupported_format`. This test drives
    the real `parse_event` output into the real ladder, not a hand-built ref,
    so it fails against that bug instead of a fake with the fix baked into its
    fixture.

    The sniff matters because the sha256 is computed from these bytes and
    `media_type` is what the comprehension job branches on to pick a
    describer: an unresolved `""` would send every real Signal sticker down
    the `unreadable` path.
    """
    envelope = {
        "envelope": {
            "source": "+15555550123",
            "timestamp": 1755280000000,
            "dataMessage": {
                "message": "",
                "timestamp": 1755280000000,
                "groupInfo": {"groupId": "group.abc123="},
                "sticker": {
                    "packId": "8c6ce85a3156b8e498e1ba0e97bc6620",
                    "stickerId": 15,
                },
            },
        }
    }
    message = parse_event(envelope, own_account="")
    assert message is not None
    assert len(message.media) == 1
    ref = message.media[0]
    assert ref.sticker is True
    assert ref.media_type == "", "the real parser declares no type for a sticker"

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"sticker-payload" * 4
    source = FakeMediaSource({ref.fetch_token: png_bytes})
    source.platform = "signal"
    store = FakeMediaDescriptionStore()

    out = await _reader(store).read(platform="signal", ref=ref, source=source)

    assert out.blob is not None
    assert out.blob.media_type == "image/png", "sniffed from the PNG signature"
    assert out.blob.size == len(png_bytes)


async def test_a_cancelled_winner_does_not_hang_its_waiters():
    """`except Exception` in `read` never catches `asyncio.CancelledError` (a
    `BaseException` since 3.8), so a cancelled winner would skip
    `future.set_result` entirely while `finally` still deleted the in-flight
    map entry, and every waiter awaiting that future would block forever on a
    future nobody would resolve."""
    store = FakeMediaDescriptionStore()
    reader = _reader(store)
    slow = _SlowMediaSource({TOKEN: BYTES}, delay=0.2)

    winner = asyncio.create_task(
        reader.read(platform="signal", ref=REF, source=slow))
    # One tick is enough for `winner` to register itself and reach the
    # fetch's genuine suspension point — the same shape the single-flight
    # test above relies on.
    await asyncio.sleep(0)
    loser = asyncio.create_task(
        reader.read(platform="signal", ref=REF, source=slow))
    await asyncio.sleep(0)  # let `loser` observe the winner's in-flight future

    winner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await winner

    # The loser must resolve — not hang — even though the winner never did.
    loser_outcome = await asyncio.wait_for(loser, timeout=2.0)
    assert loser_outcome.blob is None
    assert loser_outcome.reason == "cancelled"

    # No leaked in-flight entry: a fresh sighting runs its own attempt rather
    # than hanging on the cancelled winner's future.
    fresh = await asyncio.wait_for(
        reader.read(platform="signal", ref=REF, source=_source()), timeout=2.0)
    assert fresh.blob is not None


def _animated_webp(n_frames: int = 3) -> bytes:
    frames = [Image.new("RGB", (4, 4), color=(i * 40, 0, 0)) for i in range(n_frames)]
    out = BytesIO()
    frames[0].save(
        out, format="WEBP", save_all=True, append_images=frames[1:],
        duration=100, loop=0,
    )
    return out.getvalue()


async def test_an_animated_sticker_keeps_all_its_frames():
    """Stickers skip `strip_exif` and hash the fetched bytes directly:
    `strip_exif`'s Pillow re-encode does not pass `save_all`, so a real
    animated WebP would be flattened to ONE frame before it was hashed, held,
    or sent to the model, and content-addressing a re-encoded copy would make
    the cache key depend on the Pillow version (DEC-47's PII argument does not
    apply to published pack art). This asserts on the STORED bytes' frame
    count, not merely that something was stored; the byte-for-byte equality
    check below is what proves the strip was skipped.
    """
    original = _animated_webp(3)
    with Image.open(BytesIO(original)) as probe:
        assert getattr(probe, "n_frames", 1) == 3, "the fixture itself must be animated"

    store = FakeMediaDescriptionStore()
    blobs = FakeBlobStore()
    source = FakeMediaSource({TOKEN: original})
    source.platform = "signal"

    out = await _reader(store, blobs).read(
        platform="signal", ref=REF, source=source)

    assert out.blob is not None
    stored = blobs.blobs[out.blob.sha256]
    assert stored == original, "a sticker must hash the FETCHED bytes, never a re-encoded copy"
    assert out.blob.size == len(original)
    with Image.open(BytesIO(stored)) as held:
        assert getattr(held, "n_frames", 1) == 3, "the animation must survive intact"
