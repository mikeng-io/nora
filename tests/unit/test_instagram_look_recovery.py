"""Instagram item look routing, custody and bounded recovery."""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from nora.domain.turn_deadline import look_seconds_left
from nora.infra.social import SocialRouter
from nora.infra.social.readings import VIDEO_ITEM_TIMEOUT_SECONDS
from nora.infra.social.render import POST_READ_FRAMING, POST_UNSEEN_KEPT_FRAMING
from nora.infra.vision import preprocess as _preprocess
from nora.infra.vision.client import VisionResult
from nora.testing import FakeMediaObjectStore, FakeSocialProvider
from tests.unit.test_instagram_read_readings import (
    _POST_LINK,
    ScriptedLook,
    _inside_the_wrap,
)
from tests.unit.test_instagram_read_readings import (
    bridge as bridge,
)
from tests.unit.test_instagram_read_readings import (
    world as world,
)


class _Recorder(FakeSocialProvider):
    """The shared fake, which also records whether it was handed a `look`,
    so «the look was not forwarded» is a fact about the call, not about a
    status. `_NOT_HANDED` is the argument's absence, which `None` is not."""

    _NOT_HANDED = object()

    def __init__(self, serves: frozenset[tuple[str, str]]) -> None:
        super().__init__(serves=serves)
        self.looks: list[object] = []

    async def read(self, *, platform, what, target, limit, look=_NOT_HANDED) -> dict:
        self.looks.append(look)
        return await super().read(platform=platform, what=what, target=target, limit=limit)


async def test_the_router_forwards_media_fetch_for_a_post_and_never_for_a_search():
    bridge = _Recorder(frozenset({("instagram", "post"), ("instagram", "search")}))
    default = _Recorder(frozenset({("x", "search")}))
    router = SocialRouter(
        default=default,
        exceptions={("instagram", "post"): bridge, ("instagram", "search"): bridge},
    )
    look = ScriptedLook()
    await router.read(platform="instagram", what="post", target="SynthPost01", limit=5, look=look)
    await router.read(platform="instagram", what="search", target="synth", limit=5, look=look)
    await router.read(platform="x", what="search", target="synth", limit=5, look=look)

    assert bridge.looks == [look, _Recorder._NOT_HANDED]
    assert default.looks == [_Recorder._NOT_HANDED]
    assert look.calls == []


async def test_two_batched_calls_share_one_turns_four_slots(world):
    """Two `instagram_read` calls in ONE turn (the model batches them) share
    the turn's four slots: six looks, never more than four at once. (A
    look that waits for a slot may then be a cache hit, so fewer than six
    reach the vision client; four at once is the positive control.)"""
    world.vision.delay = 0.3
    read = world.make()
    await asyncio.gather(
        read(link=_POST_LINK), read(link="https://www.instagram.com/p/SynthPost02/"),
    )
    assert len(world.vision.prompts) >= 4
    assert world.vision.peak == 4


async def test_a_story_frame_the_model_opens_itself_is_not_persisted_either(world):
    """The story result points her at `media_fetch(url=...)` on a frame. That
    call is the MODEL's own (`caller=model`), and it must not persist the
    frame either: this turn's story links are remembered and read without
    writing. The positive control is the same turn's `media_fetch` on a link
    that is not a story frame, which does write."""
    tools = world.tools()
    story = await tools["instagram_read"](person="Quinn", what="story")
    frame = story["items"][0]["url"]
    assert world.descriptions.remembered == []

    out = await tools["media_fetch"](url=frame)
    assert out["status"] == "ok"
    assert world.descriptions.remembered == []
    assert world.metrics.get("nora_media_fetch_total", caller="model") == 1

    await tools["media_fetch"](url="https://scontent.cdninstagram.com/v/t51/elsewhere.jpg")
    assert len(world.descriptions.remembered) == 1


async def test_shorter_windows_do_not_shrink_the_automatic_read_limit(world):
    world.reconfigure(video_chunk_seconds=10)
    out = await world.make()(link=_POST_LINK)
    body = _inside_the_wrap(out["text"])
    assert "not read here" not in body
    assert body.count("shows: ") == 3
    assert len(world.vision.seen) == 4


# ---- the reel incident, end to end ----------------------------------------------


async def _two_window_clip(args, timeout_seconds, data):
    """Return two generated frame windows for a 39.264-second clip."""
    if args[0] == "ffprobe":
        return 0, b"39.264", b""
    start = args[args.index("-ss") + 1].split(".")[0]
    return 0, f"window@{start}".encode(), b""


class ScriptedVision:
    """A vision that answers by the window's OWN bytes and counts asks per
    window: a window in `first_timeout` runs its first call out of time then
    answers, `always_timeout` windows never answer, `hang` windows wait to be
    cancelled, `down` makes every call time out."""

    def __init__(self, *, first_timeout=(), always_timeout=(), hang=(),
                 slow_timeout=()):
        self.first_timeout = frozenset(first_timeout)
        self.always_timeout = frozenset(always_timeout)
        self.hang = frozenset(hang)
        self.slow_timeout = frozenset(slow_timeout)
        self.down = False
        self.asks: Counter[bytes] = Counter()
        self.prompts: list[str] = []
        #: What the read saw of the look's own deadline at each call — the
        #: allowance `read_view` handed down, or None outside an item look.
        self.look_lefts: list[float | None] = []
        self.in_flight = 0
        self.peak = 0

    async def describe(self, *, data: bytes, media_type: str, prompt: str = "",
                       model: str = "", provider: str = "omniroute",
                       max_bytes: int | None = None,
                       windows: int | None = None) -> VisionResult:
        self.prompts.append(prompt)
        self.look_lefts.append(look_seconds_left())
        self.asks[data] += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if data in self.hang:
                await asyncio.Event().wait()
            if data in self.slow_timeout:
                await asyncio.sleep(0.2)
            if (
                self.down or data in self.always_timeout
                or data in self.slow_timeout
                or (data in self.first_timeout and self.asks[data] == 1)
            ):
                return VisionResult(available=False, reason="timeout")
            return VisionResult(available=True, description=f"saw {data.decode()}")
        finally:
            self.in_flight -= 1


def _video_ref(out: dict) -> dict:
    return next(item for item in out["items"] if item["kind"] == "video")


async def test_a_timed_out_opening_window_gets_its_recovery_on_the_real_path(
    world, monkeypatch,
):
    """The reel incident, through the REAL `instagram_read` → item envelope →
    `media_fetch` → render path: the opening window times out once, is owed
    its ONE recovery attempt inside the item's own allowance, and answers;
    the tail — which answered — is never re-asked. The item's shows line
    covers both windows and its reference carries the kept media's `code`,
    which a follow-up `media_fetch(code=...)` resolves from the HELD bytes:
    no second download, no expiring url to re-type."""
    monkeypatch.setattr(_preprocess, "_run_video", _two_window_clip)
    index = FakeMediaObjectStore()
    vision = ScriptedVision(first_timeout={b"window@0"})
    world.reconfigure(vision=vision, media_store=index)

    out = await world.make()(link=_POST_LINK)

    assert out["status"] == "ok"
    assert vision.asks[b"window@0"] == 2, (
        "the opening timed out once and was owed its one recovery attempt")
    assert vision.asks[b"window@30"] == 1, (
        "a window that answered is never re-asked")
    assert all(
        left is not None and 0 < left <= VIDEO_ITEM_TIMEOUT_SECONDS
        for left in vision.look_lefts
    ), "every window call ran under the item look's own deadline"
    body = _inside_the_wrap(out["text"])
    assert "saw window@0" in body and "saw window@30" in body
    assert "could not be read" not in body

    ref = _video_ref(out)
    assert ref["code"], "the held item hands back its stable code"
    video_url = ref["url"]
    assert world.downloads.urls.count(video_url) == 1

    again = await world.tools()["media_fetch"](code=ref["code"])
    assert again["status"] == "ok"
    assert "saw window@0" in again["description"]
    assert "saw window@30" in again["description"]
    assert "could not be read" not in again["description"]
    assert world.downloads.urls.count(video_url) == 1, (
        "the follow-up re-reads the HELD bytes — the expiring url was never "
        "downloaded again")

    # A question forces the LIVE re-read of the held bytes: two windows reach
    # a model with NO download — and a deliberate fetch is no item look, so
    # no deadline was handed to it and its read keeps its own budget.
    asked = dict(vision.asks)
    live = await world.tools()["media_fetch"](
        code=ref["code"], question="what is in the clip?")
    assert live["status"] == "ok"
    assert "saw window@0" in live["description"]
    assert "saw window@30" in live["description"]
    assert vision.asks[b"window@0"] == asked[b"window@0"] + 1
    assert vision.asks[b"window@30"] == asked[b"window@30"] + 1
    assert vision.look_lefts[-2:] == [None, None]
    assert world.downloads.urls.count(video_url) == 1


async def test_a_window_that_never_answers_stays_an_honest_gap(
    world, monkeypatch,
):
    """A window timed out on BOTH attempts: the recovery pass ran exactly
    once, the gap stays visible and timecoded, the window that DID answer is
    still described, and the kept item's code still rides the reference."""
    monkeypatch.setattr(_preprocess, "_run_video", _two_window_clip)
    vision = ScriptedVision(always_timeout={b"window@0"})
    world.reconfigure(vision=vision, media_store=FakeMediaObjectStore())

    out = await world.make()(link=_POST_LINK)

    assert vision.asks[b"window@0"] == 2, "one recovery attempt, bounded"
    assert vision.asks[b"window@30"] == 1
    body = _inside_the_wrap(out["text"])
    assert "0:00 — this part could not be read" in body
    assert "saw window@30" in body
    assert "could not watch" in body
    assert _video_ref(out)["code"]


async def test_every_look_failing_still_hands_the_held_items_code(
    world, monkeypatch,
):
    """All-window failure is honest AND recoverable: custody succeeded before
    the look failed, so every item ref carries the verified code — the
    all-unseen note says nothing was seen and offers `media_fetch(code=...)`
    on the held handle, never the url that just failed. The code then
    re-opens the item with NO download at all."""
    monkeypatch.setattr(_preprocess, "_run_video", _two_window_clip)
    index = FakeMediaObjectStore()
    vision = ScriptedVision()
    vision.down = True
    world.reconfigure(vision=vision, media_store=index)

    out = await world.make()(link=_POST_LINK)

    assert out["status"] == "ok"
    assert out["note"] == POST_UNSEEN_KEPT_FRAMING
    assert "media_fetch(code=...)" in out["note"]
    for ref in out["items"]:
        assert ref["code"], "custody survived the failed look"
    urls = list(world.downloads.urls)
    world.reconfigure(vision=ScriptedVision(), media_store=index)
    again = await world.tools()["media_fetch"](code=_video_ref(out)["code"])
    assert again["status"] == "ok", "the code resolves the held bytes"
    assert world.downloads.urls == urls, (
        "no url was fetched again — the follow-up never depends on the CDN")


async def test_a_look_whose_custody_failed_carries_no_bogus_code(world):
    """When the keep could not be confirmed, NO code is served — not even a
    hash of bytes that were here transiently. The items still read, the note
    stays the plain unseen-else/read one, and nothing in the result names a
    handle that might not resolve."""
    world.reconfigure(media_store=FakeMediaObjectStore(fail=True))

    out = await world.make()(link=_POST_LINK)

    assert out["status"] == "ok"
    assert out["note"] == POST_READ_FRAMING
    for ref in out["items"]:
        assert "code" not in ref, (
            "unconfirmed custody serves no handle — never a hash of bytes "
            "that were only here transiently")


async def test_cancelling_the_read_launches_no_recovery_wave(
    world, monkeypatch,
):
    """Cancelled means stopped: the opening answered `timeout` and its
    recovery pass was the next thing the read would issue — cancelling the
    outer call issues nothing further. The opening was asked exactly once."""
    monkeypatch.setattr(_preprocess, "_run_video", _two_window_clip)
    vision = ScriptedVision(
        first_timeout={b"window@0"}, hang={b"window@30"})
    world.reconfigure(vision=vision, media_store=FakeMediaObjectStore())

    call = asyncio.ensure_future(world.make()(link=_POST_LINK))
    for _ in range(500):
        if vision.asks[b"window@30"]:
            break
        await asyncio.sleep(0.01)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert vision.asks[b"window@0"] == 1, (
        "the recovery wave was never launched")
    assert vision.asks[b"window@30"] == 1


async def test_the_recovery_pass_fits_inside_the_looks_own_envelope(
    world, monkeypatch,
):
    """The shared allowance, proved by shrinking it: the video item's
    envelope is 0.5 s and each window's call takes 0.2 s to time out. The
    recovery pass is owed INSIDE that envelope — both passes complete at
    ~0.4 s — so the look RETURNS, honestly `failed`, carrying custody's
    verified code rather than being killed mid-call into a bare `timed_out`."""
    monkeypatch.setattr(_preprocess, "_run_video", _two_window_clip)
    monkeypatch.setattr(
        "nora.infra.social.readings.VIDEO_ITEM_TIMEOUT_SECONDS", 0.5)
    vision = ScriptedVision(
        slow_timeout={b"window@0", b"window@30"})
    world.reconfigure(vision=vision, media_store=FakeMediaObjectStore())

    out = await world.make()(link=_POST_LINK)

    assert out["status"] == "ok"
    assert vision.asks[b"window@0"] == 2, (
        "the recovery pass ran, bounded, inside the look's own allowance")
    assert vision.asks[b"window@30"] == 2
    ref = _video_ref(out)
    assert ref["code"], (
        "the look ended with a result, not the envelope's kill — custody's "
        "verified handle rode the failed reading")


async def test_exhausted_recovery_keeps_successful_tail_and_custody(world, monkeypatch):
    monkeypatch.setattr(_preprocess, "_run_video", _two_window_clip)
    monkeypatch.setattr("nora.infra.social.readings.VIDEO_ITEM_TIMEOUT_SECONDS", 0.3)
    vision = ScriptedVision(slow_timeout={b"window@0"})
    world.reconfigure(vision=vision, media_store=FakeMediaObjectStore())

    out = await world.make()(link=_POST_LINK)

    assert vision.asks[b"window@0"] == 2
    assert vision.asks[b"window@30"] == 1
    assert "saw window@30" in out["text"]
    assert "0:00-0:30" in out["text"]
    assert _video_ref(out)["code"]
