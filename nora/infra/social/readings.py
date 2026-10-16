"""What each item of a post or story SHOWS, read through `media_fetch` itself.

**The same path, not a copy.** The caller hands in its own `media_fetch`
(`TurnLook.fetch`), and each item is read as `media_fetch(url=item.url)`: the
same url egress screen, the same download ceilings, the same image vision call
and chunked video read, and the same content caches (the vision cache by
sha256, the `video_readings` cache) that a manual
`media_fetch(url=...)` goes through. So a second open of the same post costs
no vision call, and an item she then asks about by its link is a cache hit.
A STORY frame is the exception the caller makes: its `fetch` reads without
writing either cache, because a story expires in 24h.

**The caption never reaches a vision prompt.** Vision prompts are authored
(`nora/infra/vision/comprehend.py`): what a poster or a room wrote must not
steer a model whose words go back to the room. `media_fetch` has exactly one
way to put caller text into a prompt, its `question` (layer 2, a question
NORA composed), and it is never passed here: the url, and nothing else. With
no question `media_fetch` sends no prompt of its own and the vision client
uses its authored one. Nora crosses the caption with the readings herself,
with both in front of her. `tests/unit/test_instagram_read_readings.py` holds
it on the prompts a recording vision client receives.

**Bounded, and a failure is one item's.** At most `MAX_READ_ITEMS` items, in
the view's order, of which at most `MAX_READ_VIDEOS` videos, each no longer
than one reading window; at most `READ_CONCURRENCY` looks in flight across
the WHOLE TURN (`TurnLook.slots` is one semaphore per turn, so two batched
calls cannot run eight at once). Each look is held to `ITEM_TIMEOUT_SECONDS`
from the moment it gets a slot and the whole set to `TOTAL_TIMEOUT_SECONDS`,
which inside a turn is also held under the share of the turn's remaining time
the vision client already leaves (`seconds_left() x VISION_LOOP_SHARE`), so
this can never outlast the turn's own `wait_for`. An item whose look fails,
is withheld, or runs out of time says so on its own `Reading`; every other
item still returns.

**Cancelled means stopped.** If the read itself is cancelled (the turn's
`wait_for` fired), every look still running is cancelled AND awaited before
the cancellation propagates, so nothing keeps downloading, reading or writing
a cache after the tool has returned; `preprocess._run` and `_run_video` kill
ffmpeg and remove their scratch file on the same path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, TypeVar

from ...domain.turn_deadline import seconds_left
from ..vision.client import VISION_LOOP_SHARE
from ..vision.preprocess import VIDEO_CHUNK_SECONDS
from .views import ItemView, PostView, Reading, StoryView

log = logging.getLogger("nora.social.instagram.readings")

#: The rows whose items are read. A profile and a search read no media: the
#: router forwards a `TurnLook` for these two rows only, whatever it is
#: handed.
READS_WITH_READINGS: frozenset[tuple[str, str]] = frozenset({
    ("instagram", "story"),
    ("instagram", "post"),
})

#: The first this many items of one post or story are looked at, in order.
#: Instagram allows 20 in a carousel; eight at `READ_CONCURRENCY` is two
#: waves at most. The rest are listed with their links.
MAX_READ_ITEMS = 8

#: Of those, at most this many VIDEOS are read per open. A video window is
#: the expensive read (thousands of completion tokens, DEC-197); an image is
#: a single short call. Videos past it are listed as not read, saying
#: why.
MAX_READ_VIDEOS = 3

#: A video longer than one reading window is not read here: it would be
#: several windows read in sequence inside one open. Its link reads it
#: through `media_fetch`. The window is the CONFIGURED one
#: (`[media.describe.video] chunk_seconds`), which the caller hands in on
#: `TurnLook.max_video_seconds`; this is only its default.
#:
#: A POST video with no stated length is not read here either: nothing
#: bounds it. A STORY frame carries no length at all, and Instagram caps a
#: story video at 60 s, so a story frame is read.
MAX_READ_VIDEO_SECONDS = float(VIDEO_CHUNK_SECONDS)

#: Looks in flight at once, across the whole turn: `media_fetch`'s own
#: per-turn budget, and the number of media reads the executor already runs
#: together when the model batches them.
READ_CONCURRENCY = 4

#: One look: the single-window vision ceiling (120 s) plus the url download
#: ceiling (25 s) plus 5 s for ffmpeg.
ITEM_TIMEOUT_SECONDS = 150.0

#: Every look together. Also held under the turn's remaining share (module
#: docstring).
TOTAL_TIMEOUT_SECONDS = 180.0

V = TypeVar("V", bound=PostView | StoryView)


class MediaLook(Protocol):
    """`media_fetch`'s shape, as far as a reading uses it: one url in, its
    result dict out. `media_fetch` also takes a `question`; nothing here ever
    passes one (module docstring)."""

    def __call__(self, *, url: str) -> Awaitable[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class TurnLook:
    """What a read is handed to look with: the caller's `media_fetch` (or a
    wrapper of it that labels and, for a story, does not persist), and the
    TURN's slots, one semaphore shared by every read in the turn."""

    fetch: MediaLook
    slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(READ_CONCURRENCY),
    )
    max_video_seconds: float = MAX_READ_VIDEO_SECONDS


#: The coverage sentences a video reading can END with, and nothing else:
#: `VideoCut.note()` (three forms), `describe_coverage` (two more) and
#: `media_fetch`'s own fallback when ffmpeg could not cut the clip. Matched
#: by their fixed openings, so an ordinary last line in parentheses («(No
#: speech is audible.)») stays part of the description and is shortened with
#: it. `tests/unit/test_instagram_read_readings.py` builds every one of them
#: from the real code, so a reworded sentence fails there rather than
#: silently losing its protection.
_COVERAGE = re.compile(
    r"\((?:I could not tell how long this clip is"
    r"|About \d+ minutes of this \d+-minute clip"
    r"|This clip runs about \d+ minutes and I only watched)"
)

#: A coverage sentence is never shortened, so it is bounded here instead:
#: the longest real one is under 150 characters.
_COVERAGE_MAX_CHARS = 240


def split_coverage(text: str) -> tuple[str, str | None]:
    """A video reading → `(description, coverage sentence or None)`.

    The coverage sentence is the reading's LAST line, one of the known forms
    (`_COVERAGE`), after the timecoded segments. Called for videos only."""
    body, sep, last = text.rstrip().rpartition("\n")
    last = last.strip()
    if (
        sep and body.strip() and _COVERAGE.match(last) and last.endswith(")")
        and len(last) <= _COVERAGE_MAX_CHARS
    ):
        return body.rstrip(), last
    return text, None


def reading_of(served: Mapping[str, Any], *, video: bool = False) -> Reading:
    """`media_fetch`'s answer → a `Reading`. Only an `ok` with a description
    is `seen` (a video's coverage sentence split off); an `ok` that withheld
    its words (the identifier screen) is `withheld`; every other answer,
    whatever its status, is `failed`: it could not look, and that is all
    this item may say."""
    if served.get("status") == "ok":
        if served.get("withheld") is True:
            return Reading("withheld")
        text = served.get("description")
        if isinstance(text, str) and text.strip():
            if video:
                body, coverage = split_coverage(text)
                return Reading("seen", body, coverage)
            return Reading("seen", text)
    return Reading("failed")


def _not_read(
    chosen: tuple[ItemView, ...], *, max_seconds: float, story: bool,
) -> dict[int, Reading]:
    """The chosen items that are NOT looked at, by position: a video longer
    than one window, a post video of unknown length, and every video past
    the per-open cap."""
    skipped: dict[int, Reading] = {}
    videos = 0
    for n, item in enumerate(chosen):
        if item.kind != "video":
            continue
        if item.duration_seconds is None and not story:
            skipped[n] = Reading("unknown_length")
            continue
        if item.duration_seconds is not None and item.duration_seconds > max_seconds:
            skipped[n] = Reading("too_long")
            continue
        videos += 1
        if videos > MAX_READ_VIDEOS:
            skipped[n] = Reading("over_video_cap")
    return skipped


async def read_view(
    view: V,
    look: TurnLook,
    *,
    max_items: int = MAX_READ_ITEMS,
    item_timeout: float = ITEM_TIMEOUT_SECONDS,
    total_timeout: float = TOTAL_TIMEOUT_SECONDS,
) -> V:
    """`view` with a `Reading` on each of its first `max_items` items; the
    rest keep `reading=None` (not looked at). Never raises for one item's
    sake: a look that raises is `failed`, one that runs out of time is
    `timed_out`, a video past the cap or the length is marked so, and the
    others are unaffected. Cancelled, it stops every look before it goes."""
    chosen = view.items[:max_items]
    if not chosen:
        return view
    budget = total_timeout
    left = seconds_left()
    if left is not None:
        budget = min(budget, left * VISION_LOOP_SHARE)
    skipped = _not_read(
        chosen, max_seconds=look.max_video_seconds, story=isinstance(view, StoryView),
    )
    #: The looks that got a slot and began. One still waiting for a slot at
    #: the deadline never started, and says so rather than «took too long».
    started: set[int] = set()

    async def one(n: int, item: ItemView) -> Reading:
        async with look.slots:
            started.add(n)
            try:
                # The url and NOTHING else: no `question`, so no text a
                # poster wrote can reach the vision prompt.
                served = await asyncio.wait_for(look.fetch(url=item.url), timeout=item_timeout)
            except TimeoutError:
                return Reading("timed_out")
            except Exception:
                log.exception("instagram item reading failed")
                return Reading("failed")
            return reading_of(served, video=item.kind == "video")

    tasks = {
        n: asyncio.ensure_future(one(n, item))
        for n, item in enumerate(chosen) if n not in skipped
    }
    try:
        done, _pending = (
            await asyncio.wait(tasks.values(), timeout=max(0.0, budget))
            if tasks else (set(), set())
        )
    finally:
        # Deadline or cancellation alike: nothing is left running. A look
        # still going is cancelled and AWAITED, so its own cleanup (ffmpeg
        # killed, scratch file gone) has happened before this returns or
        # re-raises, and it can write no cache afterwards.
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
    # `one` answers every look it finished, failed or not, so a finished
    # task holds a `Reading`; one still running at the deadline is
    # `timed_out`, one still waiting for a slot `not_started`, and both were
    # cancelled above.
    readings = [
        skipped[n] if n in skipped
        else tasks[n].result() if tasks[n] in done
        else Reading("timed_out") if n in started
        else Reading("not_started")
        for n in range(len(chosen))
    ]
    items = tuple(
        replace(item, reading=reading)
        for item, reading in zip(chosen, readings, strict=True)
    )
    return replace(view, items=items + view.items[max_items:])
