"""A shared reel's, post's or story's thumbnail is HELD at ingest and READ only
when asked (operator rule, 2026-09-24).

Measured before this: a live shared reel and a live shared story, with nobody
tagging Nora, each had its thumbnail described by the vision model at ingest
(`media_objects.description` filled). The rule is that Nora reads shared
content only when tagged, never on every share or forward.

These drive the real chain — `parse_event` → the custody stage → the real
`MediaComprehension` job → `FakeMediaInterpreter` — because the property is
about what reaches a MODEL, and only the interpreter's own record of what it
was asked can say that. A stage-level test that inspected subjects alone would
pass while some other path still read the bytes.
"""

from __future__ import annotations

from nora.agent.egress import scan_identifiers
from nora.domain.media import MediaObject, media_json
from nora.domain.media_join import MediaJoin
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.ingest.comprehension import MediaComprehension
from nora.ingest.stages import media as media_stage
from nora.obs.metrics import Metrics
from nora.platform.instagram.parser import parse_event
from nora.platform.registry import MediaSourceRegistry
from nora.testing import (
    FakeBlobStore,
    FakeMediaInterpreter,
    FakeMediaObjectStore,
    FakeMediaSource,
    FakeMemoryStore,
)

THUMB_URL = "https://cdn.example/thumb.jpg"
PHOTO_URL = "https://cdn.example/photo.jpg"
# Not a decodable image, so `strip_exif` passes it through unchanged and the
# same bytes hash to the same key on both sightings in the dedupe test.
PIXELS = b"\xff\xd8\xff\xe0 synthetic thumbnail bytes"


class _CollectingSpawn:
    """`spawn`, collected rather than backgrounded, so a test can await the
    job without a sleep (the same shape `test_media_comprehension.py` uses)."""

    def __init__(self) -> None:
        self.coros: list = []

    def __call__(self, coro) -> None:
        self.coros.append(coro)

    async def drain(self) -> None:
        for coro in self.coros:
            await coro
        self.coros.clear()


def _event(**over) -> dict:
    base = {
        "kind": "message",
        "room_id": "1000000000000001",
        "message_id": "mid.$synthetic",
        "sender": {"fbid": "2000000000000002", "username": "a_member"},
        "from_me": False,
        "timestamp_ms": 1_790_000_000_000,
        "text": "",
        "mentions": [],
        "reply_to": None,
        "media": None,
    }
    base.update(over)
    return base


def _shared_reel_event() -> dict:
    return _event(
        shared={
            "author": "someaccount",
            "target_url": "https://www.instagram.com/reel/SynthReel01/?is_clips_ad=0",
            "typename": "SlideMessagePortraitXMA",
        },
        media={"kind": "image", "url": THUMB_URL},
    )


def _direct_photo_event() -> dict:
    return _event(media={"kind": "image", "url": PHOTO_URL})


def _row(message) -> TranscriptRow:
    """The stored row a staged message becomes — body and `media_json` as the
    audit store writes them."""
    return TranscriptRow(
        id=1, sender_id=message.sender_id, sender_name=message.sender_name,
        ts=message.timestamp, body=message.body, is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        media_json=media_json(message.media_blobs),
    )


class _Wiring:
    def __init__(self) -> None:
        self.interpreter = FakeMediaInterpreter()
        self.store = FakeMediaObjectStore()
        self.blobs = FakeBlobStore()
        self.spawn = _CollectingSpawn()
        self.job = MediaComprehension(
            blobs=self.blobs, store=self.store, interpreter=self.interpreter,
            memory=FakeMemoryStore(), metrics=Metrics(), spawn=self.spawn,
        )
        source = FakeMediaSource({
            f"attachment:{THUMB_URL}": PIXELS,
            f"attachment:{PHOTO_URL}": PIXELS,
        })
        source.platform = "instagram"
        self.sources = MediaSourceRegistry([source])

    async def ingest(self, event: dict):
        message = parse_event(event)
        assert message is not None
        staged = await media_stage.run(
            message=message, sources=self.sources, blobs=self.blobs,
            media_store=self.store, metrics=Metrics(), comprehension=self.job,
        )
        await self.spawn.drain()
        return staged


async def test_a_shared_thumbnail_is_held_but_never_sent_to_the_interpreter():
    wiring = _Wiring()

    staged = await wiring.ingest(_shared_reel_event())

    # Custody happened: the bytes are held, the handle is in the body, and the
    # index has its row. That is what makes `media_fetch` possible later.
    assert len(staged.media_blobs) == 1
    blob = staged.media_blobs[0]
    assert wiring.blobs.blobs[blob.sha256] == PIXELS
    assert staged.body.endswith(f"[cover image {blob.handle}]")
    # Recorded as the cover, because `media_json` is all `media_fetch` has
    # when it serves this handle; `on_demand` does not survive into it.
    assert blob.role == "cover"
    assert '"role": "cover"' in (_row(staged).media_json or "")
    row = await wiring.store.get(sha256=blob.sha256)
    assert row is not None
    # And NO model looked at it.
    assert wiring.interpreter.calls == []
    assert not row.comprehended
    assert wiring.store.comprehended == []


async def test_an_unread_thumbnail_renders_a_bare_handle_that_claims_nothing():
    """What the model sees for the thumbnail until somebody asks: the kind, the
    author, the link, and `[cover image <handle>]` with nothing after the handle. The
    render join adds a description only for a row that has been read, so an
    on-demand thumbnail cannot be rendered as if it had been looked at.

    Positive control: the same join over a direct photo's row DOES add the
    reading, so the bare handle above is the row being unread and not the join
    never running."""
    wiring = _Wiring()
    shared = await wiring.ingest(_shared_reel_event())
    join = MediaJoin(
        store=wiring.store, screen=scan_identifiers, metrics=Metrics(),
        render_chars=240,
    )

    (rendered,) = await join.enrich([_row(shared)])

    handle = shared.media_blobs[0].handle
    assert rendered.body == (
        "[shared reel by @someaccount https://www.instagram.com/reel/SynthReel01/]"
        f" [cover image {handle}]"
    )

    direct = await wiring.ingest(_direct_photo_event())
    (read,) = await join.enrich([_row(direct)])
    assert read.body.startswith(f"[image {handle}: ")


async def test_a_photo_sent_directly_is_still_read_at_ingest():
    """The positive control. Without it, a stage that scheduled nothing for
    anybody would pass the test above."""
    wiring = _Wiring()

    staged = await wiring.ingest(_direct_photo_event())

    assert len(wiring.interpreter.calls) == 1
    row = await wiring.store.get(sha256=staged.media_blobs[0].sha256)
    assert row is not None and row.comprehended
    # And it is somebody's photo, not a cover: plain label, no role.
    assert staged.body == f"[image {staged.media_blobs[0].handle}]"
    assert staged.media_blobs[0].role == ""


async def test_the_same_bytes_sent_directly_after_a_share_are_read_then():
    """The dedupe is on whether the ROW has been read, not on whether the bytes
    were ever seen. A share that skipped the read must not make a later direct
    photo of the same bytes count as already read."""
    wiring = _Wiring()

    shared = await wiring.ingest(_shared_reel_event())
    assert wiring.interpreter.calls == []

    direct = await wiring.ingest(_direct_photo_event())

    assert direct.media_blobs[0].sha256 == shared.media_blobs[0].sha256
    assert len(wiring.interpreter.calls) == 1
    row = await wiring.store.get(sha256=direct.media_blobs[0].sha256)
    assert row is not None and row.comprehended


async def test_a_share_of_bytes_already_read_renders_the_existing_reading():
    """The other order: a photo read first, then shared. Nothing is read
    again, and the row keeps the reading it already had — the share neither
    costs a call nor erases one."""
    wiring = _Wiring()
    await wiring.ingest(_direct_photo_event())
    assert len(wiring.interpreter.calls) == 1

    shared = await wiring.ingest(_shared_reel_event())

    assert len(wiring.interpreter.calls) == 1
    row = await wiring.store.get(sha256=shared.media_blobs[0].sha256)
    assert isinstance(row, MediaObject) and row.comprehended
