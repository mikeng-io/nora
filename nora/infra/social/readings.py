"""What each item of a post or story SHOWS, read through `media_fetch` itself
(design/instagram-adoption/18 §"Typed views, one renderer, and readings").

**The same path, not a copy.** The caller hands in its own `media_fetch`
(`MediaLook`), and each item is read as `media_fetch(url=item.url)`: the
same url egress screen, the same download ceilings, the same image vision call
and chunked video read, and the same content caches (the vision cache by
sha256, the `video_readings` cache, #441) that a manual
`media_fetch(url=...)` goes through. So a second open of the same post costs
no vision call, and an item she then asks about by its link is a cache hit.

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
the view's order, `READ_CONCURRENCY` at a time; each look is held to
`ITEM_TIMEOUT_SECONDS` and the whole set to `TOTAL_TIMEOUT_SECONDS`, which
inside a turn is also held under the share of the turn's remaining time the
vision client already leaves (`seconds_left() x VISION_LOOP_SHARE`), so this
can never outlast the turn's own `wait_for`. An item whose look fails, is
withheld, or runs out of time says so on its own `Reading`; every other item
still returns. 18 gives the numbers their reasons.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Mapping
from dataclasses import replace
from typing import Any, Protocol, TypeVar

from ...domain.turn_deadline import seconds_left
from ..vision.client import VISION_LOOP_SHARE
from .views import ItemView, PostView, Reading, StoryView

log = logging.getLogger("nora.social.instagram.readings")

#: The rows whose items are read. A profile and a search read no media: the
#: router forwards a `MediaLook` for these two rows only, whatever it is
#: handed.
READS_WITH_READINGS: frozenset[tuple[str, str]] = frozenset({
    ("instagram", "story"),
    ("instagram", "post"),
})

#: The first this many items of one post or story are looked at, in order.
#: Instagram allows 20 in a carousel; eight at `READ_CONCURRENCY` is two
#: waves at most. The rest are listed with their links.
MAX_READ_ITEMS = 8

#: Looks in flight at once: `media_fetch`'s own per-turn budget, and the
#: number of media reads the executor already runs together when the model
#: batches them (design/tool-concurrency/01 §4).
READ_CONCURRENCY = 4

#: One look: the single-window vision ceiling (120 s) plus the url download
#: ceiling (25 s) plus 5 s for ffmpeg. A live reel read took 82 s uncached.
ITEM_TIMEOUT_SECONDS = 150.0

#: Every look together: two waves of that measured 82 s reel. Also held under
#: the turn's remaining share (module docstring).
TOTAL_TIMEOUT_SECONDS = 180.0

V = TypeVar("V", bound=PostView | StoryView)


class MediaLook(Protocol):
    """`media_fetch`'s shape, as far as a reading uses it: one url in, its
    result dict out. `media_fetch` also takes a `question`; nothing here ever
    passes one (module docstring)."""

    def __call__(self, *, url: str) -> Awaitable[Mapping[str, Any]]: ...


def reading_of(served: Mapping[str, Any]) -> Reading:
    """`media_fetch`'s answer → a `Reading`. Only an `ok` with a description
    is `seen`; an `ok` that withheld its words (the identifier screen) is
    `withheld`; every other answer, whatever its status, is `failed`: it
    could not look, and that is all this item may say."""
    if served.get("status") == "ok":
        if served.get("withheld") is True:
            return Reading("withheld")
        text = served.get("description")
        if isinstance(text, str) and text.strip():
            return Reading("seen", text)
    return Reading("failed")


async def read_view(
    view: V,
    look: MediaLook,
    *,
    max_items: int = MAX_READ_ITEMS,
    concurrency: int = READ_CONCURRENCY,
    item_timeout: float = ITEM_TIMEOUT_SECONDS,
    total_timeout: float = TOTAL_TIMEOUT_SECONDS,
) -> V:
    """`view` with a `Reading` on each of its first `max_items` items; the
    rest keep `reading=None` (not looked at). Never raises for one item's
    sake: a look that raises is `failed`, one that runs out of time is
    `timed_out`, and the others are unaffected."""
    chosen = view.items[:max_items]
    if not chosen:
        return view
    budget = total_timeout
    left = seconds_left()
    if left is not None:
        budget = min(budget, left * VISION_LOOP_SHARE)
    slots = asyncio.Semaphore(max(1, concurrency))

    async def one(item: ItemView) -> Reading:
        async with slots:
            try:
                # The url and NOTHING else: no `question`, so no text a
                # poster wrote can reach the vision prompt.
                served = await asyncio.wait_for(look(url=item.url), timeout=item_timeout)
            except TimeoutError:
                return Reading("timed_out")
            except Exception:
                log.exception("instagram item reading failed")
                return Reading("failed")
            return reading_of(served)

    tasks = [asyncio.ensure_future(one(item)) for item in chosen]
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, budget))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # `one` answers every look it finished, failed or not, so a finished
    # task holds a `Reading`; one still running at the deadline is
    # `timed_out`, and it was cancelled above.
    readings = [task.result() if task in done else Reading("timed_out") for task in tasks]
    items = tuple(
        replace(item, reading=reading)
        for item, reading in zip(chosen, readings, strict=True)
    )
    return replace(view, items=items + view.items[max_items:])

